"""paper-to-youtube 웹 서버 (Linux 서버에서 실행)

- GET  /                      웹 UI
- GET  /api/search?q=이름      이름 관련 유튜브 영상 (내 채널 제작 영상 + 유튜브 검색)
- POST /api/jobs              PDF 업로드 → 작업 생성
- GET  /api/jobs/{id}/events  진행 상황 SSE (Hermes 가 무엇을 하는지 + 남은 시간)
- 워커(영상 만드는 PC)용: /api/worker/next, /api/worker/{id}/pdf, /api/worker/{id}/event

워커는 서버로 '나가는' 연결만 하므로 영상 PC 가 내부 IP / NAT 뒤에 있어도 동작한다.
"""
import asyncio, json, os, re, secrets, time, uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("P2Y_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "pdf").mkdir(exist_ok=True)
JOBS_FILE = DATA / "jobs.json"
VIDEOS_FILE = DATA / "videos.json"
WORKER_TOKEN = os.environ.get("P2Y_WORKER_TOKEN", "")
MAX_PDF = 40 * 1024 * 1024

app = FastAPI(title="paper-to-youtube")
jobs: dict = json.loads(JOBS_FILE.read_text("utf-8")) if JOBS_FILE.exists() else {}
listeners: dict[str, list[asyncio.Queue]] = {}
worker_seen = {"t": 0.0, "name": ""}


def save():
    JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), "utf-8")


def videos() -> list:
    return json.loads(VIDEOS_FILE.read_text("utf-8")) if VIDEOS_FILE.exists() else []


def public(j: dict) -> dict:
    return {k: j[k] for k in ("id", "name", "filename", "status", "created", "result", "error", "eta", "stage", "progress", "paper") if k in j}


def check_worker(tok: str | None):
    if not WORKER_TOKEN or tok != WORKER_TOKEN:
        raise HTTPException(401, "bad worker token")
    worker_seen["t"] = time.time()


# ---------------------------------------------------------------- UI
@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "static" / "index.html").read_text("utf-8")


@app.get("/api/status")
def status():
    alive = time.time() - worker_seen["t"] < 30
    queued = [j["id"] for j in jobs.values() if j["status"] == "queued"]
    return {"worker_online": alive, "worker": worker_seen["name"], "queued": len(queued),
            "running": sum(1 for j in jobs.values() if j["status"] == "running")}


# ---------------------------------------------------------------- 이름 → 유튜브
def _walk(o, key):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, key)


def _txt(x):
    if not x:
        return ""
    if "simpleText" in x:
        return x["simpleText"]
    return "".join(r.get("text", "") for r in x.get("runs", []))


@app.get("/api/search")
async def search(q: str):
    q = q.strip()
    if not q:
        return {"mine": [], "youtube": []}
    ql = q.lower()
    mine = [v for v in videos() if ql in json.dumps(v, ensure_ascii=False).lower()]
    yt = []
    try:
        async with httpx.AsyncClient(timeout=12, headers={"Accept-Language": "ko-KR,ko;q=0.9",
                                     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36"}) as c:
            r = await c.get("https://www.youtube.com/results", params={"search_query": q, "hl": "ko", "gl": "KR"})
        m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
        if m:
            for v in _walk(json.loads(m.group(1)), "videoRenderer"):
                vid = v.get("videoId")
                if not vid:
                    continue
                yt.append({"title": _txt(v.get("title")), "url": f"https://www.youtube.com/watch?v={vid}",
                           "channel": _txt(v.get("ownerText")), "length": _txt(v.get("lengthText")),
                           "views": _txt(v.get("viewCountText")), "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"})
                if len(yt) >= 12:
                    break
    except Exception as e:  # 검색 실패해도 내 영상 목록은 보여 준다
        return {"mine": mine, "youtube": [], "error": str(e)}
    return {"mine": mine, "youtube": yt}


# ---------------------------------------------------------------- 이름 → 논문 (arXiv)
import xml.etree.ElementTree as ET
_cache: dict = {}
ATOM = "{http://www.w3.org/2005/Atom}"
UA = {"User-Agent": "paper-to-youtube/1.0 (mailto:ojc1234@users.noreply.github.com)"}


async def to_english(q: str) -> str:
    """한글 검색어는 arXiv 가 못 찾으므로 영어로 바꾼다 (실패하면 원문)."""
    if not re.search(r"[가-힣]", q):
        return q
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://translate.googleapis.com/translate_a/single",
                            params={"client": "gtx", "sl": "ko", "tl": "en", "dt": "t", "q": q})
        return "".join(s[0] for s in r.json()[0]).strip() or q
    except Exception:
        return q


async def arxiv_query(search_query: str, n: int = 15) -> list:
    key = (search_query, n)
    if key in _cache and time.time() - _cache[key][0] < 900:
        return _cache[key][1]
    last = None
    async with httpx.AsyncClient(timeout=25, headers=UA, follow_redirects=True) as c:
        for wait in (0, 4, 10, 20):  # arXiv 는 429/503 을 자주 준다 → 재시도
            await asyncio.sleep(wait)
            try:
                r = await c.get("https://export.arxiv.org/api/query", params={
                    "search_query": search_query, "start": 0, "max_results": n, "sortBy": "relevance"})
                if r.status_code == 200 and "<feed" in r.text:
                    break
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = str(e)
        else:
            raise HTTPException(503, f"arXiv 검색이 잠시 막혔어요 ({last}). 30초 뒤 다시 시도해 주세요.")
    out = []
    for e in ET.fromstring(r.text).findall(ATOM + "entry"):
        aid = e.findtext(ATOM + "id", "").rsplit("/abs/", 1)[-1]
        if not aid:
            continue
        cats = [c.get("term") for c in e.findall(ATOM + "category")]
        out.append({"arxiv_id": re.sub(r"v\d+$", "", aid), "version": aid,
                    "title": " ".join(e.findtext(ATOM + "title", "").split()),
                    "summary": " ".join(e.findtext(ATOM + "summary", "").split()),
                    "authors": [a.findtext(ATOM + "name", "") for a in e.findall(ATOM + "author")],
                    "published": e.findtext(ATOM + "published", "")[:10], "categories": cats,
                    "url": f"https://arxiv.org/abs/{aid}"})
    _cache[key] = (time.time(), out)
    return out


@app.get("/api/papers")
async def papers(q: str):
    q = q.strip()[:120]
    if not q:
        return {"query": q, "english": q, "papers": [], "made": []}
    en = await to_english(q)
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", en) if len(w) > 2][:6]
    parts = [f'au:"{en}"', f'ti:"{en}"']
    if words:
        parts.append("(" + " AND ".join(f"all:{w}" for w in words) + ")")
    res = [dict(p) for p in await arxiv_query(" OR ".join(parts))]
    made = {v.get("arxiv_id"): v for v in videos() if v.get("arxiv_id")}
    ql = q.lower()
    for p in res:
        p["video"] = made.get(p["arxiv_id"])
        p["author_match"] = any(ql in a.lower() or en.lower() in a.lower() for a in p["authors"])
    res.sort(key=lambda p: (not p["author_match"], p["video"] is not None))  # 저자 일치 우선
    mine = [v for v in videos() if ql in json.dumps(v, ensure_ascii=False).lower()]
    return {"query": q, "english": en, "papers": res, "made": mine}


@app.post("/api/jobs/arxiv")
async def create_job_arxiv(request: Request):
    b = await request.json()
    aid = re.sub(r"v\d+$", "", str(b.get("arxiv_id", "")).strip())
    if not re.fullmatch(r"(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7})", aid):
        raise HTTPException(400, "arXiv 번호가 이상해요")
    for v in videos():
        if v.get("arxiv_id") == aid:
            raise HTTPException(409, f"이미 만든 영상이 있어요: {v['url']}")
    for j in jobs.values():
        if (j.get("paper") or {}).get("arxiv_id") == aid and j["status"] in ("queued", "running"):
            return public(j)
    paper = b.get("paper") if isinstance(b.get("paper"), dict) and b["paper"].get("arxiv_id") == aid else None
    if not paper:  # 검색 결과 캐시에서 찾기
        paper = next((p for _, (_, ps) in list(_cache.items()) for p in ps if p["arxiv_id"] == aid), None)
    if not paper:
        paper = {"arxiv_id": aid, "title": aid, "summary": "", "authors": [], "published": "",
                 "url": f"https://arxiv.org/abs/{aid}"}
    paper = {k: paper.get(k) for k in ("arxiv_id", "title", "summary", "authors", "published", "url", "categories")}
    async with httpx.AsyncClient(timeout=90, headers=UA, follow_redirects=True) as c:
        r = await c.get(f"https://arxiv.org/pdf/{aid}")
    if not r.content.startswith(b"%PDF"):
        raise HTTPException(502, "arXiv 에서 PDF 를 받지 못했어요")
    jid = time.strftime("%m%d%H%M") + "-" + uuid.uuid4().hex[:6]
    (DATA / "pdf" / f"{jid}.pdf").write_bytes(r.content)
    privacy = str(b.get("privacy", "PUBLIC")).upper()
    jobs[jid] = {"id": jid, "name": str(b.get("name", "")).strip()[:80], "filename": f"{aid}.pdf",
                 "privacy": privacy if privacy in ("PUBLIC", "UNLISTED", "PRIVATE") else "PUBLIC",
                 "paper": paper, "status": "queued", "created": time.time(), "events": [], "eta": None,
                 "stage": "대기 중", "progress": 0}
    save()
    return public(jobs[jid])


# ---------------------------------------------------------------- 작업 생성 / 조회
@app.post("/api/jobs")
async def create_job(pdf: UploadFile = File(...), name: str = Form(""), privacy: str = Form("PUBLIC")):
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 올릴 수 있어요")
    data = await pdf.read()
    if len(data) > MAX_PDF or not data.startswith(b"%PDF"):
        raise HTTPException(400, "PDF 가 아니거나 40MB 를 넘어요")
    jid = time.strftime("%m%d%H%M") + "-" + uuid.uuid4().hex[:6]
    (DATA / "pdf" / f"{jid}.pdf").write_bytes(data)
    jobs[jid] = {"id": jid, "name": name.strip()[:80], "filename": pdf.filename, "privacy": privacy.upper() if privacy.upper() in ("PUBLIC", "UNLISTED", "PRIVATE") else "PUBLIC",
                 "status": "queued", "created": time.time(), "events": [], "eta": None, "stage": "대기 중", "progress": 0}
    save()
    return public(jobs[jid])


@app.get("/api/jobs")
def list_jobs():
    return [public(j) for j in sorted(jobs.values(), key=lambda j: -j["created"])][:30]


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    if jid not in jobs:
        raise HTTPException(404)
    return {**public(jobs[jid]), "events": jobs[jid]["events"]}


@app.get("/api/jobs/{jid}/events")
async def job_events(jid: str, request: Request):
    if jid not in jobs:
        raise HTTPException(404)
    q: asyncio.Queue = asyncio.Queue()
    listeners.setdefault(jid, []).append(q)

    async def gen():
        try:
            for ev in jobs[jid]["events"]:  # 지난 기록 먼저
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'state', **public(jobs[jid])}, ensure_ascii=False)}\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            listeners[jid].remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def push(jid: str, ev: dict):
    ev.setdefault("t", time.time())
    jobs[jid]["events"].append(ev)
    jobs[jid]["events"] = jobs[jid]["events"][-600:]
    for q in listeners.get(jid, []):
        q.put_nowait(ev)


# ---------------------------------------------------------------- 워커 API
@app.post("/api/worker/next")
def worker_next(x_worker_token: str | None = Header(None), x_worker_name: str | None = Header(None)):
    check_worker(x_worker_token)
    worker_seen["name"] = x_worker_name or ""
    for j in sorted(jobs.values(), key=lambda j: j["created"]):
        if j["status"] == "queued":
            j["status"] = "running"
            j["started"] = time.time()
            save()
            push(j["id"], {"type": "state", **public(j)})
            return {k: j.get(k) for k in ("id", "name", "filename", "privacy", "paper")}
    return JSONResponse(None, status_code=204)


@app.get("/api/worker/{jid}/pdf")
def worker_pdf(jid: str, x_worker_token: str | None = Header(None)):
    check_worker(x_worker_token)
    return FileResponse(DATA / "pdf" / f"{jid}.pdf", media_type="application/pdf")


@app.post("/api/worker/{jid}/event")
async def worker_event(jid: str, request: Request, x_worker_token: str | None = Header(None)):
    check_worker(x_worker_token)
    if jid not in jobs:
        raise HTTPException(404)
    ev = await request.json()
    j = jobs[jid]
    for k in ("eta", "stage", "progress"):
        if k in ev:
            j[k] = ev[k]
    if ev.get("type") == "done":
        j["status"] = "done"
        j["result"] = ev.get("result")
        r = ev.get("result") or {}
        if r.get("main_url"):
            vs = videos()
            pp = j.get("paper") or {}
            vs.insert(0, {"title": r.get("title", ""), "url": r["main_url"], "shorts": r.get("shorts_url", ""),
                          "paper": r.get("paper", "") or pp.get("title", ""), "arxiv_id": pp.get("arxiv_id", ""),
                          "authors": ", ".join(pp.get("authors", [])[:6]), "name": j["name"], "created": time.time()})
            VIDEOS_FILE.write_text(json.dumps(vs, ensure_ascii=False, indent=1), "utf-8")
    elif ev.get("type") == "error":
        j["status"] = "error"
        j["error"] = ev.get("text")
    push(jid, ev)
    if ev.get("type") in ("done", "error", "stage"):
        save()
    return {"ok": True}


@app.post("/api/worker/{jid}/requeue")
def worker_requeue(jid: str, x_worker_token: str | None = Header(None)):
    check_worker(x_worker_token)
    jobs[jid]["status"] = "queued"
    save()
    return {"ok": True}
