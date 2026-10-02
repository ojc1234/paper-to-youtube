#!/usr/bin/env python3
"""Download an arXiv paper's TeX source (e-print) and unpack it into paper_src/.

Stdlib-only. Usage:
    python download_source.py <arxiv-url|arxiv-id> [--output DIR] [--force]
"""

import argparse
import gzip
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
URL_PREFIX_RE = re.compile(r"(?:export\.)?arxiv\.org/(?:abs|pdf|e-print)/")
EPRINT_URL = "https://arxiv.org/e-print/{id}"


def parse_ref(ref: str) -> str | None:
    """Return the normalized arXiv ID (with version suffix) or None."""
    m = ID_RE.search(ref)
    if m is None:
        return None
    prefix, suffix = ref[: m.start()], ref[m.end():]
    if prefix == "" and suffix == "":
        return m.group(0)
    prefix = re.sub(r"^https?://", "", prefix)
    if URL_PREFIX_RE.fullmatch(prefix) and suffix == "":
        return m.group(0)
    return None


def download(id_: str, dest: Path) -> tuple[bytes, str]:
    """Fetch the e-print payload. Returns (body, content_type). Exits 1 on failure."""
    url = EPRINT_URL.format(id=id_)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type", "")
            if len(data) == 0:
                raise RuntimeError("empty response body")
            return data, content_type
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as exc:
            last_error = exc
            if attempt == 1:
                time.sleep(2)
                continue
    print(f"ERROR: download failed for {id_}: {last_error}", file=sys.stderr)
    sys.exit(1)


def detect(data: bytes) -> tuple[str, bytes]:
    """Classify the payload. Returns (kind, payload_to_write).

    kinds: archive (tar/tar.gz, written verbatim) | pdf | tex | unknown.
    A gzip payload that is not a tar is a gzipped single file: arXiv serves
    some old single-file submissions gzip-compressed, so decompress and
    re-classify it (tex/pdf) instead of treating it as an archive.
    """
    if data[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(data)
        except OSError:
            return "unknown", data
        if len(raw) > 262 and raw[257:262] == b"ustar":
            return "archive", data  # tar.gz: keep the gzipped archive verbatim
        data = raw  # gzipped single file — classify the decompressed content
    if data[:4] == b"%PDF":
        return "pdf", data
    if len(data) > 262 and data[257:262] == b"ustar":
        return "archive", data  # uncompressed tar
    head = data[:4096].lstrip()
    if head.startswith(b"\\documentclass") or b"\\begin{document}" in head:
        return "tex", data
    return "unknown", data


def extract(archive: Path, paper_src: Path) -> None:
    """Extract the archive into paper_src/, flattening a single top-level dir.

    Rejects unsafe members (absolute paths, '..'). Skips symlinks and non-file
    members. Exits 1 on any archive error.
    """
    paper_src.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, mode="r:*") as tf:
            members = tf.getmembers()
    except (tarfile.TarError, OSError) as exc:
        print(f"ERROR: cannot open archive {archive}: {exc}", file=sys.stderr)
        sys.exit(1)

    normalized: list[tuple[tarfile.TarInfo, str]] = []
    for m in members:
        name = m.name.replace("\\", "/")
        while name.startswith("./"):
            name = name[2:]
        if name in ("", "."):
            continue
        parts = Path(name).parts
        if m.name.startswith("/") or ".." in parts:
            print(f"ERROR: unsafe archive member: {m.name}", file=sys.stderr)
            sys.exit(1)
        normalized.append((m, name))

    top_entries = {Path(name).parts[0] for _, name in normalized}
    # Flatten only when exactly one top-level entry exists AND it is a directory.
    flatten = False
    if len(top_entries) == 1:
        top = next(iter(top_entries))
        flatten = any(m.isdir() and Path(name).parts[0] == top for m, name in normalized)

    with tarfile.open(archive, mode="r:*") as tf:
        for m, name in normalized:
            if not (m.isfile() or m.isdir()):
                continue
            if flatten:
                name = name[len(next(iter(top_entries))) + 1:]
                if not name:
                    continue
            m.name = name
            try:
                if sys.version_info >= (3, 12):
                    # data filter rejects unsafe members; on older Pythons the
                    # manual validation above (no '..', no absolute paths) covers it.
                    tf.extract(m, path=str(paper_src), set_attrs=False, filter="data")
                else:
                    tf.extract(m, path=str(paper_src), set_attrs=False)
            except (tarfile.TarError, OSError) as exc:
                print(f"ERROR: cannot extract {name}: {exc}", file=sys.stderr)
                sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download an arXiv paper's TeX source (e-print) and unpack it into paper_src/."
    )
    parser.add_argument(
        "ref",
        help="arXiv URL (abs/pdf/e-print, arxiv.org or export.arxiv.org) or bare ID, e.g. 2509.07996v4",
    )
    parser.add_argument(
        "--output",
        default=".",
        help="output directory (default: current working directory)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even if the archive already exists",
    )
    args = parser.parse_args(argv)

    id_ = parse_ref(args.ref)
    if id_ is None:
        print(f"ERROR: cannot parse arXiv reference: {args.ref}", file=sys.stderr)
        return 1

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    archive = output / f"arXiv-{id_}.tar.gz"
    if archive.is_file() and archive.stat().st_size > 0 and not args.force:
        print(f"SKIP {archive} (already downloaded; use --force to re-download)")
    else:
        data, content_type = download(id_, archive)
        kind, payload = detect(data)
        if kind == "archive":
            archive.write_bytes(payload)
            print(f"DOWNLOADED {archive}")
        elif kind == "pdf":
            pdf = output / f"arXiv-{id_}.pdf"
            pdf.write_bytes(payload)
            print(f"PDF_ONLY: {pdf}")
            return 0
        elif kind == "tex":
            tex = output / f"arXiv-{id_}.tex"
            tex.write_bytes(payload)
            print(f"DOWNLOADED {tex} (single-file TeX submission)")
            return 0
        else:
            print(
                f"ERROR: unexpected arXiv payload (content-type={content_type}, "
                f"magic={data[:8].hex()})",
                file=sys.stderr,
            )
            return 1

    paper_src = output / "paper_src"
    extract(archive, paper_src)
    tex_files = sorted(str(p.relative_to(paper_src)) for p in paper_src.rglob("*.tex"))
    if not tex_files:
        print("ERROR: archive contains no .tex files", file=sys.stderr)
        return 1
    print("TEX_SOURCES: " + ", ".join(tex_files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
