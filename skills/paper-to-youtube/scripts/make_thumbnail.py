#!/usr/bin/env python3
"""유튜브 썸네일(1280×720, 2MB 이하 JPG) 생성.

python make_thumbnail.py --image figure.png --title "오류 정정 없이\n큐비트 100배 안정화" \
    --badge "양자컴퓨팅 논문 리뷰" --sub "arXiv 2610.01234" --out thumbnail.jpg

--image 는 논문 핵심 그림(권장) 또는 슬라이드 PNG. 오른쪽 58% 영역에 배치되고
왼쪽에는 큰 제목(2~3줄, 줄당 8~10자 권장)이 들어간다.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

from media_common import font, wrap, draw_text_block, cover_fit

W, H = 1280, 720
ACCENT = (255, 214, 0)       # 노란 포인트
BG = (12, 18, 38)            # 남색 배경


def build(image: str, title: str, badge: str, sub: str, out: str) -> str:
    canvas = Image.new("RGB", (W, H), BG)
    src = Image.open(image).convert("RGB")

    # 배경: 이미지를 흐리고 어둡게 깔기
    bg = cover_fit(src, W, H).filter(ImageFilter.GaussianBlur(18))
    bg = ImageEnhance.Brightness(bg).enhance(0.35)
    canvas.paste(bg, (0, 0))

    # 왼쪽 텍스트 영역에 그라데이션 어둡게
    grad = Image.new("L", (W, 1))
    for x in range(W):
        grad.putpixel((x, 0), int(230 * max(0.0, 1 - x / (W * 0.62))))
    shade = Image.new("RGB", (W, H), (0, 0, 0))
    canvas.paste(shade, (0, 0), grad.resize((W, H)))

    # 오른쪽: 원본 이미지 카드
    card_w, card_h = int(W * 0.56), int(H * 0.78)
    s = min(card_w / src.width, card_h / src.height)
    fig = src.resize((max(1, int(src.width * s)), max(1, int(src.height * s))), Image.LANCZOS)
    fx = W - fig.width - 36
    fy = (H - fig.height) // 2
    shadow = Image.new("RGBA", (fig.width + 24, fig.height + 24), (0, 0, 0, 160)).filter(ImageFilter.GaussianBlur(10))
    canvas.paste(shadow, (fx - 6, fy + 6), shadow)
    canvas.paste(fig, (fx, fy))
    d = ImageDraw.Draw(canvas)
    d.rectangle([fx - 4, fy - 4, fx + fig.width + 4, fy + fig.height + 4], outline=ACCENT, width=6)


    left_w = fx - 70
    y = 52
    if badge:
        bf = font(34)
        bw = int(d.textlength(badge, font=bf)) + 36
        d.rounded_rectangle([44, y, 44 + bw, y + 58], radius=12, fill=ACCENT)
        d.text((62, y + 8), badge, font=bf, fill=(20, 20, 20))
        y += 92

    # 제목: 영역에 맞을 때까지 글자 크기 줄이기
    for size in range(112, 54, -4):
        tf = font(size)
        lines = wrap(d, title, tf, left_w)
        if len(lines) <= 3 and len(lines) * size * 1.18 <= H - y - 110:
            break
    for i, line in enumerate(lines):
        color = ACCENT if i == len(lines) - 1 and len(lines) > 1 else (255, 255, 255)
        draw_text_block(d, (44, y), [line], tf, color, stroke=7, stroke_fill=(0, 0, 0))
        y += int(tf.size * 1.18)

    if sub:
        sf = font(30, bold=False)
        d.text((46, H - 70), sub, font=sf, fill=(210, 220, 235), stroke_width=3, stroke_fill=(0, 0, 0))

    outp = Path(out)
    q = 92
    while True:
        canvas.save(outp, "JPEG", quality=q, optimize=True)
        if outp.stat().st_size < 2_000_000 or q < 60:
            break
        q -= 8
    return str(outp.resolve())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--title", required=True, help="\\n 으로 줄바꿈 지정 가능")
    ap.add_argument("--badge", default="양자컴퓨팅 논문 리뷰")
    ap.add_argument("--sub", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print("THUMBNAIL:", build(a.image, a.title.replace("\\n", "\n"), a.badge, a.sub, a.out))


if __name__ == "__main__":
    main()
