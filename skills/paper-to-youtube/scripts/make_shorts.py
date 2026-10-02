#!/usr/bin/env python3
"""쇼츠(세로 1080×1920) 영상 생성.

입력: shorts_script.json
{
  "title": "큐비트 오류를\\n100배 줄인 방법",          # 상단 고정 제목 (2줄 권장)
  "segments": [
    {"image": "video/video_frames/slide_002.png", "text": "첫 문장. 두 번째 문장."},
    {"image": "slides-beamer/figures/fig1.png",  "text": "..."}
  ],
  "cta": "자세한 설명은 본편 영상에서 확인하세요."      # 마지막 안내 (선택)
}
경로는 shorts_script.json 기준 상대경로 또는 절대경로.
문장마다 TTS 를 따로 만들어 자막과 음성이 정확히 맞도록 한다.

python make_shorts.py <paper_dir>/video/shorts_script.json --out <paper_dir>/video/shorts.mp4
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from media_common import font, wrap, draw_text_block, tool, run, probe_duration, tts

W, H = 1080, 1920
BG_TOP, BG_BOT = (10, 16, 36), (28, 18, 60)
ACCENT = (255, 214, 0)
PAD_SEC = 0.25
MAX_SEC = 59.0


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。])\s+", text.strip())
    out = [p.strip() for p in parts if p and p.strip()]
    return out or [text.strip()]


def gradient() -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(BG_TOP[i] * (1 - t) + BG_BOT[i] * t) for i in range(3)))
    return img


def frame(title: str, image: Path | None, caption: str, progress: float) -> Image.Image:
    img = gradient()
    d = ImageDraw.Draw(img)
    # 상단 제목
    tf = font(76)
    lines = wrap(d, title, tf, W - 120)[:3]
    y = draw_text_block(d, (60, 150), lines, tf, (255, 255, 255), stroke=5,
                        align="center", box_w=W - 120)
    d.rectangle([W // 2 - 70, y + 14, W // 2 + 70, y + 22], fill=ACCENT)
    # 가운데 이미지
    top = max(y + 70, 520)
    if image is not None:
        src = Image.open(image).convert("RGB")
        box_w, box_h = W - 60, 760
        s = min(box_w / src.width, box_h / src.height)
        src = src.resize((int(src.width * s), int(src.height * s)), Image.LANCZOS)
        x = (W - src.width) // 2
        d.rounded_rectangle([x - 8, top - 8, x + src.width + 8, top + src.height + 8], radius=18, fill=(255, 255, 255))
        img.paste(src, (x, top))
        top += src.height
    # 하단 자막
    cf = font(58)
    clines = wrap(d, caption, cf, W - 140)[:4]
    ch = int(len(clines) * cf.size * 1.25) + 50
    cy = max(top + 70, 1420 - ch // 2)
    cy = min(cy, H - ch - 140)
    d.rounded_rectangle([50, cy, W - 50, cy + ch], radius=24, fill=(0, 0, 0))
    draw_text_block(d, (70, cy + 25), clines, cf, (255, 255, 255), line_gap=1.25,
                    align="center", box_w=W - 140)
    # 진행 바
    d.rectangle([0, H - 14, int(W * progress), H], fill=ACCENT)
    return img


def encode(png: Path, mp3: Path, out: Path) -> float:
    dur = probe_duration(str(mp3)) + PAD_SEC
    run([tool("ffmpeg", "FFMPEG"), "-y", "-loop", "1", "-framerate", "30", "-i", str(png),
         "-i", str(mp3), "-t", f"{dur:.3f}", "-af", f"apad=pad_dur={PAD_SEC}",
         "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
         "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", str(out)])
    return dur


def build(script_path: Path, out: Path) -> str:
    spec = json.loads(script_path.read_text(encoding="utf-8"))
    base = script_path.parent
    title = spec["title"].replace("\\n", "\n")
    items: list[tuple[Path | None, str]] = []
    for seg in spec["segments"]:
        img = Path(seg["image"]) if seg.get("image") else None
        if img is not None and not img.is_absolute():
            img = (base / img).resolve()
        if img is not None and not img.is_file():
            raise FileNotFoundError(f"이미지 없음: {img}")
        for s in sentences(seg["text"]):
            items.append((img, s))
    if spec.get("cta"):
        items.append((items[-1][0] if items else None, spec["cta"]))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        mp3s = []
        for i, (_, text) in enumerate(items):
            mp3 = td / f"s{i:03d}.mp3"
            tts(text, str(mp3))
            mp3s.append(mp3)
        total = sum(probe_duration(str(m)) + PAD_SEC for m in mp3s)
        if total > MAX_SEC:
            print(f"[WARN] 쇼츠 길이 {total:.1f}s > {MAX_SEC}s. 대본을 줄이는 것을 권장 (최대 3분까지는 쇼츠로 인정)", file=sys.stderr)
        clips, elapsed = [], 0.0
        for i, ((img, text), mp3) in enumerate(zip(items, mp3s)):
            elapsed += probe_duration(str(mp3)) + PAD_SEC
            png = td / f"f{i:03d}.png"
            frame(title, img, text, elapsed / total).save(png)
            clip = td / f"c{i:03d}.mp4"
            encode(png, mp3, clip)
            clips.append(clip)
        lst = td / "list.txt"
        lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        run([tool("ffmpeg", "FFMPEG"), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", "-movflags", "+faststart", str(out)])
    print(f"SHORTS: {out.resolve()} ({total:.1f}s)")
    return str(out.resolve())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(Path(a.script).resolve(), Path(a.out))


if __name__ == "__main__":
    main()
