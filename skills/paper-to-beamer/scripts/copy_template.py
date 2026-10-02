#!/usr/bin/env python3
"""Copy the SUSTech Beamer template into a destination directory.

Template source priority:
1. The canonical group template repo `<TEMPLATE_DIR>`
   (branch `lab-survey`) — the maintained source of truth (theme, latexmkrc,
   templates, avatar assets). Always used when present.
2. The packaged snapshot under templates/sustech/ — offline fallback only.

The packaged snapshot goes stale (missing \sustechscheme/\setlogo, the
official-bilibili-pink-blue scheme, labsurvey_avatar.png, and the $ENV-based
latexmkrc fix); the repo copy is the current one. See
skill://lab-survey-new-template-deck for the sync checklist.

`<TEMPLATE_DIR>` is resolved from the `TEMPLATE_DIR` environment variable, or
auto-detected from the skills tree (two levels above the skills dir).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PACKAGED_ROOT = PACKAGE_ROOT / "templates" / "sustech"
# Canonical group template repo (env `TEMPLATE_DIR` wins; else auto-detect
# from the skills tree):
#   <SKILLS_DIR>/<skill>/scripts/.. = <SKILLS_DIR> → <SKILLS_DIR>/../.. = <TEMPLATE_DIR>
SKILLS_ROOT = Path(__file__).resolve().parents[2]
_env_template_dir = os.environ.get("TEMPLATE_DIR")
if _env_template_dir:
    REPO_ROOT = Path(_env_template_dir)
else:
    REPO_ROOT = SKILLS_ROOT.parent.parent / "sustech-slides-template"
FILE_ASSETS = ("main_template.tex", "latexmkrc")
THEME_ASSET = "sustech-theme"
THEME_FILES = (
    "beamerthemesustech.sty",
    "beamercolorthemesustech.sty",
    "beamerthemesustech-elements.sty",
)


def _choose_root() -> tuple[Path, Path]:
    """Return (source_root, label). Repo wins when complete, else packaged."""
    repo_ok = all((REPO_ROOT / name).is_file() for name in FILE_ASSETS)
    repo_ok = repo_ok and (REPO_ROOT / THEME_ASSET).is_dir()
    repo_ok = repo_ok and all(
        (REPO_ROOT / THEME_ASSET / name).is_file() for name in THEME_FILES
    )
    if repo_ok:
        return REPO_ROOT, f"group template repo {REPO_ROOT}"
    return PACKAGED_ROOT, f"packaged template {PACKAGED_ROOT}"


def copy_template(output: Path, force: bool = False) -> None:
    src, label = _choose_root()
    required = [src / name for name in FILE_ASSETS]
    required.extend(src / THEME_ASSET / name for name in THEME_FILES)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing template assets: " + ", ".join(missing))
    if not (src / THEME_ASSET).is_dir():
        raise NotADirectoryError(f"theme is not a directory: {src / THEME_ASSET}")

    if output.exists() and not output.is_dir():
        raise NotADirectoryError(f"output exists and is not a directory: {output}")
    if output.exists() and any(output.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {output}; pass --force to overwrite template assets")

    output.mkdir(parents=True, exist_ok=True)
    for name in FILE_ASSETS:
        shutil.copy2(src / name, output / name)
    shutil.copytree(
        src / THEME_ASSET,
        output / THEME_ASSET,
        dirs_exist_ok=force,
    )
    print(f"Copied SUSTech template from {label} to {output.resolve()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Destination directory")
    parser.add_argument("--force", action="store_true", help="Overwrite packaged assets in a nonempty destination")
    args = parser.parse_args(argv)

    try:
        copy_template(args.output.resolve(), args.force)
    except (OSError, shutil.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
