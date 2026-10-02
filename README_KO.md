# paper-to-youtube 키트 (논문 → 한국어 유튜브 영상 자동화)

원본: [yhbcode000/paper-share-skills](https://github.com/yhbcode000/paper-share-skills) (Apache-2.0)
+ 유튜브·한국어용 스킬 `paper-to-youtube` 추가

## 매일 일어나는 일
arXiv 새 논문(기본 양자컴퓨팅 quant-ph) 확인 → 이미 만든 논문·비슷한 논문 제외 → arxiv-downloader 로 PDF+TeX 원본 다운로드
(실패 시 e-print 직접 → PDF → arXiv HTML → ar5iv 순서) → 한국어 슬라이드 → 한국어 나레이션 본편 → 썸네일 → 쇼츠 → YouTube 업로드

## 설치 (한 번만)
1. 필요한 프로그램: Python 3.12, uv, Git, TeX Live, FFmpeg, Poppler, Claude Code
   ```powershell
   winget install Python.Python.3.12 Git.Git Gyan.FFmpeg astral-sh.uv
   # TeX Live: https://tug.org/texlive/windows.html  (용량 크지만 latexmk 포함되어 가장 편함)
   # Poppler:  scoop install poppler
   ```
2. 압축을 풀고 PowerShell 에서:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1
   ```
   → `%USERPROFILE%\.claude\skills\` 에 스킬 9개가 설치됩니다.
3. `config.ps1` 에서 작업 폴더, 분야, 음성, 채널 이름을 원하는 대로 수정.

## 테스트 (업로드 없이)
```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily.ps1
```
또는 Claude Code 를 열고 `/paper-to-youtube --no-upload` 라고 입력.
결과: `%USERPROFILE%\PaperYouTube\논문영상\<논문>\video\` 에 본편 mp4, shorts.mp4, thumbnail.jpg

## 유튜브 업로드 켜기
1. Google Cloud Console → 새 프로젝트 → "YouTube Data API v3" 사용 설정
2. OAuth 동의 화면 구성(외부, 테스트 사용자에 내 계정 추가) → 사용자 인증 정보 → OAuth 클라이언트 ID → "데스크톱 앱"
3. JSON 을 받아 `%USERPROFILE%\.paper-to-youtube\client_secret.json` 으로 저장
4. 첫 업로드 때 브라우저가 열리면 로그인·허용 (이후 자동)
5. `config.ps1` 에서 `DO_UPLOAD = "1"`
   - Google 심사(audit)를 받기 전 API 프로젝트로 올린 영상은 **비공개로 잠길 수 있습니다.** 처음엔 private 로 올리고 확인 후 공개 전환을 권장.
   - 썸네일 업로드는 채널 전화번호 인증이 필요합니다.

## 매일 자동 실행
```powershell
powershell -ExecutionPolicy Bypass -File .\register_task.ps1 -Time "06:50"
```
PC 가 켜져 있거나 절전 상태(깨우기 허용)여야 실행됩니다. 로그: `PaperYouTube\logs\`

## 사람이 해야 하는 일 (API 미지원)
- 쇼츠의 **관련 동영상**에 본편 연결: YouTube Studio → 쇼츠 → 세부정보 → 관련 동영상
- 쇼츠 댓글 고정 (본편 링크 댓글은 자동 작성됨)
- 공개 전환 전 영상 내용 검수 (AI 가 만든 요약이라 틀린 부분이 있을 수 있음)

## 참고
- 저작권: 논문 그림 사용 범위는 논문 라이선스(arXiv abs 페이지)를 확인하세요. 스킬이 라이선스를 기록하고 설명란에 출처를 넣습니다.
- 원본 저장소를 사칭한 `Peytontalismanic424/paper-share-skills` (exe 배포) 는 사용하지 마세요.

- 논문 다운로드는 [arxiv-downloader](https://github.com/braun-steven/arxiv-downloader) (MIT) 를 uv 가상환경(`%USERPROFILE%\.paper-to-youtube\arxiv-dl-venv`)에 설치해 사용합니다. 끄려면 환경변수 `USE_ARXIV_DOWNLOADER=0`.
