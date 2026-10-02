#!/usr/bin/env bash
# 서버(리눅스)에서 실행:  bash web/server/deploy.sh [포트=8090]
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"; cd "$DIR"
PORT="${1:-8090}"
if [ ! -f server.env ]; then
  echo "P2Y_PORT=$PORT" > server.env
  echo "P2Y_WORKER_TOKEN=$(head -c 20 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> server.env
fi
chmod 600 server.env; . ./server.env
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
mkdir -p data; [ -f data/videos.json ] || cp seed_videos.json data/videos.json
pkill -f "$DIR/.venv/bin/uvicorn" 2>/dev/null || pkill -f "uvicorn app:app" 2>/dev/null || true
if sudo -n true 2>/dev/null; then
  sed "s#%DIR%#$DIR#g; s#%USER%#$USER#g" paper2yt.service | sudo tee /etc/systemd/system/paper2yt.service >/dev/null
  sudo systemctl daemon-reload && sudo systemctl enable paper2yt && sudo systemctl restart paper2yt
  command -v firewall-cmd >/dev/null && sudo firewall-cmd --add-port=$PORT/tcp --permanent && sudo firewall-cmd --reload || true
else
  # sudo 없음 → systemd --user (재부팅 자동 시작엔 loginctl enable-linger 필요)
  mkdir -p ~/.config/systemd/user
  sed "s#%DIR%#$DIR#g; /^User=/d; s#multi-user.target#default.target#" paper2yt.service > ~/.config/systemd/user/paper2yt.service
  systemctl --user daemon-reload && systemctl --user enable paper2yt && systemctl --user restart paper2yt
fi
for i in $(seq 1 15); do curl -sf "localhost:$P2Y_PORT/api/status" && break; sleep 1; done
echo; echo "OK → http://$(hostname -I | awk '{print $1}'):$P2Y_PORT"
echo "워커용 토큰: $P2Y_WORKER_TOKEN"
