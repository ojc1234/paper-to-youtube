# 웹 서비스 (이름 검색 + PDF → 유튜브 영상)

```
[브라우저] ⇄ [서버: web/server (FastAPI, 리눅스)] ⇄ (outbound polling) ⇄ [작업 PC: web/worker (Windows, Hermes + 크롬)]
```

- **이름 → 논문 → 영상**: 저자 이름이나 주제(한글이면 자동 영어 번역)로 arXiv 논문을 찾고, 고른 논문(또는 '바로 만들기'로 1순위)을 작업 PC 가 영상으로 만들어 유튜브에 올림. 이미 만든 논문은 영상 주소를 바로 보여 주고 중복 제작을 막음
- **PDF 업로드(선택)**: 작업 PC 의 워커가 가져가서 `hermes chat --format stream-json` 으로 슬라이드→나레이션→본편→쇼츠→업로드 실행
- **실시간 설명**: Hermes 의 도구 호출(stream-json)을 한국어 설명으로 바꿔 SSE 로 전송, 단계별 예상 시간으로 **남은 시간** 표시 (실제 소요 시간을 `stage_stats.json` 에 학습)
- 워커는 서버로 나가는 요청만 하므로 작업 PC 가 내부 IP/NAT 뒤에 있어도 됨

## 서버 배포 (리눅스)
```bash
git clone https://github.com/ojc1234/paper-to-youtube.git && cd paper-to-youtube
bash web/server/deploy.sh 8090      # venv, systemd(또는 nohup), 방화벽, 토큰 생성
```
마지막에 출력되는 `P2Y_WORKER_TOKEN` 을 작업 PC 에 넣는다.

## 작업 PC (영상 만드는 Windows)
`web/worker/worker.env.example` → `worker.env` 로 복사해 서버 주소/토큰 입력 후
```powershell
powershell -ExecutionPolicy Bypass -File web\worker\run_worker.ps1
```
필요: hermes, uv, ffmpeg, MiKTeX/TeX Live, edge-tts, BrowserSkill(bsk) + 유튜브 로그인된 크롬.
