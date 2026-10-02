#!/usr/bin/env bash
# 서버(리눅스)에서 영상 제작까지 하도록 필요한 도구를 sudo 없이 사용자 공간에 설치
#   bash web/server/setup_worker_linux.sh
set -e
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"
KIT="$(cd "$(dirname "$0")/../.." && pwd)"
log(){ echo "=== $*"; }

command -v uv >/dev/null || { log uv; curl -LsSf https://astral.sh/uv/install.sh | sh; }
export PATH="$HOME/.local/bin:$PATH"

if ! command -v xelatex >/dev/null && [ ! -x "$HOME/.TinyTeX/bin/x86_64-linux/xelatex" ]; then
  log TinyTeX; curl -sL "https://yihui.org/tinytex/install-bin-unix.sh" | sh
fi
TT="$HOME/.TinyTeX/bin/x86_64-linux"; export PATH="$TT:$PATH"
mkdir -p "$HOME/.local/bin"; for b in xelatex latexmk tlmgr kpsewhich; do [ -e "$TT/$b" ] && ln -sf "$TT/$b" "$HOME/.local/bin/$b"; done
log TeX packages
for pkg in latexmk beamer fontspec xetex etoolbox pgf tcolorbox environ trimspaces tikzfill pdfcol listings \
  xcolor booktabs colortbl multirow makecell ulem hyperref bookmark amsmath amsfonts tools graphics geometry \
  caption float enumitem translator unicode-math lm xpatch l3packages iftex adjustbox collectbox varwidth xstring fontawesome5; do
  tlmgr install $pkg >/dev/null 2>&1 || true   # 하나씩: 없는 이름이 있어도 나머지는 설치
done
# Fedora 최소 perl 에는 Unicode::Normalize 가 없어 TinyTeX latexmk 가 죽는다 → 대체 스크립트
install -m755 "$KIT/web/server/latexmk-shim.sh" "$HOME/.local/bin/latexmk"

log Korean font (NanumGothic: Noto CJK TTC 는 xdvipdfmx 에서 Invalid TTC index)
mkdir -p ~/.local/share/fonts
for f in NanumGothic-Regular NanumGothic-Bold NanumGothic-ExtraBold; do
  [ -f ~/.local/share/fonts/$f.ttf ] || curl -sfL -o ~/.local/share/fonts/$f.ttf https://github.com/google/fonts/raw/main/ofl/nanumgothic/$f.ttf
done
fc-cache -f >/dev/null 2>&1 || true

log ffmpeg static (Fedora ffmpeg 에는 libx264 가 없음)
if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264; then
  mkdir -p ~/.local/opt && cd ~/.local/opt && curl -sfL -o ff.tar.xz https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    && tar -xf ff.tar.xz && rm ff.tar.xz && D=$(ls -d ffmpeg-*-static | head -1) \
    && ln -sf ~/.local/opt/$D/ffmpeg ~/.local/bin/ffmpeg && ln -sf ~/.local/opt/$D/ffprobe ~/.local/bin/ffprobe; cd - >/dev/null
fi

log edge-tts / python tools
uv tool install -q edge-tts 2>/dev/null || uv tool upgrade edge-tts || true
uv tool install -q playwright 2>/dev/null || true
log Chromium
"$HOME/.local/share/uv/tools/playwright/bin/playwright" install chromium 2>&1 | tail -2 || uv run --with playwright playwright install chromium

log kit venvs
for p in paper-slides-to-video paper-to-beamer; do (cd "$KIT/skills/$p" && uv sync -q) || true; done

log check
echo "유튜브 로그인: 크롬에서 받은 쿠키 JSON 을 넣고  ~/.local/share/uv/tools/playwright/bin/python $KIT/skills/paper-youtube-browser-upload/scripts/pw_upload.py --import-cookies cookies.json"
for c in uv xelatex latexmk edge-tts ffmpeg ffprobe pdftoppm hermes; do printf '%-9s ' $c; command -v $c || echo MISSING; done
