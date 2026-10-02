"""BrowserSkill(bsk) 로 YouTube Studio 에 본편+쇼츠 업로드하고 쇼츠에 본편 링크 댓글 작성.
usage: python bsk_upload.py <paper_dir> [--privacy PUBLIC|UNLISTED|PRIVATE]
"""
import subprocess, json, sys, time, re, argparse
from pathlib import Path

BSK = r"C:\Users\ojc12\bsk\bsk.exe"
ap = argparse.ArgumentParser()
ap.add_argument("paper_dir"); ap.add_argument("--privacy", default="PUBLIC")
a = ap.parse_args()
V = Path(a.paper_dir) / "video"
meta = json.loads((V / "youtube_meta.json").read_text(encoding="utf-8"))
main_mp4 = next(p for p in V.glob("*_narrated.mp4"))

def bsk(*args, timeout=180):
    r = subprocess.run([BSK, *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return (r.stdout + r.stderr).strip()

S = bsk("session", "start", "--quiet", "--name", "youtube-upload").splitlines()[-1].strip()
print("session", S)
ev = lambda js: bsk("evaluate", "--session", S, js)
wait = lambda ms: bsk("wait-ms", str(ms))

def set_box(idx, text):
    js = """(() => {const b=[...document.querySelectorAll('ytcp-uploads-dialog #textbox')][%d]; b.focus();
    document.execCommand('selectAll',false,null); document.execCommand('insertText',false,%s);
    b.dispatchEvent(new Event('input',{bubbles:true})); return b.innerText.length;})()""" % (idx, json.dumps(text, ensure_ascii=False))
    return ev(js)

def upload(mp4, m, thumb=None):
    print(bsk("navigate", "--session", S, "https://studio.youtube.com/channel/UCSPGgP1LHjpvbihHkKRnAiQ/videos/upload?d=ud"))
    wait(6000)
    print(bsk("upload", "--session", S, "--selector", "#select-files-button", "--file", str(mp4)))
    for _ in range(20):
        wait(2000)
        if "2" in ev("String(document.querySelectorAll('ytcp-uploads-dialog #textbox').length)"): break
    print("title", set_box(0, m["title"]), "desc", set_box(1, m["description"]))
    print(ev("(() => {const r=document.querySelector('tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_NOT_MFK]'); r&&r.click(); return !!r})()"))
    if thumb:
        print(bsk("upload", "--session", S, "--selector", "ytcp-thumbnail-uploader button#select-button", "--file", str(thumb)))
        wait(3000)
    ev("(() => {const t=document.querySelector('#toggle-button'); t&&t.click(); return !!t})()"); wait(2000)
    print(bsk("fill", "--session", S, "--selector", "input[aria-label*=태그]", "--value", ",".join(m["tags"]) + ","))
    bsk("press", "--session", S, "Enter")
    bsk("click", "--session", S, "--selector", "ytcp-form-select#category ytcp-dropdown-trigger"); wait(1000)
    ev("(() => {const it=[...document.querySelectorAll('tp-yt-paper-item')].find(e=>e.innerText.trim()==='과학기술'); it&&it.click(); return !!it})()"); wait(800)
    print("check:", ev("[...document.querySelectorAll('ytcp-uploads-dialog #textbox')].map(e=>e.innerText.slice(0,40)).join(' || ') + ' | tags:' + [...document.querySelectorAll('ytcp-chip')].map(c=>c.innerText.trim()).join(',') + ' | cat:' + document.querySelector('ytcp-form-select#category').innerText.trim()"))
    for _ in range(3):
        bsk("click", "--session", S, "--selector", "#next-button"); wait(2000)
    res = ev("(() => {const r=document.querySelector('tp-yt-paper-radio-button[name=%s]'); r&&r.click(); return JSON.stringify({ok:!!r, link:[...document.querySelectorAll('ytcp-uploads-dialog a')].map(a=>a.href).find(h=>/youtu\\.be\\/|youtube\\.com\\/shorts\\//.test(h))})})()" % a.privacy)
    print("vis:", res)
    link = json.loads(json.loads(res) if res.startswith('"') else res)["link"]
    wait(1000)
    print(bsk("click", "--session", S, "--selector", "#done-button")); wait(6000)
    print("dialog:", ev("(document.querySelector('ytcp-video-share-dialog, tp-yt-paper-dialog[opened]')?.innerText||'none').replace(/\\s+/g,' ').slice(0,120)"))
    return link

try:
    main_url = upload(main_mp4, meta, V / "thumbnail.jpg")
    print("MAIN", main_url)
    shorts_url = upload(V / "shorts.mp4", meta["shorts"])
    print("SHORTS", shorts_url)
    vid = re.search(r"(?:shorts/|youtu\.be/)([\w-]+)", shorts_url).group(1)
    bsk("navigate", "--session", S, f"https://www.youtube.com/watch?v={vid}"); wait(6000)
    for _ in range(4):
        ev("window.scrollBy(0,800)"); wait(2000)
        if "true" in ev("String(!!document.querySelector('#simplebox-placeholder'))"): break
    bsk("click", "--session", S, "--selector", "#simplebox-placeholder"); wait(1500)
    comment = meta["shorts_comment"].replace("{main_url}", main_url)
    bsk("fill", "--session", S, "--selector", "#contenteditable-root", "--value", comment); wait(1000)
    bsk("click", "--session", S, "--selector", "ytd-commentbox #submit-button"); wait(5000)
    print("COMMENT", ev("[...document.querySelectorAll('#content-text')].map(e=>e.innerText).slice(0,2).join(' || ')"))
    (V / "upload_result.json").write_text(json.dumps({"main_url": main_url, "shorts_url": shorts_url}, ensure_ascii=False, indent=2), encoding="utf-8")
finally:
    print(bsk("session", "stop", S))
