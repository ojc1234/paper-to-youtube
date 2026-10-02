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
tlmgr install latexmk beamer fontspec xetex xunicode etoolbox pgf tcolorbox environ trimspaces \
  tikzfill pdfcol listings xcolor booktabs colortbl tabularx multirow makecell ulem \
  hyperref bookmark amsmath amsfonts tools graphics geometry caption float enumitem \
  translator unicode-math lm fancyhdr xpatch l3packages l3kernel iftex ifoddpage \
  adjustbox collectbox varwidth bm ifmtarg xstring fontawesome5 >/dev/null || true

log edge-tts / python tools
uv tool install -q edge-tts 2>/dev/null || uv tool upgrade edge-tts || true
uv tool install -q playwright 2>/dev/null || true
log Chromium
"$HOME/.local/share/uv/tools/playwright/bin/playwright" install chromium 2>&1 | tail -2 || uv run --with playwright playwright install chromium

log kit venvs
for p in paper-slides-to-video paper-to-beamer; do (cd "$KIT/skills/$p" && uv sync -q) || true; done

log check
for c in uv xelatex latexmk edge-tts ffmpeg ffprobe pdftoppm hermes; do printf '%-9s ' $c; command -v $c || echo MISSING; done
