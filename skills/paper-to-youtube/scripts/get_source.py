#!/usr/bin/env python3
"""논문 원본을 순서대로 시도해서 받는다 (PC 자동화용).

  0) arxiv-downloader (uv 로 자동 설치) → PDF + TeX 원본(tar.gz) 을 한 번에 받아 paper_src/ 에 풀기
  1) arXiv TeX 원본 (e-print)        → <dir>/paper_src/*.tex      ← 가장 좋음 (그림 포함)
  2) arXiv PDF                       → <dir>/<id>.pdf
  3) arXiv HTML  (arxiv.org/html)    → <dir>/paper.html
  4) ar5iv HTML  (ar5iv.labs.arxiv.org) → <dir>/paper.html       (최신 논문은 아직 없을 수 있음)

arXiv 이용 수칙에 맞게 요청 사이 3초 이상 쉬고, 429/503 이면 기다렸다 재시도한다.
출력 마지막 줄:  SOURCE: tex|pdf|html <경로>    (전부 실패하면 종료코드 2)

python get_source.py 2610.00504 --output "<paper_dir>"
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "paper-to-youtube/1.0 (personal research channel; polite 3s rate limit)"
HERE = Path(__file__).resolve().parent
DOWNLOADER = HERE.parent.parent / "paper-download-arxiv-paper-source" / "scripts" / "download_source.py"


def get(url: str, tries: int = 3) -> bytes:
    for i in range(tries):
        time.sleep(3.5)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and i < tries - 1:
                wait = int(e.headers.get("Retry-After", "30") or 30)
                print(f"  {e.code} → {wait}s 대기 후 재시도", file=sys.stderr)
                time.sleep(min(wait, 120))
                continue
            raise
    raise RuntimeError("unreachable")


# ── 0단계: arxiv-downloader (https://github.com/braun-steven/arxiv-downloader) ──
# 실행 파일이 확장자 없는 스크립트라 Windows 에서 바로 실행되지 않으므로,
# uv 로 전용 가상환경을 만들어 설치하고 그 환경의 python 으로 스크립트를 실행한다.
DL_VENV = Path(os.environ.get("PAPER_YT_HOME", Path.home() / ".paper-to-youtube")) / "arxiv-dl-venv"


def _venv_paths(venv: Path) -> tuple[Path, Path]:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe", venv / "Scripts" / "arxiv-downloader"
    return venv / "bin" / "python", venv / "bin" / "arxiv-downloader"


def ensure_arxiv_downloader() -> tuple[Path, Path] | None:
    py, script = _venv_paths(DL_VENV)
    if py.is_file() and script.is_file():
        return py, script
    uv = shutil.which("uv")
    if not uv:
        print("  uv 가 없어 arxiv-downloader 단계를 건너뜀 (설치: winget install astral-sh.uv)", file=sys.stderr)
        return None
    try:
        if not py.is_file():
            subprocess.run([uv, "venv", "--python", "3.12", str(DL_VENV)], check=True,
                           capture_output=True, text=True)
        subprocess.run([uv, "pip", "install", "--python", str(py), "arxiv-downloader"], check=True,
                       capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"  arxiv-downloader 설치 실패: {(e.stderr or '')[-300:]}", file=sys.stderr)
        return None
    return (py, script) if script.is_file() else None


def _load_extractor():
    spec = importlib.util.spec_from_file_location("download_source", DOWNLOADER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def unpack_source(src_file: Path, out: Path) -> bool:
    """arxiv-downloader 가 받은 원본 파일(tar.gz / gz / tex)을 paper_src/ 에 푼다."""
    ds = _load_extractor()
    kind, payload = ds.detect(src_file.read_bytes())
    paper_src = out / "paper_src"
    if kind == "archive":
        ds.extract(src_file, paper_src)
    elif kind == "tex":
        paper_src.mkdir(parents=True, exist_ok=True)
        (paper_src / "main.tex").write_bytes(payload)
    else:
        return False
    return any(paper_src.rglob("*.tex"))


def via_arxiv_downloader(aid: str, out: Path) -> str | None:
    tool = ensure_arxiv_downloader()
    if not tool:
        return None
    py, script = tool
    dl_dir = out / "_download"
    dl_dir.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([str(py), str(script), aid, "-d", str(dl_dir), "-s"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        print("  arxiv-downloader 시간 초과(10분)", file=sys.stderr)
        return None
    if r.returncode != 0:
        print(f"  arxiv-downloader 실패: {(r.stderr or r.stdout)[-300:]}", file=sys.stderr)
    pdfs = sorted(dl_dir.glob("*.pdf"))
    others = sorted(p for p in dl_dir.iterdir() if p.is_file() and p.suffix != ".pdf")
    if pdfs:
        shutil.copy2(pdfs[0], out / f"{aid}.pdf")
    for src in others:
        try:
            if unpack_source(src, out):
                return f"tex {out / 'paper_src'}"
        except SystemExit:
            print(f"  원본 압축 해제 실패: {src.name}", file=sys.stderr)
    if pdfs:
        return f"pdf {out / f'{aid}.pdf'}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arxiv_id")
    ap.add_argument("--output", required=True)
    ap.add_argument("--no-ar5iv", action="store_true")
    a = ap.parse_args()
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    aid = a.arxiv_id

    # 0) arxiv-downloader
    if os.environ.get("USE_ARXIV_DOWNLOADER", "1") == "1":
        res = via_arxiv_downloader(aid, out)
        if res and res.startswith("tex"):
            print(f"SOURCE: {res}")
            return 0
        pdf_fallback = res  # PDF 만 받았으면 TeX 원본 단계를 한 번 더 시도한 뒤 이걸 쓴다
    else:
        pdf_fallback = None

    # 1) TeX 원본
    if DOWNLOADER.is_file():
        r = subprocess.run([sys.executable, str(DOWNLOADER), aid, "--output", str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        if r.returncode == 0 and "TEX_SOURCES" in r.stdout:
            print(f"SOURCE: tex {out / 'paper_src'}")
            return 0
        print(f"  TeX 원본 실패/없음: {tail or r.stderr.strip()[-200:]}", file=sys.stderr)

    if pdf_fallback:
        print(f"SOURCE: {pdf_fallback}")
        return 0

    # 2) PDF
    try:
        data = get(f"https://arxiv.org/pdf/{aid}")
        if data[:4] == b"%PDF":
            p = out / f"{aid}.pdf"
            p.write_bytes(data)
            print(f"SOURCE: pdf {p}")
            return 0
    except Exception as e:
        print(f"  PDF 실패: {e}", file=sys.stderr)

    # 3) arXiv HTML, 4) ar5iv
    urls = [f"https://arxiv.org/html/{aid}"]
    if not a.no_ar5iv:
        urls.append(f"https://ar5iv.labs.arxiv.org/html/{aid}")
    for u in urls:
        try:
            html = get(u)
            if len(html) > 20000:
                p = out / "paper.html"
                p.write_bytes(html)
                print(f"SOURCE: html {p}  ({u})")
                return 0
        except Exception as e:
            print(f"  HTML 실패 {u}: {e}", file=sys.stderr)

    print("SOURCE: none", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
