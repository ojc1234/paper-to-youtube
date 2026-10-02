#!/usr/bin/env python3
"""
generate_poster.py — CLI: paper_dir with md_output → video-cover poster (tex, pdf, png).

Usage:
  python generate_poster.py <paper_dir> [--data data.json] [--title-font N]

Auto-extracts title, venue, figures, and key metrics from MinerU markdown.
Gemini beamerposter theme, 16:10, 2 columns, result-card style.
"""
import argparse
import glob as _glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

# ═══════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
TEMPLATES_DIR = os.path.join(SKILL_DIR, "templates")
REQUIRED_TEMPLATES = ("beamerthemegemini.sty", "beamercolorthemesustech.sty")


def resolve_binary(explicit, env_name, command):
    """Resolve a required executable without silently bypassing an override."""
    if explicit is not None:
        candidate = explicit
        source = "command line"
    else:
        candidate = os.environ.get(env_name)
        source = env_name
    if candidate is not None:
        resolved = shutil.which(candidate)
        if resolved:
            return os.path.abspath(resolved)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
        raise FileNotFoundError(f"{command} from {source} was not found: {candidate}")

    resolved = shutil.which(command)
    if not resolved:
        raise FileNotFoundError(
            f"{command} was not found; use --{command}, set {env_name}, or add it to PATH"
        )
    return os.path.abspath(resolved)


def require_nonempty(path, label):
    """Raise when an expected output is absent or empty."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError(f"{label} is missing or empty: {path}")


def install_output(staged_dir, output_dir):
    """Atomically replace the designated output directory, with rollback."""
    output_dir = os.path.abspath(output_dir)
    parent = os.path.dirname(output_dir)
    backup_dir = None
    try:
        if os.path.lexists(output_dir):
            backup_dir = tempfile.mkdtemp(prefix=".poster-backup-", dir=parent)
            os.rmdir(backup_dir)
            os.replace(output_dir, backup_dir)
        os.replace(staged_dir, output_dir)
    except Exception:
        if backup_dir and not os.path.lexists(output_dir) and os.path.lexists(backup_dir):
            os.replace(backup_dir, output_dir)
        raise
    if backup_dir:
        try:
            if os.path.isdir(backup_dir) and not os.path.islink(backup_dir):
                shutil.rmtree(backup_dir)
            else:
                os.remove(backup_dir)
        except OSError as exc:
            print(f"WARNING: could not remove output backup {backup_dir}: {exc}", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════
# Markdown parser
# ═══════════════════════════════════════════════════════════════

def find_md_file(paper_dir):
    """Find the MinerU markdown file in md_output/*/auto/."""
    pattern = os.path.join(paper_dir, "md_output", "*", "auto", "*.md")
    matches = _glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(f"No .md found in md_output/*/auto/ under {paper_dir}")
    return matches[0]


def read_md(path):
    """Read markdown file, stripping NUL bytes and handling encoding issues."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    return content.replace("\0", "")


def _strip_chinese(text):
    """Remove Chinese characters and punctuation, keeping only ASCII/Latin content."""
    # Remove CJK characters + Chinese punctuation
    text = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3000-\u303f\uff00-\uffef：，。、；？！""《》（）【】]", "", text)
    # Clean up whitespace left by removal
    text = re.sub(r"\s{2,}", " ", text).strip()
    # Remove leading colons/dashes left after stripping
    text = re.sub(r"^[\s:：\-–—]+", "", text).strip()
    return text


def extract_title(md_text):
    """Extract title from first # heading, stripping Chinese characters."""
    for line in md_text.split("\n"):
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            # Strip HTML tags
            title = re.sub(r"<[^>]+>", "", line[2:]).strip()
            return _strip_chinese(title)
    return "Unknown Title"


def extract_venue(paper_dir):
    """Infer venue from directory name."""
    basename = os.path.basename(os.path.normpath(paper_dir))
    # e.g., "CVPR 2025 - GROVE" → "CVPR 2025"
    match = re.match(r"([A-Z]+)\s*(\d{4})", basename)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return basename


def extract_project_url(md_text):
    """Extract first HTTP URL from the markdown header (first 20 lines)."""
    for line in md_text.split("\n")[:20]:
        match = re.search(r"https?://[^\s)\]]+", line)
        if match:
            return match.group(0).rstrip(".")
    return ""


def extract_figures(md_text, images_dir):
    """
    Find Figure N captions and map to image files.
    Returns list of (figure_number, image_filename, caption_text, file_path).
    """
    # Find all image references with their context
    # Pattern: ![](images/HASH.jpg) followed by or near "Fig. N" / "Figure N"
    figures = []
    lines = md_text.split("\n")

    for i, line in enumerate(lines):
        # Match image reference
        img_match = re.search(r"!\[\]\(images/([^)]+)\)", line)
        if img_match:
            img_file = img_match.group(1)
            img_path = os.path.join(images_dir, img_file)
            if not os.path.exists(img_path):
                continue

            # Look for Figure N in this line or next 3 lines
            fig_num = None
            caption = ""
            for j in range(i, min(i + 4, len(lines))):
                cap_match = re.search(r"(?:Fig(?:ure)?)\.?\s*(\d+)", lines[j], re.IGNORECASE)
                if cap_match:
                    fig_num = int(cap_match.group(1))
                    caption = lines[j].strip()
                    break

            if fig_num:
                figures.append((fig_num, img_file, caption, img_path))

    # Sort by figure number, deduplicate
    seen = set()
    result = []
    for f in sorted(figures, key=lambda x: x[0]):
        if f[0] not in seen:
            seen.add(f[0])
            result.append(f)
    return result


def extract_key_metrics(md_text):
    """
    Extract key quantitative results from the abstract and experiments.
    Returns list of metric lines for result cards.
    """
    # Focus on abstract and experiments sections
    sections = md_text.split("\n## ")
    relevant = []
    for sec in sections:
        if any(kw in sec.lower() for kw in ["abstract", "experiment", "result", "evaluation"]):
            relevant.append(sec)

    text = "\n".join(relevant[:3])  # abstract + first 2 experiment sections

    metrics = []

    # Find percentage improvements with context
    pct_patterns = re.findall(
        r"(\d+\.?\d*)\s*%\s*(higher|better|improvement|more|faster|increase|completion|boost)",
        text, re.IGNORECASE
    )
    for val, ctx in pct_patterns[:3]:
        pct = float(val)
        if pct > 1:
            metrics.append(f"+{val}\\% {ctx.lower()}")

    # Find multipliers
    mult_patterns = re.findall(
        r"(\d+\.?\d*)\s*[×x]\s*(faster|speedup|less|fewer|reduction)",
        text, re.IGNORECASE
    )
    for val, ctx in mult_patterns[:2]:
        metrics.append(f"{val}$\\times$ {ctx.lower()}")

    # Find standalone percentages in results context
    std_pcts = re.findall(
        r"(\d+\.?\d*)\s*%\s+(task\s+completion|success\s+rate|accuracy|naturalness)",
        text, re.IGNORECASE
    )
    for val, ctx in std_pcts[:3]:
        metrics.append(f"{val}\\% {ctx.lower()}")
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for m in metrics:
        normalized = re.sub(r"[+\\%$\s]+", "", m).lower()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(m)
    return unique[:6]


def find_source_figures(paper_dir):
    """Scan known source dirs for image files when markdown yields 0 figures.
    Returns list of (fig_name, rel_path) tuples, max 6."""
    SOURCE_DIRS = [
        os.path.join(paper_dir, "slides-beamer", "figures"),
        os.path.join(paper_dir, "paper-source", "image"),
        os.path.join(paper_dir, "source", "image"),
        os.path.join(paper_dir, "Figures"),
    ]
    # Also check md_output images (no caption matching needed)
    md_images_pattern = os.path.join(paper_dir, "md_output", "*", "auto", "images")
    for d in _glob.glob(md_images_pattern):
        SOURCE_DIRS.append(d)

    exts = {".png", ".jpg", ".jpeg", ".pdf"}
    found = []
    for src_dir in SOURCE_DIRS:
        if not os.path.isdir(src_dir):
            continue
        for fname in sorted(os.listdir(src_dir)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in exts:
                abs_path = os.path.join(src_dir, fname)
                rel_path = os.path.relpath(abs_path, paper_dir)
                found.append(rel_path)
        if found:
            break  # stop at first non-empty source dir

    # Limit to 6, label fig1..fig6
    result = []
    for i, rel_path in enumerate(found[:6]):
        result.append((f"fig{i+1}", rel_path))
    return result


def convert_to_jpg(src_path, dst_path, pdftoppm):
    """Convert an image to JPEG, rendering PDFs with the resolved pdftoppm."""
    ext = os.path.splitext(src_path)[1].lower()
    try:
        if ext in (".jpg", ".jpeg"):
            shutil.copy2(src_path, dst_path)
        elif ext == ".png":
            with Image.open(src_path) as img:
                img.convert("RGB").save(dst_path, "JPEG", quality=92)
        elif ext == ".pdf":
            with tempfile.TemporaryDirectory() as tmp:
                stem = os.path.join(tmp, "source")
                result = subprocess.run(
                    [pdftoppm, "-png", "-r", "300", "-singlefile", src_path, stem],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"pdftoppm failed for {src_path} with exit code "
                        f"{result.returncode}: {result.stderr.strip()[:400]}"
                    )
                png_path = stem + ".png"
                require_nonempty(png_path, "rendered source figure")
                with Image.open(png_path) as img:
                    img.convert("RGB").save(dst_path, "JPEG", quality=92)
        else:
            raise ValueError(f"unsupported figure format {ext}: {src_path}")
        require_nonempty(dst_path, "converted figure")
    except Exception:
        if os.path.exists(dst_path):
            os.remove(dst_path)
        raise


def _title_from_beamer(paper_dir, fallback):
    """Try to extract title from slides-beamer/main.tex when no md_output."""
    tex_path = os.path.join(paper_dir, "slides-beamer", "main.tex")
    if not os.path.exists(tex_path):
        return fallback
    try:
        with open(tex_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.search(r"\\title(?:\[.*?\])?\{(.+?)\}", line)
                if m:
                    title = m.group(1).strip()
                    # Strip common LaTeX formatting
                    title = re.sub(r"\\\w+\{.*?\}", "", title)
                    title = re.sub(r"\\\w+", "", title)
                    if title:
                        return title
    except Exception:
        pass
    return fallback


# ═══════════════════════════════════════════════════════════════
# Poster data builder
# ═══════════════════════════════════════════════════════════════

def build_poster_data(paper_dir, json_data=None):
    """Build the poster data dict from auto-extraction + optional JSON override."""
    try:
        md_path = find_md_file(paper_dir)
        md_text = read_md(md_path)
        images_dir = os.path.join(os.path.dirname(md_path), "images")
        has_md = True
    except FileNotFoundError:
        md_text = ""
        images_dir = ""
        has_md = False

    venue = extract_venue(paper_dir)

    title = extract_title(md_text) if has_md else _title_from_beamer(paper_dir, venue)
    if re.search(r"[\u4e00-\u9fff]", title):
        # CJK glyphs are ~1em wide: cap the headline font so the title fits.
        title_font = 110 if len(title) < 40 else 90
    else:
        title_font = 260 if len(title) < 60 else 200
    data = {
        "title": title,
        "venue": venue,
        "project_url": extract_project_url(md_text) if has_md else "",
        "title_font": title_font,
        "figures": {"col1": [], "col2": []},
        "cards_col1": [],
        "cards_col2": [],
        "figure_map": {},
    }
    figures = extract_figures(md_text, images_dir)
    selected = figures[:6]  # max 6 figures

    # Distribute: odd figs to col1, even to col2
    for i, (fnum, img_file, caption, img_path) in enumerate(selected):
        fig_name = f"fig{fnum}"
        data["figure_map"][fig_name] = os.path.relpath(img_path, paper_dir)
        if i % 2 == 0:
            data["figures"]["col1"].append(fig_name)
        else:
            data["figures"]["col2"].append(fig_name)


    # Fallback: if markdown extraction yielded 0 figures, scan source dirs
    if len(data["figure_map"]) == 0:
        source_figs = find_source_figures(paper_dir)
        for i, (fig_name, rel_path) in enumerate(source_figs):
            data["figure_map"][fig_name] = rel_path
            if i % 2 == 0:
                data["figures"]["col1"].append(fig_name)
            else:
                data["figures"]["col2"].append(fig_name)
    # Auto-metric extraction → cards
    metrics = extract_key_metrics(md_text)
    if metrics and len(data["figures"]["col1"]) > 0:
        mid = len(data["figures"]["col1"]) // 2
        first_fig = data["figures"]["col1"][0] if data["figures"]["col1"] else "fig1"

        data["cards_col1"].append({
            "title": "Key Metrics",
            "lines": [
                " \\textbullet{} ".join(metrics[:3]) if len(metrics) >= 3 else metrics[0] if metrics else "",
                " \\textbullet{} ".join(metrics[3:6]) if len(metrics) > 3 else "",
            ],
            "after": first_fig,
        })

    if len(data["figures"]["col2"]) > 0:
        data["cards_col2"].append({
            "title": "Venue",
            "lines": [venue, "See paper for full results"],
            "after": data["figures"]["col2"][0],
        })

    # JSON override
    if json_data:
        for key in ["title", "venue", "project_url", "title_font"]:
            if key in json_data:
                data[key] = json_data[key]
        if "figures" in json_data:
            data["figures"] = json_data["figures"]
        if "cards_col1" in json_data:
            data["cards_col1"] = json_data["cards_col1"]
        if "cards_col2" in json_data:
            data["cards_col2"] = json_data["cards_col2"]
        if "figure_map" in json_data:
            data["figure_map"] = json_data["figure_map"]

    return data


# ═══════════════════════════════════════════════════════════════
# LaTeX generator
# ═══════════════════════════════════════════════════════════════

PAPER_WIDTH_CM = 160
PAPER_HEIGHT_CM = 100
SCALE = 1.2
SEPWIDTH_FRAC = 0.027
COLWIDTH_FRAC = 0.46


def build_card(latex, t):
    latex.append(f"  \\begin{{block}}{{{t['title']}}}")
    latex.append("    \\centering")
    latex.append("    \\huge")
    for line in t["lines"]:
        if line.strip():
            latex.append(f"    {line} \\par")
    latex.append("  \\end{block}")
    latex.append("")


def build_column(latex, figures, cards):
    card_map = {c["after"]: c for c in cards}
    for fig in figures:
        latex.append(f"  \\posterfig{{figures/{fig}.jpg}}")
        latex.append("")
        if fig in card_map:
            build_card(latex, card_map[fig])


def escape_latex(s):
    """Escape user-supplied text (titles/venues/URLs) for safe LaTeX injection.
    Card lines are authored LaTeX and are NOT passed through this helper."""
    return (s.replace("\\", "\\textbackslash{}")
             .replace("{", "\\{").replace("}", "\\}")
             .replace("&", "\\&").replace("%", "\\%")
             .replace("$", "\\$").replace("#", "\\#")
             .replace("_", "\\_").replace("~", "\\textasciitilde{}")
             .replace("^", "\\textasciicircum{}"))


def generate_tex(poster_dir, data):
    """Generate poster.tex from data dict."""
    title_font = data.get("title_font", 240)
    title = escape_latex(data['title'])
    venue = escape_latex(data.get('venue', ''))
    project_url = escape_latex(data.get('project_url', ''))
    lines = []
    lines.extend([
        r"\RequirePackage{luatex85}",
        r"\documentclass[final]{ctexbeamer}",
        "",
        r"% ── Packages ──",
        r"\usepackage[size=custom,"
        rf"width={PAPER_WIDTH_CM},height={PAPER_HEIGHT_CM},scale={SCALE}]{{beamerposter}}",
        r"\usetheme{gemini}",
        r"\usecolortheme{sustech}",
        rf"\sustechscheme{{{data.get('scheme', 'default')}}}",
        rf"\setbeamerfont{{headline title}}{{size=\fontsize{{{title_font}}}{{{title_font + 40}}}\selectfont,series=\bfseries}}",
        r"\usepackage{graphicx}",
        r"\usepackage{booktabs}",
        r"\usepackage{qrcode}",
        r"\usepackage{hyperref}",
        r"\usepackage{amsmath}",
        "",
        r"% ── Column layout ──",
        r"\newlength{\sepwidth}",
        r"\newlength{\colwidth}",
        rf"\setlength{{\sepwidth}}{{{SEPWIDTH_FRAC}\paperwidth}}",
        rf"\setlength{{\colwidth}}{{{COLWIDTH_FRAC}\paperwidth}}",
        r"\newcommand{\separatorcolumn}{\begin{column}{\sepwidth}\end{column}}",
        "",
        r"% ── Figure macro ──",
        r"\newcommand{\posterfig}[1]{\includegraphics[width=\linewidth]{#1}}",
        "",
        rf"\title{{{title}}}",
        r"\author{}",
        r"\institute{}",
        "",
        r"% ── Footer ──",
    ])

    # Footer
    footer_left = rf"\href{{{project_url}}}{{Project Page}}" if project_url else venue
    footer_center = venue
    if project_url:
        footer_right = rf"\href{{{project_url}}}{{Paper}}"
    else:
        footer_right = ""
    lines.append(rf"\footercontent{{{footer_left} \hfill {footer_center} \hfill {footer_right}}}")

    lines.extend([
        "",
        r"\begin{document}",
        r"\begin{frame}[t]",
        r"\begin{columns}[t]",
        r"\separatorcolumn",
        "",
        r"% ═══════════ COLUMN 1 ═══════════",
        r"\begin{column}{\colwidth}",
        "",
    ])

    build_column(lines, data["figures"].get("col1", []), data.get("cards_col1", []))

    lines.extend([
        r"\end{column}",
        r"\separatorcolumn",
        "",
        r"% ═══════════ COLUMN 2 ═══════════",
        r"\begin{column}{\colwidth}",
        "",
    ])

    build_column(lines, data["figures"].get("col2", []), data.get("cards_col2", []))

    # QR code
    if project_url:
        lines.extend([
            r"  \vspace{0.5cm}",
            r"  \begin{center}",
            rf"    \qrcode[height=2.5cm]{{{project_url}}}",
            r"  \end{center}",
        ])

    lines.extend([
        "",
        r"\end{column}",
        r"\separatorcolumn",
        r"\end{columns}",
        r"\end{frame}",
        r"\end{document}",
    ])

    tex = "\n".join(lines) + "\n"
    tex_path = os.path.join(poster_dir, "poster.tex")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex)
    return tex_path


# ═══════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate a video-cover poster from a paper directory"
    )
    parser.add_argument("paper_dir", help="Path to paper directory with md_output/")
    parser.add_argument("--data", help="Optional JSON config for overrides")
    parser.add_argument(
        "--scheme",
        choices=["default", "lab", "bilibili"],
        default="default",
        help="SUSTech colour scheme for the poster (lab surveys: bilibili)",
    )
    parser.add_argument("--title-font", type=int, help="Override title font size in pt")
    parser.add_argument(
        "--output-dir",
        help="Final output directory (default: <paper_dir>/poster)",
    )
    parser.add_argument("--lualatex", help="Path or command name for lualatex")
    parser.add_argument("--pdftoppm", help="Path or command name for pdftoppm")
    args = parser.parse_args()

    try:
        lualatex = resolve_binary(args.lualatex, "LUALATEX", "lualatex")
        pdftoppm = resolve_binary(args.pdftoppm, "PDFTOPPM", "pdftoppm")
        template_paths = {
            name: os.path.join(TEMPLATES_DIR, name) for name in REQUIRED_TEMPLATES
        }
        for name, path in template_paths.items():
            require_nonempty(path, f"packaged template {name}")

        paper_dir = os.path.abspath(args.paper_dir)
        output_dir = os.path.abspath(
            args.output_dir or os.path.join(paper_dir, "poster")
        )
        try:
            output_contains_paper = os.path.commonpath([paper_dir, output_dir]) == output_dir
        except ValueError:
            output_contains_paper = False
        if output_contains_paper:
            raise ValueError("--output-dir must not be the paper directory or its ancestor")
        output_parent = os.path.dirname(output_dir)
        os.makedirs(output_parent, exist_ok=True)

        print(f"[1/6] Parsing markdown from {paper_dir}...")
        json_data = None
        if args.data:
            with open(args.data, "r", encoding="utf-8") as f:
                json_data = json.load(f)

        data = build_poster_data(paper_dir, json_data)
        data["scheme"] = args.scheme
        if args.title_font:
            data["title_font"] = args.title_font

        print(f"  Title: {data['title'][:80]}...")
        print(f"  Venue: {data['venue']}")
        print(f"  Figures: {len(data['figure_map'])} found")
        print(f"  Title font: {data['title_font']}pt")

        staged_dir = tempfile.mkdtemp(prefix=".poster-build-", dir=output_parent)
        installed = False
        try:
            figures_dir = os.path.join(staged_dir, "figures")
            os.makedirs(figures_dir)

            print(f"\n[2/6] Building temporary output in {staged_dir}...")
            for fig_name, rel_path in data["figure_map"].items():
                src = (
                    os.path.join(paper_dir, rel_path)
                    if not os.path.isabs(rel_path)
                    else rel_path
                )
                if not os.path.isfile(src):
                    raise FileNotFoundError(f"figure source not found: {src}")
                dst = os.path.join(figures_dir, f"{fig_name}.jpg")
                convert_to_jpg(src, dst, pdftoppm)
                print(f"  Converted {fig_name}.jpg")

            print("\n[3/6] Copying LaTeX templates...")
            for name, src in template_paths.items():
                dst = os.path.join(staged_dir, name)
                shutil.copy2(src, dst)
                require_nonempty(dst, f"copied template {name}")

            print("\n[4/6] Generating poster.tex...")
            tex_path = generate_tex(staged_dir, data)
            require_nonempty(tex_path, "poster.tex")

            print("\n[5/6] Compiling with lualatex...")
            for pass_num in (1, 2):
                result = subprocess.run(
                    [lualatex, "-interaction=nonstopmode", "-halt-on-error", "poster.tex"],
                    cwd=staged_dir,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    details = (result.stderr or result.stdout).strip()[-2000:]
                    raise RuntimeError(
                        f"lualatex pass {pass_num} failed with exit code "
                        f"{result.returncode}:\n{details}"
                    )
                print(f"  Pass {pass_num}: OK")
            pdf_path = os.path.join(staged_dir, "poster.pdf")
            require_nonempty(pdf_path, "poster.pdf")

            print("\n[6/6] Rendering PNG...")
            result = subprocess.run(
                [
                    pdftoppm,
                    "-png",
                    "-r",
                    "150",
                    "-f",
                    "1",
                    "-l",
                    "1",
                    "-singlefile",
                    "poster.pdf",
                    "poster",
                ],
                cwd=staged_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"pdftoppm failed with exit code {result.returncode}: "
                    f"{result.stderr.strip()[-2000:]}"
                )
            png_path = os.path.join(staged_dir, "poster.png")
            require_nonempty(png_path, "poster.png")

            for suffix in (".aux", ".log", ".nav", ".out", ".snm", ".toc"):
                auxiliary = os.path.join(staged_dir, "poster" + suffix)
                if os.path.exists(auxiliary):
                    os.remove(auxiliary)

            install_output(staged_dir, output_dir)
            installed = True
        finally:
            if not installed and os.path.exists(staged_dir):
                shutil.rmtree(staged_dir)

        print(f"\nDone. Poster at {output_dir}/")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
