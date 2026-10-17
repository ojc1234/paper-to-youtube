"""SoundCloud 주소 → 가사 자막이 달린 음악 영상 → 유튜브 업로드 (LLM 없이 결정적으로 처리)

단계: 음원 받기(yt-dlp) → 가사(직접 입력 > lrclib.net 싱크 가사 > faster-whisper 받아쓰기)
      → ASS 자막 + 앨범아트 배경으로 ffmpeg 렌더 → pw_upload.py / bsk_upload.py 로 업로드
"""
import difflib, json, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "paper-to-youtube-music/1.0 (https://github.com/ojc1234/paper-to-youtube)"}
WHISPER_PY = os.environ.get("P2Y_WHISPER_PY", os.path.expanduser("~/.local/share/p2y-whisper/bin/python"))
FONT = os.environ.get("P2Y_LYRIC_FONT", "NanumGothic")
FONTS_DIR = os.path.expanduser("~/.local/share/fonts")

MUSIC_STAGES = [
    ("download", "음원 받기", 60),
    ("lyrics", "가사 찾기 · 싱크 맞추기", 200),
    ("render", "가사 영상 렌더링", 300),
    ("upload", "유튜브 업로드", 300),
]
SC_RE = re.compile(r"^https?://(www\.|m\.|on\.)?soundcloud\.com/\S+$")


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode:
        raise RuntimeError(f"{Path(cmd[0]).name} 실패: {(r.stderr or r.stdout)[-600:]}")
    return r.stdout


# ---------------------------------------------------------------- 1. 음원
def download(url: str, d: Path) -> dict:
    ytdlp = shutil.which("yt-dlp") or "yt-dlp"
    run([ytdlp, "--no-playlist", "-x", "--audio-format", "mp3", "--audio-quality", "0",
         "--write-info-json", "--write-thumbnail", "--convert-thumbnails", "jpg",
         "-o", str(d / "audio.%(ext)s"), url])
    info = json.loads((d / "audio.info.json").read_text("utf-8"))
    title = info.get("track") or info.get("title") or "untitled"
    artist = info.get("artist") or info.get("uploader") or ""
    m = re.match(r"^\s*(.+?)\s+[-–—]\s+(.+)$", title)  # "Artist - Song" 형식이면 나눈다
    if m and not info.get("artist"):  # 재업로드 곡은 업로더 대신 제목 앞부분이 진짜 아티스트
        artist, title = m.group(1), m.group(2)
    clean = re.sub(r"[\(\[](official|audio|free\s*download|lyrics?|mv|m/v|prod[^)\]]*)[^)\]]*[\)\]]", "", title, flags=re.I).strip()
    return {"title": clean or title, "raw_title": info.get("title", ""), "artist": artist.strip(),
            "duration": float(info.get("duration") or 0), "url": info.get("webpage_url", url),
            "uploader": info.get("uploader", ""), "genre": info.get("genre", ""),
            "audio": str(d / "audio.mp3"), "cover": str(d / "audio.jpg") if (d / "audio.jpg").exists() else ""}


# ---------------------------------------------------------------- 2. 가사
TAG = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(text: str) -> list:
    out = []
    for ln in text.splitlines():
        tags = TAG.findall(ln)
        if not tags:
            continue
        body = TAG.sub("", ln).strip()
        for mm, ss in tags:
            out.append((int(mm) * 60 + float(ss), body))
    return sorted(out)


def lrclib(meta: dict):
    def get(params):
        req = urllib.request.Request("https://lrclib.net/api/search?" + urllib.parse.urlencode(params), headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    title, artist, dur = meta["title"], meta["artist"], meta["duration"]
    tries = [{"track_name": title, "artist_name": artist}] if artist else []
    tries += [{"q": f"{artist} {title}".strip()}, {"track_name": title}]
    best, best_s = None, 0.0
    for p in tries:
        try:
            cands = get(p)
        except Exception:
            continue
        for c in cands:
            if not c.get("syncedLyrics"):
                continue
            if dur and c.get("duration") and abs(c["duration"] - dur) > 8:
                continue
            s = difflib.SequenceMatcher(None, title.lower(), (c.get("trackName") or "").lower()).ratio()
            if artist:
                s += 0.5 * difflib.SequenceMatcher(None, artist.lower(), (c.get("artistName") or "").lower()).ratio()
            if s > best_s:
                best, best_s = c, s
        if best and best_s > 0.9:
            break
    if best and best_s >= 0.55:
        return parse_lrc(best["syncedLyrics"]), f"lrclib.net ({best.get('artistName')} - {best.get('trackName')})"
    return None, ""


def whisper(meta: dict, d: Path) -> list:
    if not Path(WHISPER_PY).exists():
        return []
    out = d / "whisper.json"
    run([WHISPER_PY, str(HERE / "whisper_lyrics.py"), meta["audio"], str(out)])
    return json.loads(out.read_text("utf-8"))["segments"]


def align_plain(lines: list, segs: list, duration: float) -> list:
    """타임스탬프 없는 가사 줄을, 받아쓰기로 찾은 '노래가 나오는 구간'에 글자 수 비율로 배치."""
    spans = [(s["start"], s["end"]) for s in segs] or [(duration * 0.08, duration * 0.92)]
    total = sum(e - s for s, e in spans)
    chars = [max(len(l), 2) for l in lines]
    acc, out, tot_c = 0, [], sum(chars)
    for l, c in zip(lines, chars):
        pos = total * acc / tot_c
        for s, e in spans:
            if pos <= e - s:
                out.append((s + pos, l))
                break
            pos -= e - s
        acc += c
    return out


def get_lyrics(job_lyrics: str, meta: dict, d: Path, rep) -> tuple:
    txt = (job_lyrics or "").strip()
    if txt and TAG.search(txt):
        rep.ev("explain", "직접 넣어 주신 싱크 가사(LRC)를 그대로 씁니다.")
        return parse_lrc(txt), "직접 입력 (LRC)"
    if txt:
        rep.ev("explain", "직접 넣어 주신 가사에 시간이 없어서, 노래에서 목소리가 나오는 구간을 받아쓰기로 찾아 줄마다 시간을 맞춥니다.")
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        return align_plain(lines, whisper(meta, d), meta["duration"]), "직접 입력 (자동 싱크)"
    rep.ev("explain", f"lrclib.net 에서 「{meta['artist']} - {meta['title']}」 의 싱크 가사를 찾는 중이에요.")
    lrc, src = lrclib(meta)
    if lrc:
        rep.ev("explain", f"싱크 가사를 찾았어요: {src}, {sum(1 for _, t in lrc if t)}줄.")
        return lrc, src
    rep.ev("explain", "공개 가사 DB 에 없어요. faster-whisper 로 노래를 직접 받아 적어서 가사를 만듭니다 (몇 분 걸려요).")
    segs = whisper(meta, d)
    if segs:
        return [(s["start"], s["text"]) for s in segs] + [(segs[-1]["end"], "")], "자동 받아쓰기 (faster-whisper, 틀릴 수 있음)"
    return [], "없음 (연주곡)"


# ---------------------------------------------------------------- 3. 렌더
def ts(t: float) -> str:
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def ass_esc(s: str) -> str:
    return s.replace("\\", "＼").replace("{", "(").replace("}", ")").replace("\n", " ")


CX = 1345  # 오른쪽 글자 영역의 가운데 x (앨범아트는 왼쪽 150~770)


def make_ass(lyrics: list, meta: dict, path: Path, thumb=False):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cur,{FONT},66,&H00FFFFFF,&H00FFFFFF,&H64000000,&H00000000,1,0,0,0,100,100,0,0,1,3,1,5,820,60,0,1
Style: Near,{FONT},44,&H00C8C8C8,&H00FFFFFF,&H50000000,&H00000000,0,0,0,0,100,100,0,0,1,2.5,0,5,820,60,0,1
Style: Title,{FONT},{90 if thumb else 50},&H00FFFFFF,&H00FFFFFF,&H78000000,&H00000000,1,0,0,0,100,100,0,0,1,3,1,8,820,60,0,1
Style: Artist,{FONT},{60 if thumb else 36},&H00C8C8C8,&H00FFFFFF,&H78000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,820,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    end = meta["duration"] or 600
    ev = []
    if thumb:
        ev.append(f"Dialogue: 0,{ts(0)},{ts(10)},Title,,0,0,0,,{{\\pos({CX},380)}}{ass_esc(meta['title'])}")
        ev.append(f"Dialogue: 0,{ts(0)},{ts(10)},Artist,,0,0,0,,{{\\pos({CX},560)}}{ass_esc(meta['artist'])}")
        ev.append(f"Dialogue: 0,{ts(0)},{ts(10)},Artist,,0,0,0,,{{\\pos({CX},680)\\c&H5C5CFF&}}가사 · Lyrics")
    else:
        ev.append(f"Dialogue: 0,{ts(0)},{ts(end)},Title,,0,0,0,,{{\\pos({CX},110)}}{ass_esc(meta['title'])}")
        ev.append(f"Dialogue: 0,{ts(0)},{ts(end)},Artist,,0,0,0,,{{\\pos({CX},180)}}{ass_esc(meta['artist'])}")
        L = [(t, x) for t, x in lyrics]
        for i, (s, text) in enumerate(L):
            if not text:
                continue
            e = L[i + 1][0] if i + 1 < len(L) else min(s + 6, end)
            e = min(e, s + 12)  # 긴 간주에는 가사를 내린다
            if e - s < 0.3:
                continue
            ev.append(f"Dialogue: 1,{ts(s)},{ts(e)},Cur,,0,0,0,,{{\\pos({CX},560)\\fad(150,120)}}{ass_esc(text)}")
            prev = next((x for _, x in reversed(L[:i]) if x), "")
            nxt = next((x for _, x in L[i + 1:] if x), "")
            if prev:
                ev.append(f"Dialogue: 0,{ts(s)},{ts(e)},Near,,0,0,0,,{{\\pos({CX},420)\\alpha&H60&}}{ass_esc(prev)}")
            if nxt:
                ev.append(f"Dialogue: 0,{ts(s)},{ts(e)},Near,,0,0,0,,{{\\pos({CX},700)}}{ass_esc(nxt)}")
    path.write_text(head + "\n".join(ev) + "\n", "utf-8")


def render(meta: dict, lyrics: list, d: Path) -> Path:
    V = d / "video"
    V.mkdir(exist_ok=True)
    ff = shutil.which("ffmpeg") or "ffmpeg"
    cover = meta["cover"]
    if not cover:
        cover = str(d / "cover.png")
        run([ff, "-y", "-f", "lavfi", "-i", "gradients=s=1000x1000:c0=0x1e1b4b:c1=0x7c2d12:d=1", "-frames:v", "1", cover])
    bg = d / "bg.png"
    run([ff, "-y", "-i", cover, "-filter_complex",
         "[0:v]scale=1920:1920:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=45,eq=brightness=-0.42:saturation=1.1[b];"
         "[0:v]scale=620:620:force_original_aspect_ratio=increase,crop=620:620[a];[b][a]overlay=150:230",
         "-frames:v", "1", str(bg)])
    make_ass(lyrics, meta, d / "lyrics.ass")
    make_ass([], meta, d / "thumb.ass", thumb=True)
    fd = FONTS_DIR.replace(":", "\\:")
    run([ff, "-y", "-i", str(bg), "-vf", f"ass={d / 'thumb.ass'}:fontsdir={fd},scale=1280:720", "-frames:v", "1", "-q:v", "2",
         str(V / "thumbnail.jpg")])
    out = V / "music_narrated.mp4"  # pw_upload.py 는 *_narrated.mp4 를 본편으로 올린다
    run([ff, "-y", "-loop", "1", "-framerate", "24", "-i", str(bg), "-i", meta["audio"],
         "-vf", f"ass={d / 'lyrics.ass'}:fontsdir={fd},format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "22", "-r", "24",
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)])
    return out


def write_meta(meta: dict, lyrics: list, src: str, d: Path):
    plain = "\n".join(t for _, t in lyrics if t)
    head = f"{meta['artist']} - {meta['title']}" if meta["artist"] else meta["title"]
    tag_art = re.sub(r"\W+", "", meta["artist"]) or "music"
    desc = (f"🎵 {head}\n원곡 (SoundCloud): {meta['url']}\n\n"
            f"가사 출처: {src}\n이 영상은 원곡에 가사 자막을 붙인 영상입니다. 모든 권리는 원작자에게 있습니다.\n\n"
            + (f"[가사 / Lyrics]\n{plain}\n\n" if plain else "")
            + f"#lyrics #가사 #{tag_art}")
    ym = {"title": f"{head} (가사/Lyrics)"[:100], "description": desc[:4900],
          "tags": [x for x in [meta["artist"], meta["title"], "가사", "lyrics", "soundcloud", meta.get("genre")] if x][:12]}
    (d / "video" / "youtube_meta.json").write_text(json.dumps(ym, ensure_ascii=False, indent=2), "utf-8")
    return ym


# ---------------------------------------------------------------- 실행
def run_music(job: dict, d: Path, rep, upload_cmd: list):
    m = job.get("music") or {}
    url = m.get("url") or job.get("filename", "")
    if not SC_RE.match(url):
        raise RuntimeError("SoundCloud 주소가 아니에요")
    d.mkdir(parents=True, exist_ok=True)
    rep.stage("download", "SoundCloud 에서 음원과 앨범아트를 받는 중")
    meta = download(url, d)
    (d / "music.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    rep.ev("explain", f"「{meta['artist']} - {meta['title']}」 ({int(meta['duration'] // 60)}분 {int(meta['duration'] % 60)}초) 을 받았어요."
           + ("" if meta["cover"] else " 앨범아트가 없어서 그라데이션 배경을 씁니다."))
    rep.stage("lyrics")
    lyrics, src = get_lyrics(m.get("lyrics", ""), meta, d, rep)
    (d / "lyrics.json").write_text(json.dumps({"source": src, "lines": lyrics}, ensure_ascii=False, indent=1), "utf-8")
    rep.stage("render", "앨범아트를 흐리게 깐 배경 위에 현재 줄·앞뒤 줄 가사를 띄워 1080p 영상으로 굽는 중")
    t0 = time.time()
    out = render(meta, lyrics, d)
    ym = write_meta(meta, lyrics, src, d)
    rep.ev("explain", f"영상을 만들었어요 ({out.stat().st_size / 1e6:.0f}MB, {int(time.time() - t0)}초). 제목: {ym['title']}")
    rep.stage("upload", f"유튜브 스튜디오에 {job.get('privacy', 'PUBLIC')} 로 올리는 중")
    r = subprocess.run(upload_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    (d / "upload.log").write_text(r.stdout + "\n" + r.stderr, "utf-8")
    res_f = d / "video" / "upload_result.json"
    if not res_f.exists():
        raise RuntimeError(f"업로드 실패 (exit {r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    res = json.loads(res_f.read_text("utf-8"))
    res.update({"title": ym["title"], "paper": f"🎵 {meta['artist']} - {meta['title']}", "lyrics_source": src})
    return res


if __name__ == "__main__":  # 업로드 없이 렌더까지만 시험: python music.py <soundcloud url> <out dir> [lyrics.txt]
    class P:
        def stage(self, k, t=""): print("==", k, t)
        def ev(self, k, t, d=None): print("  ", t)
    dd = Path(sys.argv[2]); dd.mkdir(parents=True, exist_ok=True)
    meta = download(sys.argv[1], dd)
    ly, src = get_lyrics(Path(sys.argv[3]).read_text("utf-8") if len(sys.argv) > 3 else "", meta, dd, P())
    print(render(meta, ly, dd), src, write_meta(meta, ly, src, dd)["title"])
