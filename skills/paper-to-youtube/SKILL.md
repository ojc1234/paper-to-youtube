---
name: paper-to-youtube
description: |
  매일 arXiv 새 논문(기본 quant-ph 양자컴퓨팅)을 골라 기존 영상과 겹치는 논문은 거르고, 한국어 Beamer 슬라이드 → 한국어 나레이션 본편 영상 → 썸네일 → 쇼츠를 만들어 YouTube 에 업로드한다. "오늘 논문 영상 만들어줘", "paper to youtube", "논문 유튜브 자동화" 요청이나 매일 예약 실행에 사용. paper-share-skills(yhbcode000) 의 형제 스킬들을 재사용한다.
user-invocable: true
argument-hint: "[--category quant-ph] [--max 1] [--no-upload]"
---

# Paper → YouTube (한국어)

`paper-share-skills` 의 슬라이드·영상 엔진을 그대로 쓰고, 빌리빌리/중국어 부분만 **YouTube/한국어** 로 바꾼 오케스트레이터.
`<SKILLS_DIR>` = 이 폴더의 상위 폴더(예: `~/.claude/skills`). 형제 스킬 `paper-download-arxiv-paper-source`, `paper-to-beamer`, `paper-slides-to-video` 가 같은 곳에 있어야 한다.

## 0. 환경 (매 실행 시작 시 확인)

| 변수 | 기본값 | 의미 |
|---|---|---|
| `PP_ROOT` | (필수) | 작업 루트. `history.json`, `papers_today.json`, `논문영상/` 가 여기 생김 |
| `EDGE_TTS_VOICE` | `ko-KR-SunHiNeural` | 한국어 음성 (남성: `ko-KR-InJoonNeural`, `ko-KR-HyunsuMultilingualNeural`) |
| `EDGE_TTS_RATE` | `+0%` | 본편은 엔진이 1.25배속으로 합치므로 +0% 권장 |
| `RENDER_DPI` | `305` | 슬라이드 렌더 해상도 (16:9 에서 305 ≈ 1920px 폭) |
| `PAPER_CATEGORY` | `quant-ph` | arXiv 분류 |
| `PAPER_KEYWORDS` | (선택) | 우선 고를 키워드, 쉼표 구분 |
| `YOUTUBE_API_KEY` | (선택) | 유튜브 기존 영상과 중복 비교용 |
| `YT_CLIENT_SECRET` | `~/.paper-to-youtube/client_secret.json` | 업로드용 OAuth 파일 |
| `YT_PRIVACY` | `private` | 업로드 공개 상태 |

`EDGE_TTS_VOICE` 가 비어 있으면 반드시 `ko-KR-SunHiNeural` 로 설정한 뒤 진행한다 (기본값이 중국어 음성이므로).
`edge-tts`, `ffmpeg`, `ffprobe`, `pdftoppm`, `xelatex`(또는 `latexmk`) 가 PATH 에 있는지 먼저 확인하고, 없으면 멈추고 무엇이 없는지 보고한다.

## 1. 논문 고르기 + 중복 거르기

```bash
python "<SKILLS_DIR>/paper-to-youtube/scripts/fetch_papers.py" \
  --category "$PAPER_CATEGORY" --max 1 --root "$PP_ROOT" \
  --keywords "$PAPER_KEYWORDS" [--youtube-check]
```

- 출력 마지막 줄 `PAPERS_JSON: <path>`. 종료코드 3 = 새 논문 없음 → "오늘은 새 논문 없음"으로 보고하고 종료.
- 각 논문 폴더(`dir_path`)에 `paper.json` 이 생긴다. 이후 모든 산출물은 이 폴더 안에.
- `SKIP` 줄(이미 만든 논문, 유사도 초과)은 그대로 요약해 보고에 포함한다.

## 2. 논문 원본 받기 (예비 경로 포함)

```bash
python "<SKILLS_DIR>/paper-to-youtube/scripts/get_source.py" "<arxiv_id>" --output "<dir_path>"
```

먼저 `arxiv-downloader`(uv 가상환경에 자동 설치)로 PDF+TeX 원본을 한 번에 받고, 실패하면 e-print 직접 → PDF → arXiv HTML → ar5iv 순서로 넘어간다.
마지막 줄 `SOURCE: <종류> <경로>` 에 따라:
- `tex` → `paper_src/` 의 .tex 를 읽는다 (가장 정확, 그림 파일도 그대로 사용).
- `pdf` → PDF 를 Read 도구로 직접 읽는다 (MinerU 불필요). 그림은 `pdftoppm -png -r 200 -f N -l N` 로 해당 페이지를 잘라 쓴다.
- `html` → `paper.html` 을 읽는다 (arXiv HTML → 실패 시 ar5iv 순서로 시도한 결과).
- 종료코드 2 → 이 논문은 건너뛰고 1단계 후보의 다음 논문으로.

**라이선스 확인**: `https://arxiv.org/abs/<id>` 의 라이선스를 `paper.json` 에 `"license"` 로 기록한다. CC BY 계열이면 그림을 출처와 함께 써도 되고, arXiv 기본 라이선스(nonexclusive-distrib)면 그림은 꼭 필요한 1~3장만 인용 목적으로 쓰고 출처를 명확히 단다. (법률 자문 아님 — 채널 운영 전 직접 확인 권장)

## 3. 한국어 슬라이드

작업 폴더 준비:

```bash
mkdir -p "<dir_path>/slides-beamer/figures" "<dir_path>/video"
cp -r "<SKILLS_DIR>/paper-to-beamer/templates/sustech/sustech-theme" "<SKILLS_DIR>/paper-to-beamer/templates/sustech/latexmkrc" "<dir_path>/slides-beamer/"
cp "<SKILLS_DIR>/paper-to-youtube/templates/preamble_ko.tex" "<dir_path>/slides-beamer/"
```

`main.tex` 는 **첫 줄을 `\input{preamble_ko}`** 로 시작한다 (16:9, 한글 폰트, 학교 로고·크레딧 제거, 중국어 고정 문구 한국어화가 모두 들어 있음). 예시는 `<SKILLS_DIR>/paper-to-youtube/examples/main.tex`.
`skill://paper-to-beamer` 의 매크로 사용법(\shl, \keyword, \hlbox, \metric, \twopane, callout, 그림 한 장당 한 프레임, overflow 금지)을 따르되 **다음 규칙이 우선**한다.

1. **언어**: 원 스킬의 "중국어" 규칙은 전부 "한국어"로. 제목·본문·도메인 태그·callout 모두 한국어. 영어 기술 용어는 `표면 코드(surface code)` 처럼 병기.
2. **비율**: 16:9 (preamble_ko 에 설정됨). 원 스킬의 16:10 규칙보다 우선.
3. **제목**: `\title[<약칭> 논문 리뷰]{<한국어 설명형 제목>}`, `\subtitle{<영어 원제목>}`, `\setpresenter{<채널명>}`, `\setvenue{YouTube}`. 채널 로고가 `$PP_ROOT/channel_logo.png` 에 있으면 `\setlogo[0.14\paperheight]{<절대경로>}`.
4. **분량·흐름**: 본문 12~18장. "왜 중요한가 → 배경 개념(1~2장) → 핵심 아이디어 → 실험/결과 → 생각해 볼 점 → 요약 → 마무리 장". 저자 소개는 이름만 표지에. Q&A 장 대신 "시청해 주셔서 감사합니다" 장.
5. **사실만**: 숫자·주장은 원문에 있는 것만. 원문에 없는 해석은 "생각해 볼 점 (해설자 의견)" 장에만.
6. **함정**: `\item [[72,12,6]] ...` 처럼 `\item` 바로 뒤에 `[` 가 오면 라벨로 해석되니 `\item {[[72,12,6]]} ...` 로 감싼다. TikZ 는 positioning 라이브러리가 이미 로드돼 있다.
7. `latexmk -xelatex main.tex` 로 컴파일, `Overfull` 경고 0 개까지 고친 뒤, `pdftoppm -png -r 60` 으로 몇 장 렌더링해 눈으로 확인한다.

## 4. 한국어 나레이션

`slides-beamer/main.tex` 를 `video/main_with_narration.tex` 로 복사하고, **실제 PDF 페이지마다 정확히 한 줄**의 `% NARRATION: ...` 를 단다 (`\section` 구분 페이지도 한 페이지로 센다 — `slides_to_video.py plan <tex>` 로 페이지 계획 확인).

- 자연스러운 구어체 존댓말("~입니다", "~거든요" 섞어서). 한 페이지 2~5문장.
- 슬라이드 글을 읽지 말고 **설명을 덧붙인다**. "이 페이지", "그림에서 보듯" 같은 화면 지시어 금지.
- 수식·기호는 말로 풀어 쓴다 (`$10^{-3}$` → "천분의 일"). LaTeX 명령, 이모지, 괄호 속 긴 영어 금지.
- 영어 용어는 처음 한 번만 "큐비트, 영어로 qubit" 식으로, 이후 한국어.
- 첫 페이지는 10초 안에 훅: "오늘 소개할 논문은 ~를 해냈습니다."

## 5. 본편 영상

```bash
python "<SKILLS_DIR>/paper-slides-to-video/scripts/slides_to_video.py" full "<dir_path>" \
  --annotated-tex "<dir_path>/video/main_with_narration.tex"
```

결과: `video/<이름>_narrated.mp4`, `video/video_frames/slide_###.png|mp3`. (빌리빌리용 `video_meta.json` 도 생기지만 쓰지 않는다. 원본 `batch.py video` 는 포함되지 않은 세로영상 스킬을 부르므로 **쓰지 않는다**.)

## 6. 썸네일

핵심 그림 1장(논문 `paper_src/` 의 png/pdf 그림 → pdf 면 `pdftoppm -png -r 200 -singlefile` 로 변환)이나 가장 인상적인 슬라이드 프레임을 골라:

```bash
python "<SKILLS_DIR>/paper-to-youtube/scripts/make_thumbnail.py" --image "<그림>" \
  --title "<훅 제목 2줄, 줄당 8~10자, \n 으로 구분>" --badge "양자컴퓨팅 논문 리뷰" \
  --sub "arXiv <id>" --out "<dir_path>/video/thumbnail.jpg"
```

제목은 결과/숫자 중심 ("오류율 10배 감소", "큐비트 1000개 시대"). 과장·거짓 금지 — 논문에 있는 사실만.
만든 뒤 이미지를 직접 열어 글자가 잘리거나 겹치지 않는지 확인한다.

## 7. 쇼츠

`video/shorts_script.json` 작성 (형식은 `scripts/make_shorts.py` 상단 설명):
- 전체 40~55초 (한국어 약 220~300자). 첫 문장이 훅. 3~5 세그먼트, 각 세그먼트는 프레임 PNG 또는 논문 그림 1장.
- `title` 은 2줄 이내, `cta` 는 "자세한 설명은 본편 영상에서 확인하세요."

```bash
python "<SKILLS_DIR>/paper-to-youtube/scripts/make_shorts.py" "<dir_path>/video/shorts_script.json" \
  --out "<dir_path>/video/shorts.mp4"
```

59초를 넘는다는 경고가 나오면 대본을 줄여 다시 만든다.

## 8. 메타데이터 `video/youtube_meta.json`

```json
{
  "title": "<한국어 제목 ≤ 70자> | <약칭> 논문 리뷰",
  "description": "<3~5줄 요약>\n\n📄 논문: <영어 원제목>\n✍️ 저자: <저자>\n🔗 https://arxiv.org/abs/<id>\n📜 라이선스: <license>\n\n⏱️ 챕터\n0:00 소개\n...\n\n이 영상은 AI 도구로 슬라이드·나레이션을 만들고 사람이 검토한 논문 해설입니다.\n#양자컴퓨팅 #논문리뷰 #quantumcomputing",
  "tags": ["양자컴퓨팅", "양자역학", "논문리뷰", "quantum computing", "arXiv", "<핵심 키워드>"],
  "categoryId": "28",
  "shorts": {"title": "<훅 제목> #shorts", "description": "<2줄 요약>\n#양자컴퓨팅 #shorts", "tags": ["양자컴퓨팅", "shorts"]},
  "shorts_comment": "본편 전체 리뷰 보기 👉 {main_url}"
}
```

챕터 시간은 `video/video_frames/slide_###.mp3` 길이 합(+페이지당 0.5초)을 1.25 로 나눠 `\section` 시작 페이지마다 계산한다 (첫 챕터는 반드시 0:00, 3개 이상).

## 9. 업로드

```bash
python "<SKILLS_DIR>/paper-to-youtube/scripts/youtube_upload.py" "<dir_path>" --dry-run
python "<SKILLS_DIR>/paper-to-youtube/scripts/youtube_upload.py" "<dir_path>"
```

- 인자에 `--no-upload` 가 있거나 `YT_CLIENT_SECRET` 파일이 없으면 dry-run 까지만 하고 멈춘다.
- 업로드 후 `history.json` 이 갱신되어 다음 날 중복 판정에 쓰인다. 업로드하지 않은 경우에도 같은 논문이 반복 선택되지 않도록 `history.json` 에 `{"arxiv_id","title","summary","main_url": ""}` 를 추가한다.

## 10. 최종 보고 (짧게)

- 고른 논문(제목, arXiv 링크)과 건너뛴 논문 수·이유
- 생성 파일: 본편 길이, 쇼츠 길이, 썸네일 경로
- 업로드 링크(또는 dry-run 결과)
- 해야 할 수동 작업: **Studio 에서 쇼츠 '관련 동영상'에 본편 연결**, 댓글 고정, 비공개→공개 전환
