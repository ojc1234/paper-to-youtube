"""Playwright(헤드리스 Chromium)로 YouTube Studio 에 본편+쇼츠 업로드 + 쇼츠에 본편 링크 댓글.
리눅스 서버처럼 사용자의 크롬/BrowserSkill 이 없는 곳에서 쓴다. bsk_upload.py 와 같은 결과(video/upload_result.json)를 만든다.

  python pw_upload.py <paper_dir> [--privacy PUBLIC|UNLISTED|PRIVATE] [--profile ~/.p2y-chrome]

로그인: 영구 프로필(--profile) 에 유튜브 로그인이 저장돼 있어야 한다.
  처음 한 번: python pw_upload.py --import-cookies cookies.json  (크롬에서 내보낸 youtube/google 쿠키)
"""
import argparse, json, os, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

CHANNEL = os.environ.get("YT_CHANNEL_ID", "UCSPGgP1LHjpvbihHkKRnAiQ")
ap = argparse.ArgumentParser()
ap.add_argument("paper_dir", nargs="?")
ap.add_argument("--privacy", default="PUBLIC")
ap.add_argument("--profile", default=os.path.expanduser(os.environ.get("P2Y_CHROME_PROFILE", "~/.p2y-chrome")))
ap.add_argument("--import-cookies")
ap.add_argument("--check", action="store_true", help="로그인 상태만 확인")
ap.add_argument("--headful", action="store_true")
a = ap.parse_args()


def log(*x):
    print(*x, flush=True)


def launch(p):
    # channel="chromium" = 전체 Chromium(--headless=new). 기본 headless-shell 빌드는
    # 미디어 기능이 빠져 있어 Studio 업로드가 '준비 중'에서 멈춘다(게시 버튼 비활성).
    return p.chromium.launch_persistent_context(
        a.profile, headless=not a.headful, channel="chromium",
        locale="ko-KR", viewport={"width": 1400, "height": 1000},
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def set_box(page, idx, text):
    page.evaluate("""([i,t]) => {const b=[...document.querySelectorAll('ytcp-uploads-dialog #textbox')][i]; b.focus();
      document.execCommand('selectAll',false,null); document.execCommand('insertText',false,t);
      b.dispatchEvent(new Event('input',{bubbles:true}));}""", [idx, text])


def get_box(page, idx):
    return page.evaluate(
        "i => ([...document.querySelectorAll('ytcp-uploads-dialog #textbox')][i]||{}).innerText || ''", idx)


def jsclick(page, sel, idx=0, required=False):
    """DOM click. 업로드 dialog 위의 tp-yt-iron-overlay-backdrop 때문에 Playwright 의
    액션성 클릭이 'intercepts pointer events' 로 막히는 경우를 우회한다."""
    ok = page.evaluate("""([sel, idx]) => {
        const els=[...document.querySelectorAll(sel)];
        const e=els[idx]; if(!e) return false;
        try { e.scrollIntoView({block:'center'}); } catch(_) {}
        e.click(); return true; }""", [sel, idx])
    log("jsclick", sel, idx, ok)
    if required and not ok:
        raise RuntimeError(f"jsclick target missing: {sel}[{idx}]")
    return ok


def jsclick_text(page, sel, pattern):
    ok = page.evaluate("""([sel, pat]) => {
        const re = new RegExp(pat);
        const e = [...document.querySelectorAll(sel)].find(x => re.test(x.innerText||''));
        if(!e) return false; e.click(); return true; }""", [sel, pattern])
    log("jsclick_text", sel, pattern, ok)
    return ok


def wait_ready(page, timeout=60):
    """업로드 dialog 의 제목/설명 입력칸이 나타날 때까지 evaluate 폴링."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.evaluate("() => document.querySelectorAll('ytcp-uploads-dialog #textbox').length") >= 2:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def dialog_state(page):
    """업로드 dialog 의 현재 단계/버튼 상태를 읽어 온다 (진단 + 단계 진행 판단)."""
    return page.evaluate("""() => {
        const dlg = document.querySelector('ytcp-uploads-dialog');
        const tabs = [...document.querySelectorAll('ytcp-uploads-dialog #stepper paper-tab')].map(t => ({
            t: (t.innerText||'').trim(),
            sel: t.getAttribute('aria-selected') === 'true' || t.classList.contains('iron-selected')}));
        const btns = [...document.querySelectorAll('ytcp-uploads-dialog #next-button, ytcp-uploads-dialog #done-button')]
            .map(b => ({id: b.id, disabled: b.disabled === true || b.getAttribute('aria-disabled') === 'true'}));
        const link = [...document.querySelectorAll('ytcp-uploads-dialog a, ytcp-publish-popup a')].map(a=>a.href)
            .find(h => /youtu\\.be\\/|youtube\\.com\\/shorts\\//.test(h)) || '';
        const progress = (document.querySelector('ytcp-video-upload-progress')?.innerText||'').trim();
        return {open: !!dlg, tabs, btns, link, progress};
    }""")


def find_video(page, prefix, timeout=120):
    """Studio 콘텐츠 목록에서 제목이 prefix 로 시작하는 최근 영상을 찾는다 (게시 확인용)."""
    page.goto(f"https://studio.youtube.com/channel/{CHANNEL}/videos/upload"
              "?filter=%5B%5D&sort=%7B%22columnType%22%3A%22date%22%2C%22sortOrder%22%3A%22DESCENDING%22%7D",
              wait_until="domcontentloaded")
    time.sleep(8)
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = page.evaluate("""() => [...document.querySelectorAll('ytcp-video-row')].map(r => {
            const a = r.querySelector('a[href*="/video/"]');
            const t = r.querySelector('#video-title');
            const m = a ? (a.getAttribute('href')||'').match(/\\/video\\/([\\w-]{11})/) : null;
            return {id: m?m[1]:'', title: (t?.innerText||'').trim(),
                    vis: ((r.innerText||'').match(/비공개|공개|일부 공개/)||[''])[0]};
        })""")
        for r in rows:
            if r["title"].startswith(prefix[:40]):
                return r
        time.sleep(5)
    return None


def upload(page, mp4: Path, m: dict, thumb: Path | None):
    page.goto(f"https://studio.youtube.com/channel/{CHANNEL}/videos/upload?d=ud", wait_until="domcontentloaded")
    page.wait_for_selector("input[type=file]", state="attached", timeout=60000)
    page.set_input_files("input[type=file]", str(mp4))
    log("file set", mp4.name)
    # YouTube Studio CSP 가 Playwright 의 wait_for_function(내부 eval) 을 차단하므로
    # page.evaluate 폴링으로 대기한다.
    if not wait_ready(page, timeout=300):
        log("warning: upload dialog textboxes not detected within timeout")
    time.sleep(3)
    set_box(page, 0, m["title"][:100])
    set_box(page, 1, m["description"][:4900])
    time.sleep(1)
    log("title readback:", get_box(page, 0)[:60].replace("\n", " "))
    jsclick(page, "tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_NOT_MFK]")
    time.sleep(1)
    if thumb and thumb.exists():
        try:
            page.set_input_files("ytcp-thumbnail-uploader input[type=file]", str(thumb), timeout=30000)
            time.sleep(4)
            log("thumbnail set")
        except Exception as e:
            log("thumbnail skip", str(e)[:200])
    try:
        jsclick(page, "#toggle-button")
        time.sleep(2)
        tags = ",".join(m.get("tags", []))
        page.evaluate("""() => {const i=document.querySelector("input[aria-label*='태그'], input[aria-label*='Tags'], #tags-container input");
            if (i) { i.scrollIntoView({block:'center'}); i.focus(); }}""")
        time.sleep(0.5)
        page.keyboard.insert_text(tags)     # 실제 키 이벤트 -> 태그 칩 생성
        page.keyboard.press("Enter")
        time.sleep(1.5)
        log("tags set:", tags[:80])
    except Exception as e:
        log("tags skip", str(e)[:200])
    try:
        jsclick(page, "ytcp-form-select#category ytcp-dropdown-trigger")
        time.sleep(2)
        jsclick_text(page, "tp-yt-paper-item", "과학기술|Science")
        time.sleep(1)
    except Exception as e:
        log("category skip", str(e)[:200])
    # 단계 진행: next-button 이 활성화될 때까지 기다렸다가 누른다 (검사 단계는 시간이 걸린다).
    for step in range(4):
        st = dialog_state(page)
        log(f"step {step + 1} tabs", [t["t"] for t in st["tabs"] if t["sel"]], st["btns"], st.get("progress", "")[:30])
        if not st["open"]:
            log("dialog closed early")
            break
        for _ in range(90):  # 최대 3분, 버튼이 켜지길 기다림
            st = dialog_state(page)
            nxt = next((b for b in st["btns"] if b["id"] == "next-button"), None)
            if nxt and not nxt["disabled"]:
                break
            time.sleep(2)
        jsclick(page, "#next-button")
        time.sleep(3)
    st = dialog_state(page)
    log("after nexts tabs", [t["t"] for t in st["tabs"] if t["sel"]], st["btns"], st.get("progress", "")[:30])
    jsclick(page, f"tp-yt-paper-radio-button[name={a.privacy}]")
    time.sleep(2)
    log("privacy checked:", page.evaluate(
        "(n) => !!document.querySelector(`tp-yt-paper-radio-button[name=${n}][checked], tp-yt-paper-radio-button[name=${n}][aria-checked=true]`)",
        a.privacy))
    # 업로드(전송)가 끝나고 게시 버튼이 켜질 때까지 기다린다.
    # 14MB/8분짜리 영상은 처리·링크 생성에 수 분~십수 분 걸릴 수 있다.
    log("waiting for publish button to enable...")
    for i in range(60):   # 처리·링크 생성 대기 (전체 Chromium 에서는 보통 수십 초)
        st = page.evaluate("""() => {
            const b = document.querySelector('ytcp-uploads-dialog #done-button');
            if (!b) return {missing: true};
            const id = b.getAttribute('aria-describedby');
            const desc = id ? (document.getElementById(id)?.innerText || '') : '';
            return {disabled: b.disabled === true || b.getAttribute('aria-disabled') === 'true',
                    label: (b.innerText||'').trim(), desc: desc.trim(),
                    radios: [...document.querySelectorAll('tp-yt-paper-radio-button')].map(r => ({n: r.getAttribute('name'), c: r.checked === true})).filter(r => r.n),
                    progress: (document.querySelector('ytcp-video-upload-progress')?.innerText||'').trim()};
        }""")
        done = next((b for b in [st] if not b.get("disabled", True)), None)
        if i % 6 == 0:
            log("publish-wait", i * 10, "s", str(st)[:300])
        if done:
            break
        time.sleep(10)
    else:
        log("publish button still disabled after timeout", str(st)[:300])
        # Studio 프론트가 '준비 중' 상태를 갱신하지 못하는 경우가 있어,
        # disabled 를 걷어내고 강제 클릭을 시도한다 (게시 여부는 콘텐츠 목록으로 검증).
        forced = page.evaluate("""() => {
            const b = document.querySelector('ytcp-uploads-dialog #done-button');
            if (!b) return 'missing';
            b.removeAttribute('disabled'); b.setAttribute('aria-disabled', 'false');
            const inner = b.querySelector('[role=button], button, #button') || b;
            inner.click(); b.click();
            return 'forced';
        }""")
        log("forced publish click:", forced)
        time.sleep(10)
    jsclick(page, "#done-button", required=True)
    log("publish clicked (label:", str(st.get("label"))[:30], ")")
    # 게시 확인: 공유 팝업/다이얼로그의 링크가 나올 때까지
    link = ""
    for _ in range(45):
        link = page.evaluate("""() => [...document.querySelectorAll('ytcp-publish-popup a, ytcp-uploads-dialog a, ytcp-sharing-dialog a')]
            .map(a=>a.href).find(h=>/youtu\\.be\\/|youtube\\.com\\/shorts\\//.test(h)) || ''""")
        if link:
            break
        time.sleep(2)
    log("publish link:", link)
    time.sleep(3)
    return link


def comment(page, url, text):
    m = re.search(r"(?:shorts/|youtu\.be/|v=)([\w-]{11})", url or "")
    if not m:
        log("comment skipped: no video id in url", url)
        return
    vid = m.group(1)
    page.goto(f"https://www.youtube.com/watch?v={vid}", wait_until="domcontentloaded"); time.sleep(6)
    for _ in range(6):
        page.mouse.wheel(0, 900); time.sleep(2)
        if page.locator("#simplebox-placeholder").count():
            break
    page.click("#simplebox-placeholder", force=True); time.sleep(2)
    page.evaluate("t => {const e=document.querySelector('#contenteditable-root'); e.focus(); document.execCommand('insertText',false,t)}", text)
    time.sleep(1); page.click("ytd-commentbox #submit-button", force=True); time.sleep(6)


def logged_in(page):
    page.goto(f"https://studio.youtube.com/channel/{CHANNEL}", wait_until="domcontentloaded")
    time.sleep(5)
    return "accounts.google.com" not in page.url and "studio.youtube.com" in page.url


with sync_playwright() as p:
    ctx = launch(p)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    if a.import_cookies:
        cs = json.loads(Path(a.import_cookies).read_text("utf-8"))
        cs = cs.get("cookies", cs) if isinstance(cs, dict) else cs
        norm = []
        for c in cs:
            d = {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c.get("path", "/"),
                 "secure": bool(c.get("secure", True)), "httpOnly": bool(c.get("httpOnly", False))}
            exp = c.get("expires", c.get("expirationDate"))
            if exp and exp > 0:
                d["expires"] = float(exp)
            ss = str(c.get("sameSite", "")).lower()
            d["sameSite"] = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}.get(ss, "Lax")
            if d["name"].startswith("__Secure-") or d["name"].startswith("__Host-") or d["sameSite"] == "None":
                d["secure"] = True
            norm.append(d)
        ctx.add_cookies(norm)
        log("imported", len(norm), "cookies")
    if a.check or a.import_cookies:
        ok = logged_in(page)
        log("LOGGED_IN" if ok else "NOT_LOGGED_IN", page.url)
        ctx.close(); sys.exit(0 if ok else 2)

    V = Path(a.paper_dir) / "video"
    meta = json.loads((V / "youtube_meta.json").read_text("utf-8"))
    if not logged_in(page):
        sys.exit("유튜브 로그인이 안 돼 있어요. --import-cookies 로 쿠키를 넣어 주세요.")
    main_mp4 = next(V.glob("*_narrated.mp4"))
    main_url = upload(page, main_mp4, meta, V / "thumbnail.jpg")
    if not main_url:
        r = find_video(page, meta["title"])
        log("find_video main:", r)
        if r:
            main_url = f"https://youtu.be/{r['id']}"
    log("MAIN", main_url)
    shorts_url = ""
    if (V / "shorts.mp4").exists():
        shorts_url = upload(page, V / "shorts.mp4", meta["shorts"], None)
        if not shorts_url:
            r = find_video(page, meta["shorts"]["title"])
            log("find_video shorts:", r)
            if r:
                shorts_url = f"https://youtu.be/{r['id']}"
        log("SHORTS", shorts_url)
        try:
            comment(page, shorts_url, meta.get("shorts_comment", "본편 👉 {main_url}").replace("{main_url}", main_url))
            log("COMMENT ok")
        except Exception as e:
            log("comment skip", e)
    (V / "upload_result.json").write_text(json.dumps({"main_url": main_url, "shorts_url": shorts_url}, ensure_ascii=False, indent=2), "utf-8")
    ctx.close()
