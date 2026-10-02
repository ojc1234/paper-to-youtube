#!/usr/bin/env bash
# 서버(리눅스)에서 실행:  bash web/server/deploy.sh [포트=8090]
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"; cd "$DIR"
PORT="${1:-8090}"
if [ ! -f server.env ]; then
  echo "P2Y_PORT=$PORT" > server.env
  echo "P2Y_WORKER_TOKEN=$(head -c 20 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> server.env
fi
PY=$(command -v python3.12 || command -v python3.11 || command -v python3)
[ -d .venv ] || $PY -m venv .venv || { command -v uv && uv venv .venv; }
.venv/bin/pip install -q -r requirements.txt 2>/dev/null || uv pip install -p .venv/bin/python -r requirements.txt
mkdir -p data; [ -f data/videos.json ] || cp seed_videos.json data/videos.json
sed "s#%DIR%#$DIR#g; s#%USER%#$USER#g" paper2yt.service > /tmp/paper2yt.service
if sudo -n true 2>/dev/null; then
  sudo cp /tmp/paper2yt.service /etc/systemd/system/paper2yt.service
  sudo systemctl daemon-reload && sudo systemctl enable --now paper2yt && sudo systemctl restart paper2yt
  command -v firewall-cmd >/dev/null && sudo firewall-cmd --add-port=$PORT/tcp --permanent && sudo firewall-cmd --reload || true
  command -v ufw >/dev/null && sudo ufw allow $PORT/tcp || true
else
  echo "sudo 없음 → nohup 으로 실행"; pkill -f "uvicorn app:app" || true
  set -a; . ./server.env; set +a
  nohup .venv/bin/uvicorn app:app --host 0.0.0.0 --port $P2Y_PORT > data/server.log 2>&1 &
fi
sleep 2; curl -s localhost:$PORT/api/status && echo && echo "OK → http://$(hostname -I | awk '{print $1}'):$PORT"
grep TOKEN server.env
