# ===== paper-to-youtube 설정 (이 파일만 고치면 됩니다) =====

# 작업 폴더: 논문별 슬라이드/영상, history.json 이 여기에 쌓입니다
$env:PP_ROOT          = "$env:USERPROFILE\PaperYouTube"

# 논문 분야와 우선 키워드 (쉼표 구분, 비워도 됨)
$env:PAPER_CATEGORY   = "quant-ph"
$env:PAPER_KEYWORDS   = "error correction,logical qubit,quantum algorithm,quantum advantage"
$env:PAPERS_PER_DAY   = "1"

# 한국어 음성 (여성 SunHi / 남성 InJoon)
$env:EDGE_TTS_VOICE   = "ko-KR-SunHiNeural"
$env:EDGE_TTS_RATE    = "+0%"
$env:RENDER_DPI       = "305"

# 채널 이름 (슬라이드 표지에 표시)
$env:PRESENTER        = "양자컴퓨팅 논문 리뷰"

# YouTube: OAuth 클라이언트 파일 위치, 업로드 공개 상태 (private / unlisted / public)
$env:YT_CLIENT_SECRET = "$env:USERPROFILE\.paper-to-youtube\client_secret.json"
$env:YT_PRIVACY       = "private"

# (선택) 유튜브 기존 영상과 중복 비교용 API 키
$env:YOUTUBE_API_KEY  = ""

# 업로드까지 할지 ("1" = 업로드, "0" = 영상까지만 만들고 멈춤)
$env:DO_UPLOAD        = "0"
