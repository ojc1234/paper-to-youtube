#!/usr/bin/env python3
"""Generate a lab-survey (实验室调研) poster in the deck's 可视化分析 divider format.

The poster mirrors the deck's 可视化分析 section-divider page: full-bleed
figures/background.png + four white rounded shadow boxes (实验室调研+date pair,
institute brand-colour eyebrow block, Prof. name, title_2/3 + emph rule). Stats
from papers.json are informational only (the metric row was removed).

Token contract (any unmatched 〈...〉 on code lines after substitution is fatal):
  〈DATE〉 〈TITLE1UNI〉 〈TITLE1UNIEN〉 〈TITLE1UNICOLOR〉 〈TITLE1PI〉 〈TITLE2〉 〈TITLE3〉
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
TEMPLATES_DIR = os.path.join(SKILL_DIR, "templates")
# TEMPLATE_DIR: a checkout of https://github.com/yhbcode000/sustech-slides-template.
# Unset → the vendored templates/ fallback is used (source of truth is the repo).
REPO_DIR = os.environ.get("TEMPLATE_DIR", "")
DEFAULT_TEMPLATE = os.path.join(REPO_DIR, "main_template_lab_poster.tex")
FALLBACK_TEMPLATE = os.path.join(TEMPLATES_DIR, "main_template_lab_poster.tex")
DEFAULT_THEME_DIR = os.path.join(REPO_DIR, "sustech-theme")
FALLBACK_THEME_DIR = os.path.join(TEMPLATES_DIR, "sustech-theme")
TOKEN_PAT = re.compile(r"〈[^〉]*〉")

sys.path.insert(0, SCRIPT_DIR)
from generate_poster import (  # noqa: E402
    resolve_binary, require_nonempty, install_output, escape_latex,
)


def _corpus_stats(paper_dir):
    """Compute corpus stats from papers.json (legacy — the metric row was
    removed from the poster; stats are informational only, never fatal)."""
    papers_path = os.path.join(paper_dir, "papers.json")
    if not os.path.isfile(papers_path):
        print(f"  (no papers.json — stats skipped: {papers_path})")
        return {"N": "", "CITES": "", "H": "", "YEARS": ""}
    with open(papers_path, "r", encoding="utf-8") as f:
        papers = json.load(f)
    categories = [k for k in papers if k != "meta"]
    all_papers = [p for c in categories for p in papers[c]]
    n = len(all_papers)
    if n == 0:
        print("  (papers.json has no papers — stats skipped)")
        return {"N": "", "CITES": "", "H": "", "YEARS": ""}
    total_cites = sum(p.get("citations") or 0 for p in all_papers)
    cites = sorted((p.get("citations") or 0) for p in all_papers)[::-1]
    h = 0
    for i, c in enumerate(cites, 1):
        if c >= i:
            h = i
        else:
            break
    years = [p["year"] for p in all_papers if p.get("year")]
    year_range = f"{min(years)}–{max(years)}" if years else ""
    return {
        "N": f"{n}",
        "CITES": f"{total_cites:,}",
        "H": f"{h}",
        "YEARS": year_range,
    }


TITLE1_UNI_MAX_UNITS = 40  # title_1_uni width budget: CJK glyph = 2 units, other = 1 (e.g. 纽约大学 = 8, Physical Intelligence = 21)
TITLE1_UNIEN_MAX_LEN = 40  # title_1_uni_en: English small line (e.g. NEW YORK UNIVERSITY), optional
TITLE1_PI_MAX_LEN = 30     # title_1_pi: Prof. name (e.g. Prof. Yann LeCun), REQUIRED
TITLE23_MAX_UNITS = 30     # title_2/title_3 width budget: CJK glyph = 2 units, other = 1 (e.g. 从卷积网络到世界模型 = 18, IEEE Fellow 2026 = 15)
UNI_COLOR_DEFAULT = "000000"  # title_1_uni_color: institute logo brand colour; black when no logo
UNI_COLOR_PAT = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _width_units(val):
    """Visual width in units: CJK/wide glyphs count 2, everything else 1.
    The old plain char-count caps rejected legitimately narrow Latin strings
    like 'IEEE Fellow 2026' (16 chars) and 'Physical Intelligence' (21)."""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in val)


def _title_value(val, key):
    """Escape a title part and convert plain spaces to \hspace glue.

    TikZ nodes + large \fontsize under the sustech theme drop interword
    spaces (even '\ '); explicit \hspace glue survives. Apply to title parts
    so English titles like 'Prof. Yann LeCun' keep their spaces."""
    return escape_latex(val).replace(" ", r"\hspace{0.25em}")


def _uni_value(val):
    """Escape the university eyebrow; for CJK text insert 0.25em tracking
    between CJK characters (NYU Purple eyebrow per 方案 A)."""
    val = escape_latex(val)
    out = []
    for ch in val:
        if re.match(r"[\u4e00-\u9fff]", ch):
            if out:
                out.append(r"\hspace{0.25em}")
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def build_replacements(paper_dir, data, stats):
    uni = data.get("title_1_uni", "").strip()
    if _width_units(uni) > TITLE1_UNI_MAX_UNITS:
        raise RuntimeError(
            f"--data title_1_uni too wide ({_width_units(uni)} units > {TITLE1_UNI_MAX_UNITS}, CJK=2/other=1): {uni}")
    uni_en = data.get("title_1_uni_en", "").strip()
    if len(uni_en) > TITLE1_UNIEN_MAX_LEN:
        raise RuntimeError(
            f"--data title_1_uni_en too long ({len(uni_en)} chars > {TITLE1_UNIEN_MAX_LEN}): {uni_en}")
    uni_color = data.get("title_1_uni_color", "").strip().lstrip("#") or UNI_COLOR_DEFAULT
    if not UNI_COLOR_PAT.match(uni_color):
        raise RuntimeError(
            f"--data title_1_uni_color must be #RRGGBB (got: {data.get('title_1_uni_color', '')!r})")
    pi = data.get("title_1_pi", "").strip()
    if len(pi) > TITLE1_PI_MAX_LEN:
        raise RuntimeError(
            f"--data title_1_pi too long ({len(pi)} chars > {TITLE1_PI_MAX_LEN}): {pi}")
    if not pi:
        raise RuntimeError("--data title_1_pi is REQUIRED (e.g. Prof. Yann LeCun; ≤ 30 chars)")
    title_parts = {}
    for key in ("title_2", "title_3"):
        val = data.get(key, "").strip()
        if _width_units(val) > TITLE23_MAX_UNITS:
            raise RuntimeError(
                f"--data {key} too wide ({_width_units(val)} units > {TITLE23_MAX_UNITS}, CJK=2/other=1): {val}")
        title_parts[key] = _title_value(val, key)
    date = data.get("date", "").strip() or datetime.date.today().isoformat()
    return {
        "〈TITLE1UNI〉": _uni_value(uni),
        "〈TITLE1UNIEN〉": _title_value(uni_en, "title_1_uni_en"),
        "〈TITLE1UNICOLOR〉": uni_color,
        "〈TITLE1PI〉": _title_value(pi, "title_1_pi"),
        "〈TITLE2〉": title_parts["title_2"],
        "〈TITLE3〉": title_parts["title_3"],
        "〈DATE〉": escape_latex(date),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate a lab-survey poster (deck 可视化分析 divider format)"
    )
    parser.add_argument("lab_dir", help="Survey directory with papers.json and slides-beamer/")
    parser.add_argument("--data", help="Optional JSON config (title REQUIRED, lab/date optional)")
    parser.add_argument("--scheme", choices=["default", "lab", "bilibili"], default="bilibili",
                        help="SUSTech colour scheme (default: bilibili)")
    parser.add_argument("--output-dir", help="Final output directory (default: <lab_dir>/poster)")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, help="Poster template tex path")
    parser.add_argument("--theme-dir", default=DEFAULT_THEME_DIR,
                        help="Deck theme directory to copy into the build (sustech-theme)")
    parser.add_argument("--xelatex", help="Path or command name for xelatex")
    parser.add_argument("--pdftoppm", help="Path or command name for pdftoppm")
    args = parser.parse_args()

    try:
        xelatex = resolve_binary(args.xelatex, "XELATEX", "xelatex")
        pdftoppm = resolve_binary(args.pdftoppm, "PDFTOPPM", "pdftoppm")

        template = args.template
        if not os.path.isfile(template):
            template = FALLBACK_TEMPLATE
        if not os.path.isfile(template):
            raise RuntimeError(f"poster template not found: {args.template} (fallback {FALLBACK_TEMPLATE})")
        theme_dir = args.theme_dir
        if not os.path.isdir(theme_dir):
            theme_dir = FALLBACK_THEME_DIR
        if not os.path.isfile(os.path.join(theme_dir, "beamerthemesustech.sty")):
            raise RuntimeError(f"deck theme not found under {theme_dir}")

        paper_dir = os.path.abspath(args.lab_dir)
        background = os.path.join(paper_dir, "slides-beamer", "figures", "background.png")
        if not os.path.isfile(background):
            raise RuntimeError(
                f"slides-beamer/figures/background.png not found: {background} "
                "(the 可视化分析 divider background is REQUIRED for this poster format)")
        output_dir = os.path.abspath(args.output_dir or os.path.join(paper_dir, "poster"))
        try:
            output_contains_paper = os.path.commonpath([paper_dir, output_dir]) == output_dir
        except ValueError:
            output_contains_paper = False
        if output_contains_paper:
            raise ValueError("--output-dir must not be the paper directory or its ancestor")
        output_parent = os.path.dirname(output_dir)
        os.makedirs(output_parent, exist_ok=True)

        print(f"[1/6] Computing corpus stats from {paper_dir}/papers.json...")
        stats = _corpus_stats(paper_dir)
        print(f"  N={stats['N']} cites={stats['CITES']} h={stats['H']} years={stats['YEARS']}")

        json_data = {}
        if args.data:
            with open(args.data, "r", encoding="utf-8") as f:
                json_data = json.load(f)

        replacements = build_replacements(paper_dir, json_data, stats)
        print(f"  Title parts: {json_data.get('title_1', '')}/{json_data.get('title_2', '')}/{json_data.get('title_3', '')}")

        staged_dir = tempfile.mkdtemp(prefix=".poster-build-", dir=output_parent)
        installed = False
        try:
            print("\n[2/6] Copying background.png...")
            figures_dir = os.path.join(staged_dir, "figures")
            os.makedirs(figures_dir)
            shutil.copy2(background, os.path.join(figures_dir, "background.png"))
            require_nonempty(os.path.join(figures_dir, "background.png"), "background.png")

            print("\n[3/6] Copying template + deck theme...")
            tex_path = os.path.join(staged_dir, "poster.tex")
            shutil.copy2(template, tex_path)
            shutil.copytree(theme_dir, os.path.join(staged_dir, "sustech-theme"))
            require_nonempty(tex_path, "poster.tex")

            print("\n[4/6] Filling tokens...")
            with open(tex_path, "r", encoding="utf-8") as f:
                tex = f.read()
            tex = tex.replace(r"\sustechscheme{bilibili}", rf"\sustechscheme{{{args.scheme}}}")
            out_lines = []
            for ln in tex.split("\n"):
                if ln.strip().startswith("%"):
                    out_lines.append(ln)
                    continue
                if "〈TITLE1UNI〉" in ln and not replacements.get("〈TITLE1UNI〉"):
                    continue  # empty uni: drop the whole line
                if "〈TITLE1UNIEN〉" in ln and not replacements.get("〈TITLE1UNIEN〉"):
                    continue  # empty uni_en: drop the whole line
                if "〈TITLE2〉" in ln and not replacements.get("〈TITLE2〉"):
                    continue  # empty part: drop the whole title line
                if "〈TITLE3〉" in ln and not replacements.get("〈TITLE3〉"):
                    continue
                if "〈TITLE1UNICOLOR〉" in ln:
                    # institute brand colour: rewrite the raw black default
                    # (\definecolor{instbrand}{HTML}{000000}) to the real hex
                    color = replacements["〈TITLE1UNICOLOR〉"]
                    ln = ln.replace("〈TITLE1UNICOLOR〉", color)
                    ln = ln.replace("{000000}", "{" + color + "}")
                for token, value in replacements.items():
                    ln = ln.replace(token, value)
                out_lines.append(ln)
            tex = "\n".join(out_lines)
            code_only = "\n".join(
                ln for ln in tex.split("\n") if not ln.strip().startswith("%"))
            leftover = TOKEN_PAT.findall(code_only)
            if leftover:
                raise RuntimeError(f"unreplaced tokens: {sorted(set(leftover))}")
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(tex)

            print("\n[5/6] Compiling with xelatex...")
            env = dict(os.environ)
            env["TEXINPUTS"] = "./sustech-theme//;" + env.get("TEXINPUTS", "")
            for pass_num in (1, 2):
                result = subprocess.run(
                    [xelatex, "-interaction=nonstopmode", "-halt-on-error", "poster.tex"],
                    cwd=staged_dir, capture_output=True, text=True, env=env,
                )
                if result.returncode != 0:
                    details = (result.stderr or result.stdout).strip()[-2000:]
                    raise RuntimeError(
                        f"xelatex pass {pass_num} failed with exit code "
                        f"{result.returncode}:\n{details}")
                print(f"  Pass {pass_num}: OK")
            pdf_path = os.path.join(staged_dir, "poster.pdf")
            require_nonempty(pdf_path, "poster.pdf")

            print("\n[6/6] Rendering PNG...")
            result = subprocess.run(
                [pdftoppm, "-png", "-r", "300", "-f", "1", "-l", "1", "-singlefile",
                 "poster.pdf", "poster"],
                cwd=staged_dir, capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"pdftoppm failed with exit code {result.returncode}: "
                    f"{result.stderr.strip()[-2000:]}")
            require_nonempty(os.path.join(staged_dir, "poster.png"), "poster.png")

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
