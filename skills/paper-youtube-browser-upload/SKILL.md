---
name: paper-youtube-browser-upload
description: "Use when making a Korean paper-review YouTube video with the paper-to-youtube kit and uploading it by driving Chrome (BrowserSkill), not the YouTube API. 주제로 arXiv 논문 찾기 → 슬라이드/나레이션/본편/썸네일/쇼츠 → 웹브라우저 조작으로 본편+쇼츠 업로드 → 쇼츠에 본편 링크 댓글."
version: 1.0.0
author: Hermes Agent (오지철 세션에서 정리)
platforms: [windows]
metadata:
  hermes:
    tags: [youtube, arxiv, paper-review, browserskill, bsk, chrome, upload, korean]
    related_skills: [paper-to-youtube, canvas-lecture-automation]
---

# 논문 → 한국어 유튜브 영상 → 웹브라우저로 업로드

`paper-to-youtube` 키트(`E:\codingstudy\paper-to-youtube`)로 영상을 만들고,
**YouTube API(OAuth) 대신 사용자가 이미 로그인한 Chrome 을 BrowserSkill(`bsk`)로 직접 조작**해서 올린다.
OAuth 클라이언트가 없어도 되고, API 업로드처럼 비공개로 잠기는 문제가 없다.

## 경로 / 고정값

| 항목 | 값 |
|---|---|
| 키트 | `E:/codingstudy/paper-to-youtube` (스킬: `skills/`) |
| 출력 | `E:/codingstudy/paper-to-youtube/output/<arXiv 연도 - 짧은 이름>/` |
| BrowserSkill CLI | `C:\Users\ojc12\bsk\bsk.exe` |
| 업로드 스크립트 | `scripts/bsk_upload.py` (이 스킬 폴더) |
| 채널 Studio | `https://studio.youtube.com/channel/UCSPGgP1LHjpvbihHkKRnAiQ` |
| 음성 | `EDGE_TTS_VOICE=ko-KR-SunHiNeural`, `EDGE_TTS_RATE=+0%`, `RENDER_DPI=305` |

경로는 Git-bash 에서도 **`E:/...` 형식**으로 넘긴다. `~/.claude/...` 같은 MSYS 경로를 python 에 넘기면 `E:\c\Users\...` 로 깨진다.

## 0. 사전 확인

```bash
for c in edge-tts ffmpeg ffprobe pdftoppm xelatex latexmk uv; do which $c || echo "MISSING $c"; done
C:/Users/ojc12/bsk/bsk.exe doctor     # "1 browser(s) connected" 확인
```

- Hermes 내장 `browser_exec(local=true)` 는 기본 브라우저가 Chromium 이 아니라서 실패함 → **반드시 bsk 사용**.
- bsk 파일 업로드가 `Not allowed` 로 실패하면: `chrome://extensions` → BrowserSkill 세부정보 → **"파일 URL에 대한 액세스 허용"** 을 켜 달라고 사용자에게 요청. 켜면 확장이 재시작되어 **기존 세션이 사라지므로 `session start` 를 다시** 한다.

## 1. 논문 찾기 (주제 지정 시)

`web_search` 가 설정 문제로 안 될 수 있으므로 arXiv API 를 직접 쓴다.

```bash
curl -s "https://export.arxiv.org/api/query?search_query=all:symplectic+AND+all:Maxwell+AND+all:Hamiltonian&sortBy=submittedDate&sortOrder=descending&max_results=12"
```

**사람에게 묻지 않는다.** 후보 중 에이전트가 직접 고른다. 선정 기준(우선순위):
1. 일반 시청자가 **쉽게 이해하고 재밌어 보이는** 논문 (게임·시뮬레이션·실험·눈에 보이는 결과). 수식 위주 이론 논문은 피한다.
2. 주제와 직접 관련 있고, 최근 논문 우선.
3. TeX/PDF 원본을 받을 수 있는 것.
고른 이유는 최종 보고에 한 줄로 적는다. 매일 자동 실행이면 키트의 `fetch_papers.py` 를 쓴다.
- arXiv API 는 연속 호출 시 429/503 이 잦다 → 쿼리 사이 20~60초 쉬고, `abs:"..."` 구문 검색을 쓴다.

## 2. 폴더 + paper.json + 원본

1. 출력 폴더 생성, arXiv API 결과로 `paper.json`(arxiv_id, title, summary, authors, published, url, dir_name, dir_path, license) 작성.
2. 라이선스: `curl -sL https://arxiv.org/abs/<id> | grep -io 'licenses/[^"]*'`
3. 원본:
   ```bash
   python3 "E:/codingstudy/paper-to-youtube/skills/paper-to-youtube/scripts/get_source.py" <id> --output "<dir>"
   ```
   마지막 줄 `SOURCE: tex <dir>/paper_src` 이면 .tex 를 읽는다. `grep -n 'section\|begin{Theorem}'` 으로 구조부터 파악하고 정리·명제·결론 부분만 읽는다.

## 3. 슬라이드

```bash
SK=E:/codingstudy/paper-to-youtube/skills
mkdir -p "$D/slides-beamer/figures" "$D/video"
cp -r "$SK/paper-to-beamer/templates/sustech/sustech-theme" "$SK/paper-to-beamer/templates/sustech/latexmkrc" "$D/slides-beamer/"
cp "$SK/paper-to-youtube/templates/preamble_ko.tex" "$D/slides-beamer/"
```

- `main.tex` 첫 줄 `\input{preamble_ko}`. 표지는 `\begin{frame}[plain]\titlepage\end{frame}` — `\frame{\titlepage}` 단축형은 plan 이 페이지로 못 세서 `Narration cardinality mismatch` 가 난다. `\bm` 을 쓰면 **바로 다음 줄에 `\usepackage{bm}`** (preamble 에 없음). 파일이 CRLF 라 `sed` 로 넣으면 실패하니 patch 도구를 쓴다.
- 구성: 표지 → 한눈에 보기 → 섹션(왜 중요한가 / 배경 개념 / 핵심 아이디어 / 결과 / 생각해 볼 점(해설자 의견) / 요약) → 감사 장. 본문 12~18장.
- 매크로: `\shl{}`, `\keyword{}`, `\hlbox{}`, `\twopane{}{}`, `callout`, `\tightgap`, `\deckgap`, `\secblurb`. 숫자·주장은 원문에 있는 것만 쓴다.
- 빌드: `latexmk -xelatex main.tex > build.log 2>&1`, 그다음 `grep '^!' main.log`, `grep Overfull main.log` 결과가 0 이어야 한다.
- 넘침 검사(이미지를 못 볼 때): pymupdf 로 텍스트 블록 하단 y 를 확인. 본문이 푸터(≈254/255pt) 위에서 끝나면 정상.

## 4. 나레이션 → 본편

- 페이지 계획: `uv run --project $SK/paper-slides-to-video python $SK/paper-slides-to-video/scripts/slides_to_video.py plan slides-beamer/main.tex`. `\section` 도 한 페이지로 세므로 **나레이션 수 = plan 항목 수 = PDF 페이지 수**.
- `video/narr.py`: 나레이션 리스트 N 을 정의하고, `^\s*\\(begin{frame}|section{)` 앞에 `% NARRATION: ...` 를 넣어 `video/main_with_narration.tex` 를 만든다. 반드시 `assert i==len(N)`.
- 말투: 구어체 존댓말, 수식은 말로 풀고, "이 페이지" 같은 화면 지시어는 쓰지 않는다. 첫 문장은 "오늘 소개할 논문은 ~".
- 렌더(5~10분 걸리므로 백그라운드 + notify):
  ```bash
  export EDGE_TTS_VOICE=ko-KR-SunHiNeural EDGE_TTS_RATE=+0% RENDER_DPI=305 PYTHONIOENCODING=utf-8
  uv run --project $SK/paper-slides-to-video python $SK/paper-slides-to-video/scripts/slides_to_video.py full "$D" --annotated-tex "$D/video/main_with_narration.tex" > video/full.log 2>&1
  ```

## 5. 썸네일 · 쇼츠 · 챕터

```bash
S2=$SK/paper-to-youtube/scripts
uv run --with pillow python $S2/make_thumbnail.py --image video/video_frames/slide_011.png \
  --title "전자기장이\n심플렉틱 행렬로" --badge "물리 논문 리뷰" --sub "arXiv <id>" --out video/thumbnail.jpg
cd $S2 && uv run --with pillow python make_shorts.py "$D/video/shorts_script.json" --out "$D/video/shorts.mp4"
```

- `make_shorts.py` 는 `media_common` 을 import 하므로 **scripts 폴더에서 실행**한다.
- `shorts_script.json`: title(2줄) + 세그먼트 3~5개(`video_frames/slide_###.png` + 문장) + cta. 59초를 넘으면 줄인다.
- 챕터 시간 = 섹션 시작 전까지 `(각 slide_###.mp3 길이 + 0.5) 합 / 1.25`. ffprobe 로 계산하고 첫 챕터는 0:00.
- `video/youtube_meta.json`: title, description(요약, 논문, 저자, 링크, 라이선스, 챕터, AI 제작 고지), tags, categoryId 28, shorts{title,description,tags}, `shorts_comment: "본편 전체 리뷰 보기 👉 {main_url}"`.

## 6. 업로드 (웹브라우저 조작)

공개 범위는 **묻지 않고 항상 PUBLIC** (사용자 지정 기본값). 전 과정에서 사람에게 질문하지 않고 끝까지 진행한다.

```bash
PYTHONIOENCODING=utf-8 python3 "<이 스킬>/scripts/bsk_upload.py" "<paper_dir>" --privacy PUBLIC
```

스크립트가 하는 일: 본편 업로드 → 제목·설명·아동용 아님·썸네일·태그·카테고리 → 공개 범위 → 게시. 이어서 쇼츠를 같은 방식으로 올리고, 쇼츠 시청 페이지에 본편 링크 댓글을 단다. 결과는 `video/upload_result.json`.

### Studio DOM 요령 (스크립트가 깨졌을 때 직접 고치는 법)

| 동작 | 방법 |
|---|---|
| 파일 선택 | `bsk upload --selector '#select-files-button' --file <path>` (숨은 `input[type=file]` 을 지정하면 "no visible geometry" 오류) |
| 제목/설명 | `ytcp-uploads-dialog #textbox` [0]/[1] 은 contenteditable → focus 후 `execCommand('selectAll')`, `execCommand('insertText', …)` |
| 아동용 아님 | `tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_NOT_MFK]` 클릭 |
| 썸네일 | `bsk upload --selector 'ytcp-thumbnail-uploader button#select-button'` |
| 태그 | `#toggle-button`(자세히 보기) → `bsk fill --selector 'input[aria-label*=태그]' --value 'a,b,c,'` → Enter |
| 카테고리 | `ytcp-form-select#category ytcp-dropdown-trigger` 클릭 → `tp-yt-paper-item` 중 '과학기술' 클릭 |
| 다음 단계 | `#next-button` 3번 |
| 공개 범위 | `tp-yt-paper-radio-button[name=PUBLIC]` (UNLISTED / PRIVATE), 영상 링크는 dialog 안의 `a[href*="youtu.be/"]` |
| 게시 | `#done-button` |
| 댓글 | watch 페이지에서 스크롤해야 `#simplebox-placeholder` 가 생김 → 클릭 → `#contenteditable-root` fill → `ytd-commentbox #submit-button` |

## 함정

- bsk 세션 ID 는 실행마다 바뀌고, 확장이 재시작되면 이전 세션은 `unknown` 이 된다.
- 게시 후 "게시된 동영상" 공유 dialog 가 안 잡히거나, 다음 업로드로 이동할 때 `beforeunload` 경고가 떠도 업로드 자체는 정상일 수 있다. 끝나면 Studio 콘텐츠 목록이나 `https://www.youtube.com/oembed?url=https://youtu.be/<id>&format=json` 으로 확인한다.
- 댓글을 `bsk fill` 로 넣으면 **이모지(👉)가 빠진다**("fill could not be confirmed" 경고). 텍스트와 링크는 정상. 이모지가 꼭 필요하면 `execCommand('insertText')` 를 쓴다.
- `get_source.py` 가 `EOFError: Compressed file ended` 로 죽어도 `_download/*.tar.gz` 를 `tar -xzf` 하면 .tex 와 그림 대부분이 풀린다. 그림은 PDF 에서 pymupdf `get_images` 로 뽑아 `slides-beamer/figures/` 에 넣는 게 빠르다.
- 라이선스가 CC BY-NC-ND 면 그림은 **자르거나 고치지 말고** 원본 그대로 쓰고, 각 그림 아래에 출처·라이선스를 단다.
- 쇼츠 세그먼트 이미지 `slide_###.png` 번호 = PDF 페이지 번호(`\section` 구분 페이지 포함). `main.tex` 의 frame 순서로 세지 말고 plan 결과로 확인한다. JSON 수정은 sed 체인 말고 Python 으로.
- 이미지를 볼 수 없는 모델이면 썸네일과 슬라이드는 사람이 확인해야 한다고 보고한다.
- API 로는 쇼츠 '관련 동영상' 연결과 댓글 고정이 안 된다 → 사용자의 수동 작업으로 안내한다.
- 업로드 후 키트의 `history.json`(`PP_ROOT`)에 `{arxiv_id,title,summary,main_url}` 를 추가해야 다음 자동 선정 때 중복을 피한다.

## 최종 보고 형식

논문(제목, arXiv 링크, 한 줄 요지) / 본편·쇼츠 링크와 길이 / 출력 폴더 / 수동 작업(관련 동영상 연결, 댓글 고정) / 확인 못 한 부분.
