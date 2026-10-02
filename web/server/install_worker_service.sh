#!/usr/bin/env bash
# 서버에서 워커를 상시 실행 (PC 를 꺼도 서버 혼자 영상 제작+업로드)
#   bash web/server/setup_worker_linux.sh   (도구 설치, 한 번)
#   bash web/server/install_worker_service.sh
set -e
KIT="$(cd "$(dirname "$0")/../.." && pwd)"
. "$KIT/web/server/server.env"
W="$KIT/web/worker/worker.env"
if [ ! -f "$W" ]; then
  cat > "$W" <<EOF
P2Y_SERVER=http://127.0.0.1:$P2Y_PORT
P2Y_WORKER_TOKEN=$P2Y_WORKER_TOKEN
EOF
  chmod 600 "$W"
fi
# latexmk: Fedora 기본 perl 에 Unicode::Normalize 가 없어 TinyTeX latexmk 가 안 돎 → 가벼운 대체 스크립트
install -m755 "$KIT/web/server/latexmk-shim.sh" "$HOME/.local/bin/latexmk"
mkdir -p ~/.config/systemd/user
sed "s#%DIR%#$KIT#g; s#%HOME%#$HOME#g" "$KIT/web/server/paper2yt-worker.service" > ~/.config/systemd/user/paper2yt-worker.service
systemctl --user daemon-reload
systemctl --user enable paper2yt-worker
systemctl --user restart paper2yt-worker
sleep 3; systemctl --user is-active paper2yt-worker
