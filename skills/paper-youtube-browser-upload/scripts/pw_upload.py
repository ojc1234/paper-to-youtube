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
    return p.chromium.launch_persistent_context(
        a.profile, headless=not a.headful, locale="ko-KR", viewport={"width": 1400, "height": 1000},
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def set_box(page, idx, text):
    page.evaluate("""([i,t]) => {const b=[...document.querySelectorAll('ytcp-uploads-dialog #textbox')][i]; b.focus();
      document.execCommand('selectAll',false,null); document.execCommand('insertText',false,t);
      b.dispatchEvent(new Event('input',{bubbles:true}));}""", [idx, text])


def upload(page, mp4: Path, m: dict, thumb: Path | None):
    page.goto(f"https://studio.youtube.com/channel/{CHANNEL}/videos/upload?d=ud", wait_until="domcontentloaded")
    page.wait_for_selector("input[type=file]", state="attached", timeout=60000)
    page.set_input_files("input[type=file]", str(mp4))
    log("file set", mp4.name)
    page.wait_for_function("document.querySelectorAll('ytcp-uploads-dialog #textbox').length>=2", timeout=120000)
    time.sleep(2)
    set_box(page, 0, m["title"][:100])
    set_box(page, 1, m["description"][:4900])
    page.click("tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_NOT_MFK]")
    if thumb and thumb.exists():
        try:
            page.set_input_files("ytcp-thumbnail-uploader input[type=file]", str(thumb), timeout=15000)
            time.sleep(3)
        except Exception as e:
            log("thumbnail skip", e)
    try:
        page.click("#toggle-button", timeout=10000); time.sleep(1.5)
        tag = page.locator("input[aria-label*='태그'], input[aria-label*='Tags'], #tags-container input").first
        tag.fill(",".join(m.get("tags", [])) + ","); tag.press("Enter")
    except Exception as e:
        log("tags skip", e)
    try:
        page.click("ytcp-form-select#category ytcp-dropdown-trigger", timeout=8000); time.sleep(1)
        page.evaluate("""() => {const it=[...document.querySelectorAll('tp-yt-paper-item')].find(e=>/과학기술|Science/.test(e.innerText)); it&&it.click()}""")
    except Exception as e:
        log("category skip", e)
    for _ in range(3):
        page.click("#next-button"); time.sleep(2)
    page.click(f"tp-yt-paper-radio-button[name={a.privacy}]"); time.sleep(1)
    link = page.evaluate("""() => [...document.querySelectorAll('ytcp-uploads-dialog a')].map(a=>a.href)
        .find(h=>/youtu\\.be\\/|youtube\\.com\\/shorts\\//.test(h)) || ''""")
    log("link", link)
    # 업로드(처리 전 전송)가 끝날 때까지 기다린 뒤 게시
    for _ in range(180):
        st = page.evaluate("(document.querySelector('ytcp-video-upload-progress')?.innerText||'')")
        if not re.search(r"업로드 중|Uploading", st):
            break
        time.sleep(5)
    page.click("#done-button"); time.sleep(8)
    return link


def comment(page, url, text):
    vid = re.search(r"(?:shorts/|youtu\.be/|v=)([\w-]{11})", url).group(1)
    page.goto(f"https://www.youtube.com/watch?v={vid}", wait_until="domcontentloaded"); time.sleep(6)
    for _ in range(6):
        page.mouse.wheel(0, 900); time.sleep(2)
        if page.locator("#simplebox-placeholder").count():
            break
    page.click("#simplebox-placeholder"); time.sleep(1.5)
    page.evaluate("t => {const e=document.querySelector('#contenteditable-root'); e.focus(); document.execCommand('insertText',false,t)}", text)
    time.sleep(1); page.click("ytd-commentbox #submit-button"); time.sleep(5)


def logged_in(page):
    page.goto(f"https://studio.youtube.com/channel/{CHANNEL}", wait_until="domcontentloaded")
    time.sleep(5)
    return "accounts.google.com" not in page.url and "studio.youtube.com" in page.url


with sync_playwright() as p:
    ctx = launch(p)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
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
    log("MAIN", main_url)
    shorts_url = ""
    if (V / "shorts.mp4").exists():
        shorts_url = upload(page, V / "shorts.mp4", meta["shorts"], None)
        log("SHORTS", shorts_url)
        try:
            comment(page, shorts_url, meta.get("shorts_comment", "본편 👉 {main_url}").replace("{main_url}", main_url))
            log("COMMENT ok")
        except Exception as e:
            log("comment skip", e)
    (V / "upload_result.json").write_text(json.dumps({"main_url": main_url, "shorts_url": shorts_url}, ensure_ascii=False, indent=2), "utf-8")
    ctx.close()
