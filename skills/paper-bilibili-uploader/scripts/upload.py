#!/usr/bin/env python3
"""
Bilibili upload CLI — read video_meta.json, upload MP4 + cover via Python biliup v1.2+.

Usage:
  python scripts/upload.py <paper_dir>      # upload from video_meta.json
  python scripts/upload.py --login          # interactive QR-code login

Requires: pip install biliup>=1.2
Cookie store: %USERPROFILE%\\.bilibili\\cookies.json
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import requests

COOKIE_DIR = Path.home() / ".bilibili"
COOKIE_PATH = COOKIE_DIR / "cookies.json"


def login():
    """Interactive QR-code login."""
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "biliup", "-u", str(COOKIE_PATH), "login"]
    print("Opening QR-code login in terminal. Scan with Bilibili app.")
    print(f"Cookies saved to: {COOKIE_PATH}")
    subprocess.run(cmd, check=True)

def _lock_dir() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    return Path(local_app_data) / "biliup" / "locks" if local_app_data else None


def _biliup_processes() -> list[psutil.Process]:
    processes = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            command = " ".join(process.info.get("cmdline") or [])
            if "biliup" in command.lower():
                processes.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return processes


def _lock_holders(lock_path: Path) -> list[psutil.Process]:
    holders = []
    resolved = lock_path.resolve()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if any(Path(open_file.path).resolve() == resolved for open_file in process.open_files()):
                holders.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return holders


def diagnose():
    """Diagnose biliup lock state and running processes without WMIC or fsutil."""
    lock_dir = _lock_dir()

    print("=" * 60)
    print("BILIUP DIAGNOSTIC")
    print("=" * 60)
    print("\n── Lock Files ──")
    if lock_dir is None:
        print("  LOCALAPPDATA is unset; lock directory is unavailable")
    elif lock_dir.is_dir():
        locks = sorted(lock_dir.glob("*.lock"))
        if not locks:
            print("  (no lock files)")
        for lock_path in locks:
            match = re.match(r"biliup_upload_(\d+)\.lock", lock_path.name)
            uid = match.group(1) if match else "?"
            holders = _lock_holders(lock_path)
            if holders:
                for process in holders:
                    print(
                        f"  [ACTIVE] {lock_path.name} (UID={uid}) — held by "
                        f"PID {process.pid} ({process.name()})"
                    )
            else:
                print(f"  [STALE]  {lock_path.name} (UID={uid}) — safe to remove")
    else:
        print(f"  Lock dir not found: {lock_dir}")

    print("\n── Running Biliup Processes ──")
    processes = _biliup_processes()
    if not processes:
        print("  (no biliup processes found)")
    for process in processes:
        try:
            command = " ".join(process.cmdline())
            port_match = re.search(r"--port\s+(\d+)", command)
            port = port_match.group(1) if port_match else "?"
            health = "?"
            if port != "?":
                try:
                    response = urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/", timeout=2
                    )
                    health = f"HTTP {response.status}"
                except Exception:
                    health = "no response"
            print(f"  PID {process.pid}  port={port}  health={health}")
            print(f"    cmd: {command[:120]}")
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

    print("\n── Recent Upload Log (download.log) ──")
    log_path = COOKIE_DIR / "download.log"
    if log_path.is_file():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            biliup_lines = [line for line in lines if "biliup_cli" in line]
            for line in biliup_lines[-15:]:
                print(f"  {line[:150]}")
            if len(biliup_lines) > 15:
                print(f"  ... ({len(biliup_lines)} total biliup_cli log lines)")
        except OSError as exc:
            print(f"  Error reading log: {exc}")
    else:
        print(f"  No download.log at {log_path}")
    print()


def kill_all(force: bool = False):
    """Kill biliup processes and clean lock files not held by a live process."""
    print("=" * 60)
    print("BILIUP KILL-ALL")
    print("=" * 60)
    diagnose()

    if not force:
        print("This will kill all biliup processes and remove stale lock files.")
        response = input("Proceed? [y/N] ").strip().lower()
        if response not in ("y", "yes"):
            print("Aborted.")
            return
        print()

    print("── Killing biliup processes ──")
    killed = 0
    for process in _biliup_processes():
        try:
            print(f"  Killing PID {process.pid}...")
            process.kill()
            process.wait(timeout=5)
            print("    Killed")
            killed += 1
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.TimeoutExpired) as exc:
            print(f"    Failed: {exc}")
    if killed == 0:
        print("  (no biliup processes to kill)")
    else:
        print(f"  Killed {killed} process(es)")

    print("\n── Cleaning lock files ──")
    lock_dir = _lock_dir()
    cleaned = 0
    if lock_dir is None:
        print("  LOCALAPPDATA is unset; no lock files inspected")
    elif lock_dir.is_dir():
        for lock_path in sorted(lock_dir.glob("*.lock")):
            holders = _lock_holders(lock_path)
            if holders and not force:
                print(f"  Skipped (active): {lock_path.name}")
                continue
            try:
                lock_path.unlink()
                print(f"  Removed: {lock_path.name}")
                cleaned += 1
            except OSError as exc:
                print(f"  Failed to remove {lock_path.name}: {exc}")
    if cleaned == 0:
        print("  (no removable lock files)")
    else:
        print(f"  Cleaned {cleaned} lock file(s)")
    print("\nDone.")



def upload_cover_image(cookies: dict, bili_jct: str, cover_path: str) -> str:
    """Upload a cover image via the member web cover endpoint.

    Returns the returned URL (already without scheme prefix, matching how
    biliup stores covers). Crop to 16:10 like biliup's cover_up, base64
    data-URI in the 'cover' field + csrf.
    """
    import base64
    from io import BytesIO

    from PIL import Image

    with Image.open(cover_path) as im:
        xsize, ysize = im.size
        if xsize / ysize > 1.6:
            delta = xsize - ysize * 1.6
            region = im.crop((delta / 2, 0, xsize - delta / 2, ysize))
        else:
            delta = ysize - xsize * 10 / 16
            region = im.crop((0, delta / 2, xsize, ysize - delta / 2))
        buffered = BytesIO()
        region.save(buffered, format="PNG")
        b64 = base64.b64encode(buffered.getvalue())
        buffered.close()

    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://member.bilibili.com/",
    })
    resp = session.post(
        "https://member.bilibili.com/x/vu/web/cover/up",
        data={"cover": b"data:image/png;base64," + b64, "csrf": bili_jct},
        timeout=30,
    )
    result = resp.json()
    data = result.get("data") or {}
    url = data.get("url")
    if result.get("code") != 0 or not url:
        raise RuntimeError(f"cover upload failed: {result}")
    # Return the URL exactly as the member cover endpoint returns it — the
    # web edit endpoint accepts it verbatim; stripping the scheme makes the
    # edit reject it as "封面必须由页面进行上传，不支持外部链接".
    return url



def edit_video_metadata(bv: str, tid: int = None, title: str = None,
                         tags: str = None, desc: str = None,
                         cover: str = None) -> bool:
    """Edit an already-uploaded video's metadata via Bilibili web edit API.

    Uses the member.bilibili.com web edit endpoint directly, which supports
    changing partition (tid), title, tags, description, and cover without
    re-uploading video files. `cover` is a local image path — it is uploaded
    via the member cover endpoint first and the returned URL is submitted.
    """
    if not COOKIE_PATH.exists():
        print(f"ERROR: Cookie file not found at {COOKIE_PATH}")
        return False

    with open(COOKIE_PATH, "r", encoding="utf-8") as f:
        cookie_data = json.load(f)

    cookies = {}
    bili_jct = None
    for c in cookie_data["cookie_info"]["cookies"]:
        cookies[c["name"]] = c["value"]
        if c["name"] == "bili_jct":
            bili_jct = c["value"]

    if not bili_jct:
        print("ERROR: bili_jct not found in cookies. Re-login with --login.")
        return False

    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://member.bilibili.com/",
        "Origin": "https://member.bilibili.com",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    # Fetch metadata from BOTH APIs:
    # - Public API for full desc/dynamic (member API truncates desc to ~250 chars)
    # - Member API for video filenames (public API only has cid+part)
    session.headers.update({"Referer": "https://www.bilibili.com/"})
    public_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bv}"
    try:
        resp = session.get(public_url, timeout=10)
        if resp.status_code != 200:
            print(f"ERROR: Failed to fetch video metadata (HTTP {resp.status_code})")
            return False
        public_data = resp.json()
        if public_data.get("code") != 0:
            print(f"ERROR: Public API error: {public_data.get('message', public_data)}")
            return False
    except requests.RequestException as e:
        print(f"ERROR: Network error fetching metadata: {e}")
        return False

    session.headers.update({"Referer": "https://member.bilibili.com/"})
    member_url = f"https://member.bilibili.com/x/client/archive/view?bvid={bv}"
    try:
        resp = session.get(member_url, timeout=10)
        if resp.status_code != 200:
            print(f"ERROR: Failed to fetch video metadata (HTTP {resp.status_code})")
            return False
        member_data = resp.json()
        if member_data.get("code") != 0:
            print(f"ERROR: Member API error: {member_data.get('message', member_data)}")
            return False
    except requests.RequestException as e:
        print(f"ERROR: Network error fetching metadata: {e}")
        return False

    pub = public_data["data"]
    mem = member_data["data"]["archive"]
    mem_videos = member_data["data"]["videos"]

    # Build edit payload: use public API for desc/dynamic (full text),
    # member API for video filenames
    cover_url = mem.get("cover", "")
    if cover:
        try:
            cover_url = upload_cover_image(cookies, bili_jct, cover)
            print(f"  封面已上传: {cover_url}")
        except Exception as exc:
            print(f"ERROR: cover upload failed: {exc}")
            return False
    payload = {
        "aid": pub["aid"],
        "copyright": pub.get("copyright", 1),
        "tid": tid if tid is not None else pub["tid"],
        "title": (title[:80] if title else pub["title"]),
        "desc": desc if desc is not None else pub.get("desc", ""),
        "desc_format_id": mem.get("desc_format_id", 0),
        "tag": tags if tags is not None else mem.get("tag", ""),
        "cover": cover_url,
        "source": mem.get("source", ""),
        "no_reprint": mem.get("no_reprint", 0),
        "dolby": mem.get("dolby", 0),
        "hires": mem.get("hires", 0),
        "is_only_self": mem.get("is_only_self", 0),
        "charging_pay": mem.get("charging_pay", 0),
        "dtime": mem.get("dtime"),
        "videos": [
            {
                "title": v["title"],
                "filename": v["filename"],
                "desc": v.get("desc", ""),
                "cid": v["cid"],
            }
            for v in mem_videos
        ],
    }

    # Submit edit
    edit_url = f"https://member.bilibili.com/x/vu/web/edit?csrf={bili_jct}"
    try:
        resp = session.post(edit_url, json=payload, timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            print(f"稿件修改成功: https://www.bilibili.com/video/{bv}")
            if tid is not None:
                print(f"  分区已更新为: tid={tid}")
            if title is not None:
                print(f"  标题已更新")
            if tags is not None:
                print(f"  标签已更新为: {tags}")
            if desc is not None:
                print(f"  简介已更新")
            return True
        else:
            print(f"ERROR: Edit failed: {result.get('message', result)}")
            return False
    except requests.RequestException as e:
        print(f"ERROR: Network error during edit: {e}")
        return False

def _is_lab_survey(paper_dir: Path) -> bool:
    """True for 课题组调研 (实验室调研) dirs — marked by poster_data.json,
    which only the lab-survey poster pipeline writes."""
    return (paper_dir / "poster_data.json").is_file()


def _find_cover(video_dir: Path, meta: dict, paper_dir: Path = None) -> str:
    """Resolve the required nonempty cover file from video metadata.

    Lab-survey policy (poster_data.json present): the paper-video-cover
    POSTER PNG is still a COMPULSORY ARTIFACT — <paper_dir>/poster/poster.png
    must exist (fail-closed generation gate) — but the UPLOAD cover now
    defaults to the deck's FIRST SLIDE (video/cover.png, rendered by the
    video pipeline from the title page). Explicit meta.cover_path wins;
    otherwise video/cover.png. The poster no longer overrides the first-slide
    cover. For all other papers: explicit cover_path -> video/cover.png.
    series_mode callers pass paper_dir=None (lab check off)."""
    if paper_dir is not None and _is_lab_survey(paper_dir):
        poster = paper_dir / "poster" / "poster.png"
        if not poster.is_file() or poster.stat().st_size <= 0:
            raise SystemExit(
                f"ERROR: 实验室调研 dirs still require the POSTER artifact — missing "
                f"{poster}. Generate it with generate_lab_poster.py (poster_data.json present).")
    cover_rel = meta.get("cover_path")  # absent key = no explicit override
    if isinstance(cover_rel, str) and cover_rel:
        explicit = video_dir / cover_rel
        if explicit.is_file() and explicit.stat().st_size > 0:
            return str(explicit)
    cover_path = video_dir / "cover.png"
    if not cover_path.is_file() or cover_path.stat().st_size <= 0:
        raise SystemExit(f"ERROR: Missing or empty cover image: {cover_path}")
    return str(cover_path)


def validate_meta(meta: dict) -> None:
    """Validate the Bilibili metadata contract."""
    if not isinstance(meta, dict):
        raise ValueError("metadata must be a JSON object")
    if not isinstance(meta.get("title"), str) or not meta["title"].strip():
        raise ValueError("title must be a nonempty string")
    tags = meta.get("tag")
    if (
        not isinstance(tags, list)
        or not tags
        or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
    ):
        raise ValueError("tag must be a nonempty list of nonempty strings")
    tid = meta.get("tid")
    if isinstance(tid, bool) or not isinstance(tid, int) or tid <= 0:
        raise ValueError("tid must be a positive integer")
    copyright_value = meta.get("copyright")
    if isinstance(copyright_value, bool) or copyright_value not in (1, 2):
        raise ValueError("copyright must be 1 (original) or 2 (reprint)")
    if copyright_value == 2:
        source = meta.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source is required when copyright is 2")


def _nonempty_files(paths) -> list[Path]:
    return [path for path in paths if path.is_file() and path.stat().st_size > 0]


def resolve_inputs(paper_dir: str, series_mode: bool = False) -> tuple:
    """Resolve ordered MP4s, required cover, and metadata."""
    video_dir = (
        Path(paper_dir) / "bilibili-series" / "video"
        if series_mode
        else Path(paper_dir) / "video"
    )
    meta_path = video_dir / "video_meta.json"
    if not meta_path.is_file():
        raise SystemExit(
            f"ERROR: No video_meta.json at {meta_path}. Run paper-slides-to-video first."
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR: Invalid video metadata at {meta_path}: {exc}") from exc

    all_mp4s = _nonempty_files(sorted(video_dir.glob("*.mp4"), key=lambda path: path.name))
    if series_mode:
        if not all_mp4s:
            raise SystemExit(f"ERROR: No nonempty MP4 found in {video_dir}")
        mp4_paths = [str(path) for path in all_mp4s]
    else:
        landscape_mp4s = [
            path for path in all_mp4s if "_portrait_narrated" not in path.name
        ]
        portrait_mp4s = [
            path for path in all_mp4s if "_portrait_narrated" in path.name
        ]
        if not landscape_mp4s:
            raise SystemExit("ERROR: No nonempty landscape MP4 found for P1 upload.")
        if not portrait_mp4s:
            raise SystemExit(
                "ERROR: No nonempty portrait MP4 found for P2 upload. "
                "Run rednote-video-uploader/scripts/portrait_video.py first."
            )
        mp4_paths = [str(path) for path in landscape_mp4s + portrait_mp4s]

    return mp4_paths, _find_cover(video_dir, meta, None if series_mode else Path(paper_dir)), meta

def build_command(mp4_paths: list[str], cover_path: str, meta: dict) -> list:
    """Build biliup upload command from metadata.
    Accepts multiple MP4 paths for multi-part upload (P1, P2, ...).
    """
    cmd = [
        sys.executable, "-m", "biliup",
        "-u", str(COOKIE_PATH),
        "upload",
    ]
    # Add all MP4 paths as positional args
    cmd.extend(mp4_paths)
    cmd.extend([
        "--title", meta["title"][:80],
        "--tid", str(meta.get("tid", 231)),
        "--tag", ",".join(meta.get("tag", [])[:10]),
        "--desc", meta.get("desc", ""),
        "--copyright", str(meta.get("copyright", 1)),
        "--no-reprint", str(meta.get("no_reprint", 0)),
        "--submit", "web",
        "--line", "cnbd",
        "--limit", "1",
    ])

    if cover_path:
        cmd.extend(["--cover", cover_path])

    if meta.get("copyright") == 2 and meta.get("source"):
        cmd.extend(["--source", meta["source"]])

    dynamic = meta.get("dynamic", "")
    if dynamic:
        cmd.extend(["--dynamic", dynamic[:256]])

    dtime = meta.get("dtime")
    if dtime:
        cmd.extend(["--dtime", str(dtime)])

    return cmd


def parse_dtime(value: str) -> int:
    """Parse a Unix timestamp or ``YYYY-MM-DD HH:MM`` interpreted in CST."""
    if value.isdigit() and len(value) == 10:
        return int(value)
    cst = timezone(timedelta(hours=8))
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=cst)
    except ValueError as exc:
        raise SystemExit(
            f"ERROR: Invalid dtime format '{value}'. Use 'YYYY-MM-DD HH:MM' "
            "(CST) or a 10-digit Unix timestamp."
        ) from exc
    return int(parsed.timestamp())


def get_dtime(now=None) -> int | None:
    """At 23:00–23:59 CST, schedule publication for 07:00 CST the next day."""
    cst = timezone(timedelta(hours=8))
    if now is None:
        now = datetime.now(cst)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=cst)
    else:
        now = now.astimezone(cst)
    if now.hour < 23:
        return None
    publish_at = (now + timedelta(days=1)).replace(
        hour=7, minute=0, second=0, microsecond=0
    )
    return int(publish_at.timestamp())


def _patch_meta_for_multi_part(meta: dict, mp4_paths: list[str], series_mode: bool = False):
    """Prepend part info to description for multi-part uploads.
    Normal: P1 横屏（适合电脑）  P2 竖屏（适合手机）
    Series: Filenames joined as-is (e.g. "P1 总览  P2 真机  ...")
    Idempotent — skips if already present.
    """
    if len(mp4_paths) < 2:
        return

    if series_mode:
        # Filenames are already like "P1 总览与分类框架.mp4" — use stem directly
        parts = [Path(p).stem for p in mp4_paths]
        series_line = "  ".join(parts)
        if series_line in meta.get("desc", ""):
            return
        meta["desc"] = f"{series_line}\n\n{meta.get('desc', '')}"
    else:
        P1P2_LINE = "P1 横屏（适合电脑）  P2 竖屏（适合手机）"
        if P1P2_LINE in meta.get("desc", ""):
            return
        meta["desc"] = f"{P1P2_LINE}\n\n{meta.get('desc', '')}"

def upload(
    paper_dir: str,
    extra_args: list[str] | None = None,
    dtime: str | None = None,
    series_mode: bool = False,
    dry_run: bool = False,
):
    """Validate and upload a paper; dry-run never starts biliup."""
    mp4_paths, cover_path, meta = resolve_inputs(paper_dir, series_mode=series_mode)
    _patch_meta_for_multi_part(meta, mp4_paths, series_mode=series_mode)

    if dtime is not None:
        meta["dtime"] = parse_dtime(dtime)
    elif not meta.get("dtime"):
        scheduled = get_dtime()
        if scheduled is not None:
            meta["dtime"] = scheduled

    try:
        validate_meta(meta)
    except ValueError as exc:
        raise SystemExit(f"ERROR: Invalid video_meta.json: {exc}") from exc

    is_multi = len(mp4_paths) > 1
    print(f"=== Bilibili Upload {'(multi-part)' if is_multi else ''} ===")
    print(f"Title:   {meta['title'][:60]}...")
    print(f"Tags:    {meta['tag']}")
    for index, path in enumerate(mp4_paths):
        label = f"P{index + 1}" if is_multi else "File"
        print(f"{label}:     {path} ({os.path.getsize(path) / 1024 / 1024:.1f} MB)")
    print(f"Cover:   {cover_path}")
    if meta.get("dtime"):
        cst = timezone(timedelta(hours=8))
        publish_at = datetime.fromtimestamp(int(meta["dtime"]), tz=cst)
        print(f"Publish: {publish_at.strftime('%Y-%m-%d %H:%M')} (CST)")
    print()

    command = build_command(mp4_paths, cover_path, meta)
    if extra_args:
        command.extend(extra_args)
    print("BILIUP COMMAND:")
    print(" \\\n  ".join(f'"{value}"' if " " in value else value for value in command))
    print()

    if dry_run:
        print("DRY RUN: metadata and command validated; biliup was not invoked.")
        return {"command": command, "meta": meta, "mp4_paths": mp4_paths}

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 and "413" in (result.stderr or ""):
        print("413 on cnbd line, retrying with auto-probed CDN...")
        stripped = []
        index = 0
        while index < len(command):
            if command[index] in ("--line", "--limit") and index + 1 < len(command):
                index += 2
            else:
                stripped.append(command[index])
                index += 1
        result = subprocess.run(stripped, capture_output=True, text=True)

    print("STDOUT:", result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    print(f"EXIT: {result.returncode}")
    output = (result.stdout or "") + (result.stderr or "")

    if result.returncode != 0:
        lowered = output.lower()
        if any(keyword in lowered for keyword in ["login", "cookie", "expired", "鉴权", "credential"]):
            print(f"\nAUTH ERROR: Cookie expired. Re-login: {sys.executable} {Path(__file__)} --login")
            raise SystemExit(2)
        if any(keyword in lowered for keyword in ["already exists", "已存在", "duplicate"]):
            print("\nDUPLICATE: Video may already exist. Check your uploads page.")
            raise SystemExit(3)
        if any(keyword in lowered for keyword in ["too large", "过大", "exceeds"]):
            print("\nSIZE ERROR: File exceeds Bilibili size limit.")
            raise SystemExit(4)
        if any(keyword in lowered for keyword in ["过于频繁", "过快"]):
            print("\nRATE LIMIT: Upload too frequent. Wait and retry.")
            raise SystemExit(5)
        raise SystemExit(f"UPLOAD FAILED (exit {result.returncode})")

    match = re.search(r"BV[0-9A-Za-z]{10}", output)
    if match is None:
        raise SystemExit("ERROR: biliup exited successfully but returned no BV identifier")
    bv = match.group(0)
    upload_result = {
        "status": "uploaded",
        "bv": bv,
        "url": f"https://www.bilibili.com/video/{bv}",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    result_dir = (
        Path(paper_dir) / "bilibili-series" / "video"
        if series_mode
        else Path(paper_dir) / "video"
    )
    result_path = result_dir / "upload_result.json"
    result_path.write_text(
        json.dumps(upload_result, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"\nBV号: {bv}")
    print(f"URL:  {upload_result['url']}")
    print(f"Upload successful ({len(mp4_paths)} parts)!")
    return upload_result

def main():
    parser = argparse.ArgumentParser(description="Bilibili video uploader")
    parser.add_argument("paper_dir", nargs="?", help="Paper directory with video/ subfolder")
    parser.add_argument("--login", action="store_true", help="Interactive QR-code login")
    parser.add_argument("--dtime", type=str, default=None, help="Delayed publish time: 'YYYY-MM-DD HH:MM' (CST) or 10-digit Unix timestamp; uploads at/after 23:00 default to 07:00 next day")
    parser.add_argument("--series", action="store_true", help="Series mode: use bilibili-series/video/ layout, no portrait required, N landscape parts bundled as single submission")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and validate without invoking biliup")
    parser.add_argument("--diagnose", action="store_true", help="Diagnose lock state and running processes")
    parser.add_argument("--kill-all", action="store_true", help="Kill biliup processes and clean stale locks")
    parser.add_argument("--force", action="store_true", help="Skip confirmation for --kill-all")
    parser.add_argument("--edit", action="store_true", help="Edit an already-uploaded video's metadata")
    parser.add_argument("--vid", type=str, default=None, help="BV号 of video to edit (for --edit)")
    parser.add_argument("--tid", type=int, default=None, help="New partition ID (for --edit). Common: 124=社科, 208=校园学习, 209=社科·法律, 210=人文历史, 231=计算机技术")
    parser.add_argument("--tag", dest="edit_tag", type=str, default=None, help="New tags, comma-separated (for --edit)")
    parser.add_argument("--title", dest="edit_title", type=str, default=None, help="New title (for --edit)")
    parser.add_argument("--desc", dest="edit_desc", type=str, default=None, help="New description (for --edit)")
    parser.add_argument("--cover", dest="edit_cover", type=str, default=None, help="New cover image path (for --edit); uploaded via member cover endpoint")
    args, extra = parser.parse_known_args()

    if args.edit:
        if not args.vid:
            print("ERROR: --edit requires --vid <BV号>")
            sys.exit(1)
        success = edit_video_metadata(
            bv=args.vid,
            tid=args.tid,
            title=args.edit_title,
            tags=args.edit_tag,
            desc=args.edit_desc,
            cover=args.edit_cover,
        )
        sys.exit(0 if success else 1)
    elif args.login:
        login()
    elif args.diagnose:
        diagnose()
    elif args.kill_all:
        kill_all(force=args.force)
    elif args.paper_dir:
        upload(
            args.paper_dir,
            extra,
            dtime=args.dtime,
            series_mode=args.series,
            dry_run=args.dry_run,
        )
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
