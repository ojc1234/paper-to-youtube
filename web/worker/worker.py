"""paper-to-youtube 워커 (리눅스 서버 또는 Windows PC 에서 실행)

서버에서 PDF 작업을 가져와 Hermes(headless)로 슬라이드 → 나레이션 → 영상 → 업로드를 시키고,
Hermes 가 쓰는 도구 호출을 사람이 읽기 쉬운 설명 + 남은 시간으로 바꿔 서버에 실시간 전송한다.

  set P2Y_SERVER=http://172.30.1.87:8000      (서버 주소)
  set P2Y_WORKER_TOKEN=...                     (서버와 같은 토큰)
  python worker.py

서버로 '나가는' 요청만 하므로 이 PC 가 내부 IP/NAT 뒤에 있어도 된다.
"""
import json, os, re, shutil, subprocess, sys, threading, time, urllib.request, urllib.error
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]            # E:/codingstudy/paper-to-youtube
SK = KIT / "skills"
OUT = KIT / "output"
HERE = Path(__file__).resolve().parent


def load_env():
    f = HERE / "worker.env"
    if f.exists():
        for l in f.read_text("utf-8").splitlines():
            if "=" in l and not l.strip().startswith("#"):
                k, v = l.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
SERVER = os.environ.get("P2Y_SERVER", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("P2Y_WORKER_TOKEN", "")
NAME = os.environ.get("P2Y_WORKER_NAME", os.environ.get("COMPUTERNAME", "worker"))
HERMES = os.environ.get("P2Y_HERMES", shutil.which("hermes") or "hermes")
MODEL = os.environ.get("P2Y_MODEL", "")
PROVIDER = os.environ.get("P2Y_PROVIDER", "")
STATS = HERE / "stage_stats.json"
IS_WIN = os.name == "nt"
PW_PY = os.environ.get("P2Y_PW_PYTHON", os.path.expanduser("~/.local/share/uv/tools/playwright/bin/python"))
if IS_WIN:
    SHELL_NOTE = "경로는 항상 `E:/...` 형식으로 넘긴다. 셸은 git-bash."
    UPLOAD_CMD = 'PYTHONIOENCODING=utf-8 python3 "{sk}/paper-youtube-browser-upload/scripts/bsk_upload.py" "{d}" --privacy {privacy}'
else:
    SHELL_NOTE = ("리눅스 서버(bash)다. 절대경로를 쓴다. 메모리 3GB·CPU 2개라 렌더가 느리니 기다린다. "
                  "`latexmk -xelatex main.tex` 는 그대로 쓰면 된다(가벼운 대체 스크립트). 파이썬 도구는 `uv run` 으로 실행한다.")
    UPLOAD_CMD = PW_PY + ' "{sk}/paper-youtube-browser-upload/scripts/pw_upload.py" "{d}" --privacy {privacy}'


# 단계: (키, 이름, 기본 소요 초, 진행률 시작 %)
STAGES = [
    ("read", "논문 읽기", 150),
    ("slides", "슬라이드 만들기", 480),
    ("narr", "나레이션 쓰기", 300),
    ("render", "음성 합성 · 본편 렌더링", 420),
    ("media", "썸네일 · 쇼츠 만들기", 200),
    ("meta", "제목 · 설명 · 챕터 작성", 90),
    ("upload", "유튜브 업로드", 360),
]
KEYS = [s[0] for s in STAGES]


def http(method, path, data=None, raw=False, timeout=30):
    body = None
    headers = {"X-Worker-Token": TOKEN, "X-Worker-Name": NAME}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(SERVER + path, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
        if raw:
            return b
        return json.loads(b) if b and r.status != 204 else None


class Reporter:
    def __init__(self, jid, d: Path):
        self.jid, self.d = jid, d
        self.stats = json.loads(STATS.read_text("utf-8")) if STATS.exists() else {}
        self.dur = {k: self.stats.get(k, dflt) for k, _, dflt in STAGES}
        self.cur = -1
        self.stage_t0 = time.time()
        self.taken = {}
        self.lock = threading.Lock()

    def send(self, ev):
        ev.setdefault("t", time.time())
        for _ in range(3):
            try:
                http("POST", f"/api/worker/{self.jid}/event", ev)
                return
            except Exception as e:
                print("send fail", e)
                time.sleep(2)

    def eta(self):
        if self.cur < 0:
            return sum(self.dur.values())
        el = time.time() - self.stage_t0
        cur_left = max(self.dur[KEYS[self.cur]] - el, 20)
        return int(cur_left + sum(self.dur[k] for k in KEYS[self.cur + 1:]))

    def progress(self):
        tot = sum(self.dur.values())
        done = sum(self.dur[k] for k in KEYS[:max(self.cur, 0)])
        if self.cur >= 0:
            done += min(time.time() - self.stage_t0, self.dur[KEYS[self.cur]] * 0.95)
        return round(100 * done / tot, 1)

    def stage(self, key, text=""):
        with self.lock:
            i = KEYS.index(key)
            if i <= self.cur:
                return
            if self.cur >= 0:
                self.taken[KEYS[self.cur]] = time.time() - self.stage_t0
            for k in KEYS[self.cur + 1:i]:  # 건너뛴 단계
                self.taken.setdefault(k, 0)
            self.cur, self.stage_t0 = i, time.time()
            name = STAGES[i][1]
            self.send({"type": "stage", "stage": f"{i + 1}/{len(STAGES)} {name}", "text": text,
                       "eta": self.eta(), "progress": self.progress()})

    def ev(self, typ, text, detail=None):
        self.send({"type": typ, "text": text, "detail": detail, "eta": self.eta(), "progress": self.progress()})

    def save_stats(self):
        if self.cur >= 0:
            self.taken[KEYS[self.cur]] = time.time() - self.stage_t0
        for k, v in self.taken.items():
            if v > 5:
                self.stats[k] = int(0.6 * self.stats.get(k, v) + 0.4 * v) if k in self.stats else int(v)
        STATS.write_text(json.dumps(self.stats, indent=1), "utf-8")


# ---------------------------------------------------------------- 도구 호출 → 사람 말
RULES = [  # (정규식, 단계, 설명)
    (r"bsk_upload|pw_upload", "upload", "크롬 브라우저를 직접 조작해서 유튜브 스튜디오에 본편과 쇼츠를 올리고 있어요. 제목·설명·썸네일·태그를 채우고 게시한 뒤, 쇼츠에 본편 링크 댓글까지 답니다."),
    (r"youtube_meta", "meta", "유튜브 제목, 설명란(논문 정보·라이선스), 태그, 챕터 시간을 정리하고 있어요."),
    (r"make_shorts|shorts_script", "media", "쇼츠용 대본을 쓰고 60초 이내 세로 영상을 만들고 있어요."),
    (r"make_thumbnail", "media", "논문 그림이나 인상적인 슬라이드로 썸네일을 만들고 있어요."),
    (r"slides_to_video\.py\"?\s+full|slides_to_video.py full", "render", "슬라이드 한 장마다 한국어 음성을 합성하고 이미지와 이어 붙여 본편 영상을 렌더링하고 있어요. 보통 몇 분 걸려요."),
    (r"narr\.py|main_with_narration|NARRATION", "narr", "슬라이드마다 읽어 줄 한국어 나레이션을 쓰고 있어요. 화면을 그대로 읽지 않고 쉽게 풀어서 설명하도록요."),
    (r"slides_to_video\.py\"?\s+plan", "narr", "페이지 계획을 확인해서 나레이션 개수를 PDF 쪽수와 맞추고 있어요."),
    (r"latexmk|xelatex|main\.tex|preamble_ko|sustech-theme", "slides", "LaTeX(Beamer)로 한국어 슬라이드를 만들고 컴파일하고 있어요. 글자가 넘치면 스스로 고칩니다."),
    (r"paper\.txt|source\.pdf|figures", "read", "업로드한 PDF 의 본문과 그림을 읽고 핵심을 파악하고 있어요."),
]


def describe(name, inp):
    s = json.dumps(inp, ensure_ascii=False) if not isinstance(inp, str) else inp
    cmd = inp.get("command") if isinstance(inp, dict) else None
    path = (inp.get("path") or inp.get("file_path")) if isinstance(inp, dict) else None
    for pat, st, text in RULES:
        if re.search(pat, s):
            break
    else:
        st, text = None, None
    # 읽기·탐색(ls/cat/grep/read_file)은 단계를 앞당기지 않는다 → 남은 시간이 갑자기 줄지 않게
    looking = name in ("read_file", "search_files") or (cmd and re.match(r"\s*(cd [^&;]+&&\s*)?(ls|cat|head|tail|grep|find|which|for c in|wc|echo|file|pdfinfo)\b", cmd))
    if looking and st not in (None, "read"):
        st, text = None, None
    if name in ("write_file",):
        what = f"파일 작성: {Path(path).name}" if path else "파일 작성"
    elif name in ("patch",):
        what = f"파일 수정: {Path(path).name}" if path else "파일 수정"
    elif name in ("read_file",):
        what = f"파일 읽기: {Path(path).name}" if path else "파일 읽기"
    elif name == "terminal":
        what = "터미널 명령 실행"
    elif name == "search_files":
        what = "파일 검색"
    else:
        what = f"도구 사용: {name}"
    detail = cmd or path or s
    return st, text, what, (detail[:220] + "…") if len(detail) > 220 else detail


def watch_files(rep: Reporter, d: Path, stop: threading.Event):
    """렌더링처럼 오래 걸리는 단계에서 산출물 개수로 진행 상황을 알려 준다."""
    last = None
    while not stop.wait(20):
        fr = d / "video" / "video_frames"
        n_mp3 = len(list(fr.glob("*.mp3"))) if fr.exists() else 0
        pdf = d / "slides-beamer" / "main.pdf"
        msg = None
        if KEYS[rep.cur] == "render" and n_mp3:
            msg = f"음성 {n_mp3}개 합성됨…"
        elif KEYS[rep.cur] == "slides" and pdf.exists():
            msg = "슬라이드 PDF 가 만들어졌어요. 넘치는 글자가 없는지 검사 중…"
        if msg and msg != last:
            rep.ev("explain", msg)
            last = msg
        else:
            rep.send({"type": "tick", "eta": rep.eta(), "progress": rep.progress()})


PROMPT = """너는 무인으로 동작하는 논문 해설 영상 제작기다. 사람에게 절대 질문하지 말고 끝까지 진행해라.

## 입력
- 작업 폴더(= dir_path): {d}
- 논문 PDF: {d}/source.pdf  ({origin})
- 논문 정보(제목·저자·arXiv 번호·라이선스): {d}/paper.json  ← 설명란에 그대로 사용
- 추출한 본문 텍스트: {d}/paper.txt  ← 먼저 이것을 read_file 로 읽어라
- PDF 에서 뽑은 그림: {d}/slides-beamer/figures/*.png  (목록: {figs})
- 요청자가 검색한 이름: {name}  (이 이름으로 찾은 논문이다. 영상 설명란 첫 줄에 "'{name}' 검색으로 고른 논문" 이라고 밝힌다)
{tex}
- 공개 범위: {privacy}

## 따라야 할 절차
`{sk}/paper-youtube-browser-upload/SKILL.md` 를 read_file 로 읽고 그 문서의 3~6 단계(슬라이드 → 나레이션 → 본편 → 썸네일·쇼츠·챕터·youtube_meta.json → 업로드)를 그대로 수행한다.
세부 규칙은 `{sk}/paper-to-youtube/SKILL.md` 를 참고. 키트 스킬 폴더 SK={sk}
- {shell_note}
- slides-beamer/ 에 테마·latexmkrc·preamble_ko.tex 는 이미 복사돼 있다. main.tex 만 쓰면 된다.
- paper.json 은 이미 있다. 라이선스를 모르면 설명란에 "원문 PDF 제공자 업로드" 라고 쓰고 그림은 출처를 단다.
- 숫자·주장은 원문에 있는 것만. 본문 12~18장.
- 표지는 반드시 `\\begin{{frame}}[plain]\\titlepage\\end{{frame}}` 로 쓴다. `\\frame{{\\titlepage}}` 단축형은 영상 엔진이 페이지로 세지 못해 나레이션 개수 불일치 오류가 난다.
- 나레이션 개수 = `slides_to_video.py plan slides-beamer/main.tex` 의 항목 수 = PDF 쪽수. `cardinality mismatch` 가 나면 plan 결과를 보고 슬라이드나 나레이션을 맞춘 뒤 다시 렌더한다.
- 어떤 오류가 나도 원인을 고쳐서 **업로드까지 끝낸다.** 중간 보고로 멈추거나 수동 작업을 남기지 않는다.
- 본편 렌더는 오래 걸리니 terminal timeout 을 600 으로 준다.
- 업로드(이 명령만 쓴다, SKILL.md 의 bsk 명령 대신): `{upload_cmd}` (timeout 600)
- 끝나면 `{d}/video/upload_result.json` 에 main_url, shorts_url 이 있어야 한다.

## 진행 설명
각 단계를 시작할 때 무엇을 왜 하는지 한국어 한두 문장으로 먼저 말하고 도구를 호출해라 (사용자가 실시간으로 본다).
마지막 답변은 `RESULT {{"main_url": ..., "shorts_url": ..., "title": ...}}` 한 줄로 끝낸다.
"""


def prepare(job, d: Path):
    d.mkdir(parents=True, exist_ok=True)
    (d / "video").mkdir(exist_ok=True)
    sb = d / "slides-beamer"
    (sb / "figures").mkdir(parents=True, exist_ok=True)
    tpl = SK / "paper-to-beamer" / "templates" / "sustech"
    if not (sb / "sustech-theme").exists():
        shutil.copytree(tpl / "sustech-theme", sb / "sustech-theme")
    rc = (tpl / "latexmkrc").read_text("utf-8")
    if not IS_WIN:  # 리눅스 TEXINPUTS 구분자는 ':'
        rc = rc.replace("./sustech-theme//;", "./sustech-theme//:")
    (sb / "latexmkrc").write_text(rc, "utf-8")
    shutil.copy(SK / "paper-to-youtube" / "templates" / "preamble_ko.tex", sb)
    (d / "source.pdf").write_bytes(http("GET", f"/api/worker/{job['id']}/pdf", raw=True, timeout=120))
    code = r"""
import pymupdf,sys,json
d=sys.argv[1]; doc=pymupdf.open(d+'/source.pdf')
open(d+'/paper.txt','w',encoding='utf-8').write('\n'.join(f'=== p{i+1} ===\n'+p.get_text() for i,p in enumerate(doc)))
figs=[]
for pi,p in enumerate(doc):
  for j,im in enumerate(p.get_images(full=True)):
    try:
      pix=pymupdf.Pixmap(doc,im[0])
      if pix.n-pix.alpha>3: pix=pymupdf.Pixmap(pymupdf.csRGB,pix)
      if pix.width<300 or pix.height<150: continue
      fn=f'p{pi+1}_{j}.png'; pix.save(d+'/slides-beamer/figures/'+fn); figs.append(f'{fn} {pix.width}x{pix.height}')
    except Exception: pass
meta=doc.metadata or {}
print(json.dumps({'pages':doc.page_count,'title':meta.get('title') or '','author':meta.get('author') or '','figs':figs[:25]}))
"""
    r = subprocess.run(["uv", "run", "--with", "pymupdf", "python", "-c", code, str(d)],
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    info = json.loads(r.stdout.strip().splitlines()[-1])
    paper = job.get("paper") or {}
    info["arxiv"] = paper.get("arxiv_id", "")
    info["tex"] = False
    lic = "unknown (user-provided PDF)"
    if info["arxiv"]:
        try:  # arXiv 논문이면 라이선스 확인 + TeX 원본 시도 (그림·수식이 더 정확)
            html = urllib.request.urlopen(f"https://arxiv.org/abs/{info['arxiv']}", timeout=30).read().decode("utf-8", "replace")
            m = re.search(r"creativecommons\.org/licenses/([^\"']+)", html)
            lic = f"CC {m.group(1).strip('/').upper().replace('/', ' ')}" if m else "arXiv nonexclusive-distrib 1.0"
        except Exception:
            lic = "arXiv (확인 실패)"
        try:
            g = subprocess.run([sys.executable, str(SK / "paper-to-youtube/scripts/get_source.py"), info["arxiv"], "--output", str(d)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            info["tex"] = (d / "paper_src").exists() and any((d / "paper_src").rglob("*.tex"))
        except Exception:
            pass
        if not info["tex"]:
            for tgz in (d / "_download").glob("*.tar.gz") if (d / "_download").exists() else []:
                (d / "paper_src").mkdir(exist_ok=True)
                subprocess.run(["tar", "-xzf", str(tgz), "-C", str(d / "paper_src")], capture_output=True)
            info["tex"] = (d / "paper_src").exists() and any((d / "paper_src").rglob("*.tex"))
    pj = {"arxiv_id": info["arxiv"], "title": paper.get("title") or info["title"] or job["filename"],
          "authors": paper.get("authors") or ([info["author"]] if info["author"] else []),
          "summary": paper.get("summary", ""), "published": paper.get("published", ""),
          "url": paper.get("url", ""), "dir_name": d.name, "dir_path": str(d), "license": lic,
          "source": "arxiv-search" if info["arxiv"] else "web-upload", "requester": job.get("name", "")}
    (d / "paper.json").write_text(json.dumps(pj, ensure_ascii=False, indent=2), "utf-8")
    return info


def run_job(job):
    jid = job["id"]
    d = OUT / f"web-{jid}"
    rep = Reporter(jid, d)
    rep.send({"type": "started", "text": f"작업 PC({NAME})가 작업을 시작했어요.", "eta": rep.eta(), "progress": 0})
    try:
        rep.stage("read", "PDF 를 받아 본문 텍스트와 그림을 뽑는 중")
        info = prepare(job, d)
        rep.ev("explain", f"PDF {info['pages']}쪽에서 본문과 그림 {len(info['figs'])}장을 뽑았어요."
               + (" arXiv TeX 원본도 받았어요." if info.get("tex") else "") + " 이제 Hermes 에게 일을 맡깁니다.")
        origin = (f"arXiv:{info['arxiv']} 에서 받은 PDF. 이미 받았으니 다시 검색/다운로드하지 않는다"
                  if info["arxiv"] else "사용자가 직접 올린 PDF. arXiv 검색/다운로드 단계는 건너뛴다")
        tex = (f"- TeX 원본: {d.as_posix()}/paper_src/ (있으면 수식·그림은 여기서 가져온다. 그림이 .pdf 면 pdftoppm -png -r 200 -singlefile 로 변환)"
               if info.get("tex") else "")
        upload_cmd = UPLOAD_CMD.format(sk=SK.as_posix(), d=d.as_posix(), privacy=job.get("privacy", "PUBLIC"))
        prompt = PROMPT.format(d=d.as_posix(), sk=SK.as_posix(), name=job.get("name") or "(없음)", origin=origin, tex=tex,
                               shell_note=SHELL_NOTE, upload_cmd=upload_cmd,
                               privacy=job.get("privacy", "PUBLIC"), figs=", ".join(info["figs"]) or "없음")
        pf = d / "hermes_prompt.md"
        pf.write_text(prompt, "utf-8")
        pp = job.get("paper") or {}
        rep.ev("explain", "Hermes 에게 준 입력: "
               + (f"① '{job.get('name')}' 로 찾은 논문 「{pp.get('title', '')}」(arXiv:{pp.get('arxiv_id')})의 PDF·본문·그림 " if pp else "① 올린 PDF 의 본문과 그림 ")
               + f"② 키트의 절차서(SKILL.md) 경로 ③ '질문하지 말고 끝까지, {job.get('privacy', 'PUBLIC')} 로 업로드' 라는 규칙. 지금부터 Hermes 가 스스로 판단해서 도구를 호출합니다.")
        cmd = ([sys.executable] if HERMES.endswith(".py") else []) + [HERMES, "chat", "--query-file", str(pf), "--format", "stream-json", "--yolo",
               "--max-turns", "220", "-t", "terminal,file"]
        if PROVIDER:
            cmd += ["--provider", PROVIDER]
        if MODEL:
            cmd += ["-m", MODEL]
        stop = threading.Event()
        threading.Thread(target=watch_files, args=(rep, d, stop), daemon=True).start()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "EDGE_TTS_VOICE": "ko-KR-SunHiNeural",
               "EDGE_TTS_RATE": "+0%", "RENDER_DPI": "305"}
        p = subprocess.Popen(cmd, cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", env=env)
        log = open(d / "hermes_stream.jsonl", "w", encoding="utf-8")
        final = ""
        buf = ""

        def flush():
            nonlocal buf
            txt = buf.strip()
            buf = ""
            txt = re.sub(r"<[｜|][^>]*>", "", txt).strip()  # 모델 내부 토큰 제거
            if txt and not txt.startswith("RESULT"):
                rep.ev("say", txt[:600])

        for line in p.stdout:
            log.write(line); log.flush()
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = e.get("type")
            if t == "text":  # stream-json 은 텍스트를 조각으로 보낸다 → 모아서 문장 단위로
                buf += e.get("text", "")
                if len(buf) > 400 or buf.rstrip().endswith(("다.", "요.", "니다.", "\n\n")):
                    flush()
                continue
            flush()
            if t == "tool_use":
                st, text, what, detail = describe(e.get("name", ""), e.get("input", {}))
                if st:
                    rep.stage(st)
                if text:
                    rep.ev("explain", text)
                rep.ev("tool", what, detail)
            elif t == "tool_result" and e.get("is_error"):
                rep.ev("explain", "방금 단계에서 오류가 났어요. Hermes 가 원인을 보고 다시 시도합니다.")
            elif t == "result":
                final = e.get("text", "")
        flush()
        p.wait()
        stop.set()
        res_f = d / "video" / "upload_result.json"
        if not res_f.exists():
            raise RuntimeError(f"Hermes 가 업로드까지 끝내지 못했어요 (exit {p.returncode}). {final[-300:]}")
        res = json.loads(res_f.read_text("utf-8"))
        meta_f = d / "video" / "youtube_meta.json"
        if meta_f.exists():
            res["title"] = json.loads(meta_f.read_text("utf-8")).get("title", "")
        res["paper"] = json.loads((d / "paper.json").read_text("utf-8")).get("title", "")
        rep.save_stats()
        rep.send({"type": "done", "result": res, "eta": 0, "progress": 100, "stage": "완료"})
    except Exception as ex:
        rep.send({"type": "error", "text": str(ex)[:800], "stage": "실패"})


def main():
    if not TOKEN:
        sys.exit("P2Y_WORKER_TOKEN 이 필요합니다 (worker.env)")
    print(f"worker {NAME} → {SERVER}")
    while True:
        try:
            job = http("POST", "/api/worker/next")
            if job:
                print("job", job)
                run_job(job)
                continue
        except urllib.error.HTTPError as e:
            print("server", e.code, e.reason)
        except Exception as e:
            print("conn", e)
        time.sleep(5)


if __name__ == "__main__":
    main()
