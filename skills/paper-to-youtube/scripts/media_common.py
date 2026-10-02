"""썸네일·쇼츠 공용 도구: 한글 폰트 찾기, 줄바꿈, 외곽선 텍스트, ffmpeg/edge-tts 실행."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES_BOLD = [
    os.environ.get("KO_FONT_BOLD", ""),
    "C:/Windows/Fonts/malgunbd.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
]
FONT_CANDIDATES_REG = [
    os.environ.get("KO_FONT", ""),
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
]


def _ttc_index(path: str) -> int:
    # Noto CJK .ttc: 0=JP,1=KR,2=SC,3=TC,4=HK — 한국어 글리프 모양을 위해 KR 선택
    return 1 if "NotoSansCJK" in path else 0


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for p in (FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REG):
        if p and Path(p).is_file():
            return ImageFont.truetype(p, size, index=_ttc_index(p))
    raise FileNotFoundError("한글 폰트를 찾지 못했습니다. KO_FONT / KO_FONT_BOLD 환경변수로 .ttf 경로를 지정하세요.")


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    """공백 단위로 줄바꿈하고, 한 단어가 너무 길면 글자 단위로 자른다."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=fnt) <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            cur = ""
            for ch in word:
                if draw.textlength(cur + ch, font=fnt) > max_w and cur:
                    lines.append(cur)
                    cur = ""
                cur += ch
        lines.append(cur)
    return [l for l in lines if l]


def draw_text_block(draw, xy, lines, fnt, fill, stroke=0, stroke_fill="black",
                    line_gap=1.18, align="left", box_w=None):
    x, y = xy
    h = int(fnt.size * line_gap)
    for line in lines:
        lx = x
        if align == "center" and box_w:
            lx = x + (box_w - draw.textlength(line, font=fnt)) / 2
        draw.text((lx, y), line, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        y += h
    return y


def cover_fit(img: Image.Image, w: int, h: int) -> Image.Image:
    """비율 유지하며 w×h 를 꽉 채우도록 자르기."""
    s = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * s + 0.5), int(img.height * s + 0.5)), Image.LANCZOS)
    l, t = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((l, t, l + w, t + h))


def tool(name: str, env: str) -> str:
    p = os.environ.get(env) or shutil.which(name)
    if not p:
        raise FileNotFoundError(f"{name} 를 찾을 수 없습니다 (PATH 또는 {env} 환경변수 확인)")
    return p


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"명령 실패: {' '.join(map(str, cmd[:3]))} ...\n{r.stderr[-2000:]}")


def probe_duration(path: str) -> float:
    r = subprocess.run([tool("ffprobe", "FFPROBE"), "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def tts(text: str, out_mp3: str, voice: str | None = None, rate: str | None = None) -> None:
    if os.environ.get("TTS_MOCK") == "1":  # 오프라인 테스트용: 글자 수에 비례한 무음 오디오
        dur = max(1.0, len(text) / 7.0)
        run([tool("ffmpeg", "FFMPEG"), "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-t", f"{dur:.2f}", "-q:a", "9", out_mp3])
        return
    voice = voice or os.environ.get("EDGE_TTS_VOICE", "ko-KR-SunHiNeural")
    rate = rate or os.environ.get("EDGE_TTS_RATE", "+10%")
    last = None
    for _ in range(3):
        try:
            run([tool("edge-tts", "EDGE_TTS_BIN"), "--voice", voice, "--rate", rate,
                 "--text", text, "--write-media", out_mp3])
            if Path(out_mp3).stat().st_size > 500:
                return
        except Exception as e:  # 네트워크 일시 오류 재시도
            last = e
    raise RuntimeError(f"TTS 실패: {last}")
