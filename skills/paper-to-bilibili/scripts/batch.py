#!/usr/bin/env python3
"""Deterministic disk-state orchestrator for the paper-to-Bilibili workflow."""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
SLIDES_CLI = SKILL_ROOT / "paper-slides-to-video" / "scripts" / "slides_to_video.py"
PORTRAIT_CLI = (
    Path.home()
    / ".omp"
    / "agent"
    / "managed-skills"
    / "rednote-video-uploader"
    / "scripts"
    / "portrait_video.py"
)
UPLOAD_CLI = SKILL_ROOT / "paper-bilibili-uploader" / "scripts" / "upload.py"
RETAINED_PYTHON = Path(os.environ.get("PAPER_PYTHON") or sys.executable)
BV_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")


def load_papers(path: str | Path) -> list[dict]:
    papers_path = Path(path)
    try:
        papers = json.loads(papers_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read papers file {papers_path}: {exc}") from exc
    if not isinstance(papers, list) or any(not isinstance(paper, dict) for paper in papers):
        raise ValueError("papers.json must contain a JSON array of objects")
    for index, paper in enumerate(papers):
        if not isinstance(paper.get("dir_path"), str) or not paper["dir_path"].strip():
            raise ValueError(f"paper {index} has no nonempty dir_path")
    return papers


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _valid_meta(meta_path: Path) -> tuple[bool, dict | None]:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, None
    if not isinstance(meta, dict):
        return False, None
    if not isinstance(meta.get("title"), str) or not meta["title"].strip():
        return False, meta
    tags = meta.get("tag")
    if (
        not isinstance(tags, list)
        or not tags
        or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
    ):
        return False, meta
    tid = meta.get("tid")
    if isinstance(tid, bool) or not isinstance(tid, int) or tid <= 0:
        return False, meta
    copyright_value = meta.get("copyright")
    if isinstance(copyright_value, bool) or copyright_value not in (1, 2):
        return False, meta
    if copyright_value == 2:
        source = meta.get("source")
        if not isinstance(source, str) or not source.strip():
            return False, meta
    return True, meta


def _valid_upload_result(path: Path) -> bool:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(result, dict) or result.get("status") != "uploaded":
        return False
    bv = result.get("bv")
    if not isinstance(bv, str) or BV_PATTERN.fullmatch(bv) is None:
        return False
    if result.get("url") != f"https://www.bilibili.com/video/{bv}":
        return False
    uploaded_at = result.get("uploaded_at")
    if not isinstance(uploaded_at, str) or not uploaded_at:
        return False
    try:
        datetime.fromisoformat(uploaded_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _source_exists(paper: dict, paper_dir: Path) -> bool:
    configured = paper.get("pdf_path")
    if isinstance(configured, str) and configured and _nonempty(Path(configured)):
        return True
    arxiv_id = paper.get("arxiv_id")
    if isinstance(arxiv_id, str) and arxiv_id and _nonempty(paper_dir / f"{arxiv_id}.pdf"):
        return True
    if any(_nonempty(path) for path in (paper_dir / "paper_src").rglob("*.tex")):
        return True
    return any(_nonempty(path) for path in paper_dir.glob("*.pdf"))


def _mineru_exists(paper: dict, paper_dir: Path) -> bool:
    arxiv_id = paper.get("arxiv_id")
    if isinstance(arxiv_id, str) and arxiv_id:
        expected = paper_dir / "md_output" / arxiv_id / "auto" / f"{arxiv_id}.md"
        if _nonempty(expected):
            return True
    return any(_nonempty(path) for path in (paper_dir / "md_output").glob("*/auto/*.md"))


def scan_disk_state(papers: list[dict]) -> list[dict]:
    """Apply the release contract's exact, highest-evidence-first precedence."""
    for paper in papers:
        paper_dir = Path(paper["dir_path"])
        video_dir = paper_dir / "video"
        slides = paper_dir / "slides-beamer" / "main.pdf"
        annotated = video_dir / "main_with_narration.tex"
        landscape = [
            path
            for path in video_dir.glob("*.mp4")
            if "_portrait_narrated" not in path.name and _nonempty(path)
        ]
        portrait = [
            path
            for path in video_dir.glob("*.mp4")
            if "_portrait_narrated" in path.name and _nonempty(path)
        ]
        meta_ok, meta = _valid_meta(video_dir / "video_meta.json")
        cover_rel = meta.get("cover_path", "cover.png") if meta else "cover.png"
        cover = video_dir / cover_rel if isinstance(cover_rel, str) and cover_rel else video_dir / "cover.png"

        if _valid_upload_result(video_dir / "upload_result.json"):
            status = "uploaded"
        elif landscape and portrait and _nonempty(cover) and meta_ok:
            status = "upload_ready"
        elif landscape:
            status = "video_done"
        elif _nonempty(annotated) and _nonempty(slides):
            status = "narrations_done"
        elif _nonempty(slides):
            status = "slides_done"
        elif _mineru_exists(paper, paper_dir):
            status = "mineru_done"
        elif _source_exists(paper, paper_dir):
            status = "pdf_downloaded"
        else:
            status = "pending"
        paper["_status"] = status
    return papers


def persist_papers(path: str | Path, papers: list[dict]) -> None:
    """Atomically replace papers.json in its own directory."""
    papers_path = Path(path)
    temporary = papers_path.with_name(f".{papers_path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(papers, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, papers_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def rescan_and_persist(papers_path: str | Path, papers: list[dict]) -> list[dict]:
    previous = [paper.get("_status") for paper in papers]
    scan_disk_state(papers)
    persist_papers(papers_path, papers)
    for paper, old_status in zip(papers, previous):
        new_status = paper["_status"]
        if old_status and old_status != new_status:
            name = paper.get("dir_name") or Path(paper["dir_path"]).name
            print(f"STATE {name}: {old_status} -> {new_status}")
    return papers


def _run_child(command: list[str], timeout: float | None = None) -> int:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return result.returncode


def _ensure_cli(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"required sibling CLI is missing: {path}")


def _promoted_command(script: Path, *args: str) -> list[str]:
    # Run sibling CLIs with the interpreter that launched this orchestrator.
    # Dependencies are declared in each skill's pyproject.toml; install them
    # with `uv sync --project <SKILLS_DIR>/<skill>` (or pip install -e).
    return [str(RETAINED_PYTHON), str(script), *args]


def complete_video(paper: dict, papers: list[dict], papers_path: Path) -> int:
    status = paper["_status"]
    paper_dir = Path(paper["dir_path"])
    annotated = paper_dir / "video" / "main_with_narration.tex"

    if status in ("uploaded", "upload_ready"):
        return 0
    if status == "slides_done":
        print(f"ERROR: {paper_dir} has slides but no annotated narration TeX", file=sys.stderr)
        return 1
    if status == "narrations_done":
        _ensure_cli(SLIDES_CLI)
        command = _promoted_command(
            SLIDES_CLI,
            "full",
            str(paper_dir),
            "--annotated-tex",
            str(annotated),
        )
        returncode = _run_child(command)
        if returncode != 0:
            return returncode
        rescan_and_persist(papers_path, papers)
        status = paper["_status"]
        if status not in ("video_done", "upload_ready"):
            print(f"ERROR: video CLI succeeded but disk state is {status}", file=sys.stderr)
            return 1

    if status == "video_done":
        _ensure_cli(PORTRAIT_CLI)
        if not RETAINED_PYTHON.is_file():
            raise FileNotFoundError(f"retained Rednote Python is missing: {RETAINED_PYTHON}")
        returncode = _run_child([str(RETAINED_PYTHON), str(PORTRAIT_CLI), str(paper_dir)])
        if returncode != 0:
            return returncode
        rescan_and_persist(papers_path, papers)
        if paper["_status"] != "upload_ready":
            print(
                f"ERROR: portrait CLI succeeded but disk state is {paper['_status']}",
                file=sys.stderr,
            )
            return 1
    return 0


def upload_paper(
    paper: dict,
    papers: list[dict],
    papers_path: Path,
    dry_run: bool,
    upload_timeout: float | None,
) -> int:
    status = paper["_status"]
    if status == "uploaded":
        return 0
    if status != "upload_ready":
        print(
            f"ERROR: {paper['dir_path']} is {status}, not upload_ready",
            file=sys.stderr,
        )
        return 1
    _ensure_cli(UPLOAD_CLI)
    command = _promoted_command(UPLOAD_CLI, paper["dir_path"])
    if dry_run:
        command.append("--dry-run")
    returncode = _run_child(command, timeout=upload_timeout)
    if returncode != 0:
        return returncode
    rescan_and_persist(papers_path, papers)
    expected = "upload_ready" if dry_run else "uploaded"
    if paper["_status"] != expected:
        print(
            f"ERROR: uploader succeeded but disk state is {paper['_status']}; expected {expected}",
            file=sys.stderr,
        )
        return 1
    return 0


def print_dashboard(papers: list[dict], mode: str) -> None:
    counts = Counter(paper["_status"] for paper in papers)
    print(f"=== {mode.upper()} ===")
    for status in (
        "pending",
        "pdf_downloaded",
        "mineru_done",
        "slides_done",
        "narrations_done",
        "video_done",
        "upload_ready",
        "uploaded",
    ):
        if counts[status]:
            print(f"  {status}: {counts[status]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("slides", "video", "bilibili"))
    parser.add_argument("papers", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate Bilibili upload commands without invoking biliup",
    )
    parser.add_argument(
        "--upload-timeout",
        type=float,
        default=None,
        help="Optional uploader timeout in seconds; omitted means no deadline",
    )
    args = parser.parse_args(argv)

    try:
        papers = load_papers(args.papers)
        rescan_and_persist(args.papers, papers)
        print_dashboard(papers, args.mode)
        if args.mode == "slides":
            return 0
        if args.mode == "video":
            for paper in papers:
                returncode = complete_video(paper, papers, args.papers)
                if returncode != 0:
                    return returncode
            print_dashboard(papers, args.mode)
            return 0
        for paper in papers:
            returncode = upload_paper(
                paper,
                papers,
                args.papers,
                dry_run=args.dry_run,
                upload_timeout=args.upload_timeout,
            )
            if returncode != 0:
                return returncode
        print_dashboard(papers, args.mode)
        return 0
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
