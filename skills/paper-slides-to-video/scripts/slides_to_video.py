"""
slides_to_video.py — PDF slides to narrated MP4 video pipeline.

Functions:
  parse_beamer_frames(tex_path) -> list[dict]
  parse_beamer_preamble(tex_path) -> dict
  get_page_plan(tex_path) -> list[dict]
  extract_narrations_from_tex(tex_path) -> dict
  write_narrations_to_files(frame_dir, tex_path, annotated_tex_path=None) -> dict
  extract_pdf_text(pdf_path) -> list[str]
  render_slides(pdf_path, output_dir, dpi=200) -> list[str]
  generate_cover(png_path, output_path) -> str
  generate_metadata(preamble, frames, cover_path, output_path) -> str
  tts_slide(text, output_path, lang='zh') -> bool
  assemble_video(frame_dir, output_path, fps=30) -> None

CLI:
  render, plan, narrations, tts, assemble, cover, metadata, and full.
All narrated paths fail closed when narration or audio is missing.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from venue_emoji import get_emoji_for_preference


def _resolve_executable(env_name: str, executable: str) -> str:
    """Resolve a required executable from an environment override, then PATH."""
    override = os.environ.get(env_name)
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        resolved = shutil.which(override)
        if resolved:
            return resolved
        raise FileNotFoundError(
            f"{env_name} points to unavailable executable: {override}"
        )
    resolved = shutil.which(executable)
    if not resolved:
        raise FileNotFoundError(
            f"Required executable '{executable}' was not found; "
            f"set {env_name} or add it to PATH"
        )
    return resolved


def _resolve_poppler_dir() -> str:
    """Resolve Poppler from POPPLER_DIR/PDFTOPPM or PATH."""
    override = os.environ.get("POPPLER_DIR")
    if override:
        directory = Path(override).expanduser()
        binary = directory / ("pdftoppm.exe" if os.name == "nt" else "pdftoppm")
        if not directory.is_dir() or not binary.is_file():
            raise FileNotFoundError(
                f"POPPLER_DIR does not contain pdftoppm: {directory}"
            )
        return str(directory.resolve())
    return str(Path(_resolve_executable("PDFTOPPM", "pdftoppm")).resolve().parent)


def _resolve_edge_tts() -> str:
    """Resolve the edge-tts CLI from EDGE_TTS_BIN, then PATH."""
    return _resolve_executable("EDGE_TTS_BIN", "edge-tts")


# ─── LaTeX parsing utilities ───────────────────────────────────────────────

def _strip_latex_macros(text: str) -> str:
    """Strip LaTeX macros keeping their inner text content.

    Handles: \\shl{...}, \\hlbox{...}, \\keyword{...}, \\brandemph{...},
    \\textbf{...}, \\emph{...}, \\textit{...}, \\textsuperscript{...},
    \\cite{...}, \\href{url}{text} -> text, \\metric{val}{label} -> val (label),
    \\domaintag{...}, \\cmark, \\xmark, \\tightgap, \\deckgap, \\smallskip,
    \\medskip, \\figcap{...}{...}, \\setlength{...}{...}, \\renewcommand{...}{...}.
    """
    # Remove comments
    text = re.sub(r'(?<!\\)%.*$', '', text, flags=re.MULTILINE)

    # \\href{url}{text} -> text
    text = re.sub(r'\\href\{[^}]*\}\{([^}]*)\}', r'\1', text)

    # \\cite{...} -> empty (citations add nothing to spoken narration)
    text = re.sub(r'\\cite\{[^}]*\}', '', text)

    # Strip wrapper macros keeping inner content (handle nested braces)
    wrapper_macros = [
        r'\shl', r'\hlbox', r'\keyword', r'\brandemph',
        r'\textbf', r'\emph', r'\textit', r'\textsuperscript',
        r'\domaintag', r'\thc',
    ]
    for macro in wrapper_macros:
        # Match macro{...} with balanced braces
        text = _strip_balanced_brace_macro(text, macro)

    # \\metric{val}{label} -> val (label)
    text = re.sub(r'\\metric\{([^}]*)\}\{([^}]*)\}', r'\1 (\2)', text)

    # Remove standalone symbols and spacing commands
    text = re.sub(r'\\cmark\b', '', text)
    text = re.sub(r'\\xmark\b', '', text)
    text = re.sub(r'\\tightgap\b', '', text)
    text = re.sub(r'\\deckgap\b', '', text)
    text = re.sub(r'\\smallskip\b', '', text)
    text = re.sub(r'\\medskip\b', '', text)
    text = re.sub(r'\\figcap\{[^}]*\}\{[^}]*\}', '', text)
    text = re.sub(r'\\setlength\{[^}]*\}\{[^}]*\}', '', text)
    text = re.sub(r'\\renewcommand\{\\secblurb\}\{([^}]*)\}', '', text)
    text = re.sub(r'\\begin\{[^}]*\}', '', text)
    text = re.sub(r'\\end\{[^}]*\}', '', text)

    # Remove inline math $...$
    text = re.sub(r'\$[^$]*\$', '', text)

    # Remove \\[dimension] and \vspace{...}
    text = re.sub(r'\\\\\[\d+[a-z]*\]', '', text)
    text = re.sub(r'\\vspace\{[^}]*\}', '', text)

    # Remove \\ alone at line end (but not line breaks in general)
    text = re.sub(r'\\\\$', '', text, flags=re.MULTILINE)

    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    return text


def _strip_balanced_brace_macro(text: str, macro: str) -> str:
    """Strip \\macro{...} where braces are balanced, keeping inner content."""
    pattern = re.escape(macro) + r'\{'
    result = []
    i = 0
    while i < len(text):
        m = re.search(pattern, text[i:])
        if not m:
            result.append(text[i:])
            break
        start = i + m.start()
        brace_start = start + len(macro) + 1  # after {
        result.append(text[i:start])

        # Find matching closing brace
        depth = 1
        j = brace_start
        while j < len(text) and depth > 0:
            if text[j] == '{':
                depth += 1
            elif text[j] == '}':
                depth -= 1
            if depth > 0:
                j += 1

        inner = text[brace_start:j]
        result.append(inner)
        i = j + 1  # skip past }
    return ''.join(result)


def _extract_braced_arg(text: str) -> str:
    """Extract content inside the first pair of balanced braces."""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i]
    return ''


def _extract_bracketed_arg(text: str) -> str:
    """Extract optional [...] argument content."""
    m = re.match(r'\s*\[([^\]]*)\]', text)
    return m.group(1) if m else ''


# ─── Preamble parsing ──────────────────────────────────────────────────────

_CONFERENCE_TID_MAP = {
    # AI / Robotics / CS / CV / ML → 231 (计算机技术)
    'RSS': 231,
    'ICRA': 231,
    'IROS': 231,
    'CoRL': 231,
    'RA-L': 231,
    'Science Robotics': 231,
    'Robotics: Science and Systems': 231,
    'CVPR': 231,
    'ICCV': 231,
    'ECCV': 231,
    'NeurIPS': 231,
    'ICML': 231,
    'ICLR': 231,
    'ACL': 231,
    'EMNLP': 231,
    'AAAI': 231,
    'IJCAI': 231,
    'SIGGRAPH': 231,
    'CHI': 231,
    'ICASSP': 231,
    # General science → 201 (科学科普)
    'Nature': 201,
    'Science': 201,
    'PNAS': 201,
    'arXiv': 201,
}
def _lookup_tid(venue: str) -> tuple:
    """Return (tid, tid_name) for a conference/journal venue."""
    for key, tid in _CONFERENCE_TID_MAP.items():
        if key.lower() in venue.lower():
            tid_names = {201: '科学科普', 231: '计算机技术', 230: '软件应用'}
            return tid, tid_names.get(tid, '计算机技术')
    return 231, '计算机技术'

def parse_beamer_preamble(tex_path: str) -> dict:
    """Extract {title, subtitle, author, venue, year, domains, urls} from Beamer preamble.

    Parses: \\title, \\subtitle, \\author, \\setsource{venue}{year},
    \\setdomains{\\domaintag{...}...}, and \\href{url}{text} in 论文信息 frame.
    """
    with open(tex_path, 'r', encoding='utf-8') as f:
        content = f.read()

    result = {
        'title': '', 'short_title': '', 'subtitle': '',
        'author': '', 'venue': '', 'year': '',
        'domains': [], 'source_url': '', 'code_url': '', 'project_url': '',
    }

    # \\title[short]{full}
    m = re.search(r'\\title(?:\[([^\]]*)\])?\{((?:[^{}]|\{[^{}]*\})*)\}', content)
    if m:
        result['short_title'] = m.group(1) or ''
        result['title'] = _strip_latex_macros(m.group(2))

    # \\subtitle{...}
    m = re.search(r'\\subtitle\{((?:[^{}]|\{[^{}]*\})*)\}', content)
    if m:
        result['subtitle'] = _strip_latex_macros(m.group(1))

    # \\author[short]{full}
    m = re.search(r'\\author(?:\[([^\]]*)\])?\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', content)
    if m:
        result['author'] = _strip_latex_macros(m.group(2))

    # \\setsource{venue}{year}
    m = re.search(r'\\setsource\{([^}]*)\}\{([^}]*)\}', content)
    if m:
        result['venue'] = m.group(1).strip()
        result['year'] = m.group(2).strip()

    # \\setdomains{\\domaintag{...}\\domaintag{...}...} — handles nested braces
    m = re.search(r'\\setdomains\{', content)
    if m:
        start = m.end()
        depth = 1
        pos = start
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            pos += 1
        domains_text = content[start:pos-1]
        for dm in re.finditer(r'\\domaintag\{([^}]*)\}', domains_text):
            result['domains'].append(dm.group(1))

    # Extract URLs: try 论文信息 frame (legacy), fall back to twopane TOC frame
    info_frame = re.search(
        r'\\begin\{frame\}\{论文信息\}(.*?)\\end\{frame\}', content, re.DOTALL)
    if not info_frame:
        info_frame = re.search(
            r'\\twopane\{.*?\}\{(.*?)\}', content, re.DOTALL)
    if info_frame:
        frame_text = info_frame.group(1)
        hrefs = re.findall(r'\\href\{([^}]*)\}\{([^}]*)\}', frame_text)
        for url, label in hrefs:
            if 'github' in url.lower():
                result['code_url'] = url
            elif 'huggingface' in url.lower() or 'project' in label.lower() or 'website' in label.lower():
                result['project_url'] = url
            elif 'arxiv' in url.lower() or 'paper' in label.lower():
                result['source_url'] = url
            elif result['project_url']:
                pass
            elif not result['source_url']:
                result['source_url'] = url
            else:
                result['project_url'] = url

    return result


# ─── Frame parsing ─────────────────────────────────────────────────────────

def parse_beamer_frames(tex_path: str) -> list:
    """Parse Beamer .tex into structured frame data.

    Returns list of dicts: {
        page_num: int, title: str, section: str, blurb: str,
        items: [str], callout: str, block_text: str,
        has_figure: bool, is_plain: bool, metrics: [(str, str)]
    }
    Pages are numbered from 1 following the frame order in the .tex file.
    """
    with open(tex_path, 'r', encoding='utf-8') as f:
        content = f.read()

    frames = []
    current_section = ''
    current_blurb = ''
    page_num = 0

    # Track \\renewcommand{\\secblurb}{...}
    blurb_positions = list(re.finditer(
        r'\\renewcommand\{\\secblurb\}\{((?:[^{}]|\{[^{}]*\})*)\}', content))
    blurb_map = {}  # char position -> blurb text
    for m in blurb_positions:
        blurb_map[m.start()] = _strip_latex_macros(m.group(1))

    # Track \\section{...}
    section_positions = list(re.finditer(r'\\section\{([^}]*)\}', content))
    sections = [(m.start(), m.group(1)) for m in section_positions]

    # Find all frames
    # Match \begin{frame}[opts]{title} or \begin{frame}[opts] (titleless, e.g. \titlepage)
    frame_starts = list(re.finditer(
        r'\\begin\{frame\}(?:\[([^\]]*)\])?(?:\{([^}]*)\})?', content))

    for idx, fm in enumerate(frame_starts):
        page_num += 1
        frame_opts = fm.group(1) or ''
        frame_title = _strip_latex_macros(fm.group(2) or '')

        # Find the matching \end{frame}
        # \end{frame} = 11 chars, \begin{frame} = 13 chars
        depth = 1
        pos = fm.end()
        while pos < len(content) and depth > 0:
            if content[pos:pos+11] == '\\end{frame}':
                depth -= 1
                if depth == 0:
                    break
                pos += 11
            elif content[pos:pos+13] == '\\begin{frame}':
                depth += 1
                pos += 13
            else:
                pos += 1
        frame_body = content[fm.end():pos]

        # Determine section context for this frame
        frame_char_start = fm.start()
        for s_start, s_name in reversed(sections):
            if s_start < frame_char_start:
                current_section = s_name
                break
        # Find most recent blurb before this frame
        for b_start in sorted(blurb_map.keys(), reverse=True):
            if b_start < frame_char_start:
                current_blurb = blurb_map[b_start]
                break

        # Extract items
        items = []
        item_matches = re.findall(r'\\item\s+(.*?)(?=\\item|\\(?:begin|end)\{|$)',
                                  frame_body, re.DOTALL)
        for item_text in item_matches:
            cleaned = _strip_latex_macros(item_text).strip()
            if cleaned and len(cleaned) > 2:
                items.append(cleaned)

        # Extract callout
        callout_text = ''
        callout_m = re.search(
            r'\\begin\{callout\}(?:\[([^\]]*)\])?\s*(.*?)\\end\{callout\}',
            frame_body, re.DOTALL)
        if callout_m:
            callout_text = _strip_latex_macros(callout_m.group(2)).strip()

        # Extract block/exampleblock text
        block_text = ''
        for block_env in ['block', 'exampleblock']:
            for block_m in re.finditer(
                    rf'\\begin\{{{block_env}\}}\{{[^}}]*\}}\s*(.*?)\\end\{{{block_env}\}}',
                    frame_body, re.DOTALL):
                block_text += _strip_latex_macros(block_m.group(1)) + '\n'

        # Extract metrics
        metrics = []
        for mm in re.finditer(r'\\metric\{([^}]*)\}\{([^}]*)\}', frame_body):
            metrics.append((mm.group(1).strip(), mm.group(2).strip()))

        # Detect figures
        has_figure = bool(re.search(r'\\includegraphics', frame_body))

        # Detect plain figure frames
        is_plain = 'plain' in frame_opts

        # Detect special frames to potentially skip
        is_titlepage = '\\titlepage' in frame_body
        is_toc = '\\tableofcontents' in frame_body
        is_qa = 'Q&A' in frame_title or 'Q\\&A' in frame_title

        frames.append({
            'page_num': page_num,
            'title': frame_title,
            'section': current_section,
            'blurb': current_blurb,
            'items': items,
            'callout': callout_text,
            'block_text': block_text.strip(),
            'has_figure': has_figure,
            'is_plain': is_plain,
            'is_titlepage': is_titlepage,
            'is_toc': is_toc,
            'is_qa': is_qa,
            'metrics': metrics,
        })

    return frames


def _extract_narration_annotations(tex_path: str) -> list[str]:
    """Return all % NARRATION: payloads in source order."""
    with open(tex_path, 'r', encoding='utf-8') as f:
        return [
            line.strip()[len('% NARRATION:'):].strip()
            for line in f
            if line.strip().startswith('% NARRATION:')
        ]


def extract_narrations_from_tex(tex_path: str) -> dict:
    """Extract one nonempty narration for every physical page."""
    page_plan = get_page_plan(tex_path)
    annotations = _extract_narration_annotations(tex_path)
    if len(annotations) != len(page_plan):
        raise ValueError(
            f"Narration cardinality mismatch: {len(annotations)} annotations "
            f"for {len(page_plan)} physical pages"
        )
    empty = [index for index, text in enumerate(annotations, 1) if not text]
    if empty:
        raise ValueError(f"Empty narration annotations at page positions: {empty}")
    return {
        str(page): text
        for page, (_entry, text) in enumerate(zip(page_plan, annotations), 1)
    }

# ─── Page plan (PDF-to-frame mapping) ──────────────────────────────────────

def get_page_plan(tex_path: str) -> list:
    """Parse .tex to predict PDF page order, accounting for \\section{} pages.

    Beamer themes (Madrid, sustech) insert a section divider frame via
    \\AtBeginSection before each \\section{}. This function tracks both
    explicit \\begin{frame} blocks and implicit section frames.

    Returns a list where each entry is:
      {'type': 'frame', 'frame_idx': N, 'title': str}
      {'type': 'section', 'section_name': str}
    in document (PDF page) order.
    """
    with open(tex_path, 'r', encoding='utf-8') as f:
        content = f.read()

    plan = []
    frame_idx = 0

    # Find all \section{...} and \begin{frame} in document order
    # Pattern matches \section{name} (possibly with leading whitespace)
    section_pat = re.compile(r'(?<!\\)\\section\{([^}]*)\}')
    # Pattern matches \begin{frame}[opts]{title} or \begin{frame}[opts]
    frame_pat = re.compile(
        r'(?<!\\)\\begin\{frame\}(?:\[([^\]]*)\])?(?:\{([^}]*)\})?')


    # Find all occurrences and sort by position
    events = []
    for m in section_pat.finditer(content):
        # Check if line is a comment
        line_start = content.rfind('\n', 0, m.start()) + 1
        if content[line_start:line_start+1] != '%':
            events.append((m.start(), 'section', m.group(1)))
    for m in frame_pat.finditer(content):
        line_start = content.rfind('\n', 0, m.start()) + 1
        if content[line_start:line_start+1] != '%':
            events.append((m.start(), 'frame', m.group(2) or ''))


    events.sort(key=lambda x: x[0])

    for _pos, kind, arg in events:
        if kind == 'section':
            plan.append({'type': 'section', 'section_name': arg})
        elif kind == 'frame':
            frame_idx += 1
            plan.append({'type': 'frame', 'frame_idx': frame_idx, 'title': arg})

    return plan


def write_narrations_to_files(frame_dir: str, tex_path: str,
                              annotated_tex_path: str = None) -> dict:
    """Write one nonempty narration for every physical page."""
    frame_dir_path = Path(frame_dir)
    frame_dir_path.mkdir(parents=True, exist_ok=True)
    source_tex = Path(tex_path)
    annotated_tex = Path(annotated_tex_path or tex_path)
    if not source_tex.is_file():
        raise FileNotFoundError(f"LaTeX source not found: {source_tex}")
    if not annotated_tex.is_file():
        raise FileNotFoundError(f"Annotated LaTeX source not found: {annotated_tex}")
    for pattern in ("slide_*.txt", "slide_*.mp3", "slide_*_final.mp4"):
        for stale in frame_dir_path.glob(pattern):
            stale.unlink()
    page_plan = get_page_plan(str(source_tex))
    annotations = _extract_narration_annotations(str(annotated_tex))
    if len(annotations) != len(page_plan):
        raise ValueError(
            f"Narration cardinality mismatch: {len(annotations)} annotations "
            f"for {len(page_plan)} physical pages"
        )
    empty = [index for index, text in enumerate(annotations, 1) if not text]
    if empty:
        raise ValueError(f"Empty narration annotations at page positions: {empty}")
    written = []
    for pdf_page, (_entry, text) in enumerate(zip(page_plan, annotations), 1):
        (frame_dir_path / f"slide_{pdf_page:03d}.txt").write_text(
            text, encoding="utf-8"
        )
        written.append(pdf_page)
    section_pages = [
        page for page, entry in enumerate(page_plan, 1)
        if entry["type"] == "section"
    ]
    return {
        "count": len(written),
        "frame_count": sum(entry["type"] == "frame" for entry in page_plan),
        "section_pages": section_pages, "files": written,
        "missing_txt": [], "stray_txt": [], "mismatched": 0,
    }
# ─── PDF utilities ─────────────────────────────────────────────────────────

def extract_pdf_text(pdf_path: str) -> list:
    """Extract text from each page of a PDF using PyMuPDF.

    Returns list of strings, one per page.
    """
    import fitz
    doc = fitz.open(pdf_path)
    texts = []
    for page in doc:
        texts.append(page.get_text('text'))
    doc.close()
    return texts


def render_slides(pdf_path: str, output_dir: str, dpi: int = 200) -> list:
    """Render all PDF pages to clean, consecutively numbered PNG files."""
    from pdf2image import convert_from_path
    source = Path(pdf_path)
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(f"Nonempty PDF not found: {source}")
    if dpi <= 0:
        raise ValueError("dpi must be positive")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for stale in destination.glob("slide_*.png"):
        stale.unlink()
    images = convert_from_path(
        str(source), dpi=dpi, poppler_path=_resolve_poppler_dir()
    )
    if not images:
        raise RuntimeError(f"PDF renderer returned no pages for {source}")
    paths = []
    for index, image in enumerate(images, 1):
        output = destination / f"slide_{index:03d}.png"
        image.save(output, "PNG")
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"Failed to render PDF page {index}: {output}")
        paths.append(str(output))
    return paths


# ─── Cover image ───────────────────────────────────────────────────────────

def generate_cover(png_path: str, output_path: str) -> str:
    """Create cover image from first slide PNG (raw, no poster processing)."""
    import shutil
    shutil.copy(png_path, output_path)
    return os.path.abspath(output_path)


# ─── Bilibili metadata ─────────────────────────────────────────────────────

def generate_metadata(preamble: dict, frames: list, cover_path: str, output_path: str, preference_tier: str = None) -> str:
    """Generate video_meta.json for Bilibili upload.

    Args:
        preamble: Dict from parse_beamer_preamble().
        frames: List from parse_beamer_frames().
        cover_path: Relative path to cover.png (e.g. 'cover.png').
        output_path: Path to save video_meta.json.

    Returns:
        Absolute path to the generated JSON file.
    """
    venue = preamble.get('venue', '')
    year = preamble.get('year', '')
    title = preamble.get('title', 'Unknown Paper')
    title_en = preamble.get('subtitle', '')
    if not title_en:
        title_en = title  # fallback

    tid, tid_name = _lookup_tid(venue)

    # Tags from domains + venue + paper name
    tags = preamble.get('domains', [])
    if venue:
        tags.append(f'{venue} {year}'.strip())
    tags.append('论文分享')
    # Add paper short name if any
    short_title = preamble.get('short_title', '')
    if short_title and short_title not in tags:
        # Truncate to fit
        if len(short_title) <= 20:
            tags.insert(0, short_title)
    # Max 10 tags
    tags = tags[:10]

    # Find the 一句话概括 callout from 论文信息 frame
    one_line_summary = ''
    highlights_items = []
    links = {}

    for f in frames:
        if f['title'] == '论文信息' or f['title'] == '目录':
            one_line_summary = f.get('callout', '')
        if '研究背景' in f.get('title', '') or '在做什么' in f.get('title', ''):
            highlights_items = f.get('items', [])[:5]

    # Extract links from preamble
    if preamble.get('source_url'):
        links['论文链接'] = preamble['source_url']
    if preamble.get('code_url'):
        links['代码'] = preamble['code_url']
    if preamble.get('project_url'):
        links['项目网站'] = preamble['project_url']

    # Build description
    desc_lines = []
    desc_lines.append(f"【论文分享】{title_en} ({venue} {year}) — {one_line_summary}" if one_line_summary
                      else f"【论文分享】{title_en} ({venue} {year})")

    if highlights_items:
        desc_lines.append('')
        for item in highlights_items[:5]:
            desc_lines.append(f"- {item}")

    if links:
        desc_lines.append('')
        for label, url in links.items():
            desc_lines.append(f"{label}：{url}")

    desc_lines.append('')
    desc_lines.append('本视频由 AI 自动生成，仅供学术交流使用。')
    desc = '\n'.join(desc_lines)

    # Dynamic feed text
    short_for_dynamic = preamble.get('short_title', '') or title_en
    short_for_dynamic = re.sub(r'\s*论文分享\s*$', '', short_for_dynamic).strip() or title_en
    if len(short_for_dynamic) > 50:
        short_for_dynamic = short_for_dynamic[:47] + '...'
    summary_short = one_line_summary[:100] if one_line_summary else ''
    dynamic = f"新一期论文分享：{short_for_dynamic} ({venue} {year}) — {summary_short}"
    if len(dynamic) > 256:
        dynamic = dynamic[:253] + '...'

    # Title with venue prefix: 【Venue Year】ShortName — Title
    # The paper short name (from \title[Short 论文分享]) is prepended so the
    # Bilibili title reads 【Venue Year】Emoji ShortName：中文亮点. When the
    # Chinese title is itself "主题：副题", the inner colon becomes a comma
    # after the short name: SuperMap：时空语义SLAM，为… (Motus/Voyager style).
    # SURVEY GUARD: if this is a survey deck, use Chinese default to avoid
    # the English \title{} text blowing past Bilibili's 80-char cap.
    # Phase 8 of paper-deep-survey-to-video will refine this further.
    _domains = preamble.get('domains', [])
    _is_survey = any('survey' in d.lower() for d in _domains)
    venue_prefix = f"【{venue} {year}】" if venue and year else ""
    emoji = get_emoji_for_preference(preference_tier)
    if _is_survey:
        # Use venue+year prefix only; the descriptive hook must be Chinese
        meta_title = f"{venue_prefix}调研报告" if venue_prefix else title[:80]
    else:
        short_name = re.sub(r'\s*论文分享\s*$', '', short_title).strip()
        if short_name and short_name not in title:
            head, sep, tail = title.partition('：')
            if sep and head:
                meta_title = f"{venue_prefix}{emoji}{short_name}：{head}，{tail}"[:80]
            else:
                meta_title = f"{venue_prefix}{emoji}{short_name}：{title}"[:80]
        else:
            meta_title = (venue_prefix + emoji + title)[:80]

    meta = {
        'title': meta_title,
        'title_en': title_en,
        'tid_name': tid_name,
        'tid': tid,
        'tag': tags,
        'desc': desc,
        'copyright': 1,
        'source': '',
        'dynamic': dynamic,
        'no_reprint': 0,
        'cover_path': cover_path,
        'preference_tier': preference_tier,
        'emoji': emoji,
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return os.path.abspath(output_path)


# ─── TTS ───────────────────────────────────────────────────────────────────
# edge-tts (default) or IndexTTS MacBook MLX. Set TTS_ENGINE=indextts to switch.

def tts_slide(text: str, output_path: str, lang: str = 'zh') -> bool:
    """Convert text to speech. Default: edge-tts. Set TTS_ENGINE=indextts for MacBook MLX."""
    engine = os.environ.get('TTS_ENGINE', 'edge-tts')
    if engine == 'indextts':
        return indextts_tts(text, output_path, lang)
    return edge_tts_slide(text, output_path, lang)

# ─── IndexTTS backend (MacBook MLX) ────────────────────────────────────────

_INDEXTTS_API = os.environ.get('INDEXTTS_API_URL', 'http://192.168.42.1:8001')

def indextts_tts(text: str, output_path: str, lang: str = 'zh') -> bool:
    """Convert text to speech via IndexTTS FastAPI server (voice cloning)."""
    import requests
    if not text or not text.strip():
        return False
    try:
        resp = requests.post(
            f'{_INDEXTTS_API}/tts',
            json={
                'text': text,
                'voice': 'default',
            },
            timeout=600
        )
        resp.raise_for_status()
        with open(output_path, 'wb') as f:
            f.write(resp.content)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 500:
            print(f'  [OK] IndexTTS -> {os.path.basename(output_path)}')
            return True
        else:
            print(f'  [FAIL] IndexTTS empty -> {os.path.basename(output_path)}')
            return False
    except Exception as e:
        print(f'  [FAIL] IndexTTS -> {os.path.basename(output_path)}: {e}')
        if os.path.exists(output_path):
            try: os.unlink(output_path)
            except OSError: pass

# ─── edge-tts backend (local, default) ─────────────────────────────────────

_EDGE_TTS_VOICE = os.environ.get("EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
_EDGE_TTS_RATE = os.environ.get("EDGE_TTS_RATE", "+15%")

def edge_tts_slide(text: str, output_path: str, lang: str = "zh") -> bool:
    """Convert text to speech via edge-tts CLI (Microsoft Edge TTS)."""
    import subprocess
    if not text or not text.strip():
        return False
    voice = "en-US-JennyNeural" if lang == "en" else _EDGE_TTS_VOICE
    rate = "+25%" if lang == "en" else _EDGE_TTS_RATE
    try:
        subprocess.run(
            [_resolve_edge_tts(), "--voice", voice, "--rate", rate,
             "--text", text, "--write-media", output_path],
            capture_output=True, timeout=30, check=True
        )
        if os.path.exists(output_path) and os.path.getsize(output_path) > 500:
            return True
        return False
    except Exception as e:
        print(f"  [FAIL] edge-tts -> {os.path.basename(output_path)}: {e}")
        if os.path.exists(output_path):
            try: os.unlink(output_path)
            except OSError: pass
        return False

def batch_tts_parallel(frame_dir: str, lang: str = 'zh', max_workers: int = 8) -> int:
    """Run TTS on all slide_NNN.txt files in parallel via ThreadPoolExecutor.

    Args:
        frame_dir: Directory containing slide_NNN.txt files.
        lang: Voice language ('zh' or 'en').
        max_workers: Parallel workers (default 8).

    Returns:
        Number of slides processed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tasks = []
    for fname in sorted(os.listdir(frame_dir)):
        if not fname.startswith('slide_') or not fname.endswith('.txt'):
            continue
        basename = fname[:-4]
        txt_path = os.path.join(frame_dir, fname)
        mp3_path = os.path.join(frame_dir, f'{basename}.mp3')
        if os.path.exists(mp3_path):
            continue
        with open(txt_path, 'r', encoding='utf-8') as f:
            text = f.read()
        if text.strip():
            tasks.append((basename, text, mp3_path))

    if not tasks:
        print('  TTS: all MP3s already present, skipping')
        return 0

    _engine = os.environ.get('TTS_ENGINE', 'edge-tts')
    _tts_fn = indextts_tts if _engine == 'indextts' else edge_tts_slide

    if _engine == 'edge-tts':
        print(f'  TTS: {len(tasks)} slides, edge-tts serial...')
        done = 0
        for i, (name, text, mp3) in enumerate(tasks):
            ok = _tts_fn(text, mp3, lang=lang)
            if ok: done += 1
            print(f'  [{i+1:02d}/{len(tasks)}] {name} {"OK" if ok else "FAIL"}')
        print(f'  TTS complete: {done}/{len(tasks)}')
        return done

    # IndexTTS: use thread pool (serialized by server mutex)
    def _do(args):
        name, text, mp3 = args
        ok = _tts_fn(text, mp3, lang=lang)
        return name, ok

    print(f'  TTS: {len(tasks)} slides, {max_workers} workers (IndexTTS MLX)...')
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_do, t): t[0] for t in tasks}
        for future in as_completed(futures):
            name, ok = future.result()
            if ok:
                done += 1
    print(f'  TTS complete: {done}/{len(tasks)}')
    return done


# ─── Video assembly ────────────────────────────────────────────────────────

def _probe_audio_duration(audio_path: str) -> float:
    path = Path(audio_path)
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"Required nonempty narration audio is missing: {path}")
    command = [
        _resolve_executable("FFPROBE", "ffprobe"), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe rejected narration audio {path}: {result.stderr.strip()}")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned no duration for narration audio {path}") from exc
    if duration <= 0:
        raise RuntimeError(f"Narration audio has invalid duration {duration}: {path}")
    return duration


def encode_slide(png_path, frame_dir, vf_filter, pad_sec,
                 require_audio=True) -> str:
    """Encode one slide; silence is allowed only via require_audio=False."""
    slide = Path(png_path)
    if not slide.is_file() or slide.stat().st_size == 0:
        raise FileNotFoundError(f"Required nonempty slide image is missing: {slide}")
    basename = slide.stem
    mp3_path = Path(frame_dir) / f"{basename}.mp3"
    final_mp4 = Path(frame_dir) / f"{basename}_final.mp4"
    has_audio = mp3_path.is_file() and mp3_path.stat().st_size > 0
    if require_audio and not has_audio:
        raise FileNotFoundError(f"Narration audio required for {basename}: {mp3_path}")
    total_duration = (
        _probe_audio_duration(str(mp3_path)) + pad_sec
        if has_audio else 2.0 + pad_sec
    )
    command = [_resolve_executable("FFMPEG", "ffmpeg"), "-y", "-loglevel", "error"]
    command.extend(["-loop", "1", "-i", str(slide)])
    if has_audio:
        command.extend(["-i", str(mp3_path)])
    else:
        command.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])
    command.extend([
        "-c:v", "libx264", "-tune", "stillimage", "-preset", "medium",
        "-crf", "23", "-pix_fmt", "yuv420p", "-t", str(total_duration),
    ])
    command.extend(["-vf", vf_filter])
    audio_filter = "aformat=sample_rates=44100:channel_layouts=stereo"
    if has_audio:
        audio_filter += f",volume=2.5,apad=whole_dur={total_duration}"
    command.extend(["-af", audio_filter, "-c:a", "aac", "-b:a", "128k", str(final_mp4)])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        if final_mp4.exists():
            final_mp4.unlink()
        raise RuntimeError(f"ffmpeg failed for {basename}: {result.stderr.strip()}")
    if not final_mp4.is_file() or final_mp4.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced no segment for {basename}")
    return str(final_mp4)


def assemble_video(frame_dir: str, output_path: str, fps: int = 30,
                   pad_sec: float = 0.5, speed: float = 1.0,
                   max_workers: int = None) -> None:
    """Assemble a narrated MP4 only when every rendered page has valid audio."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    frame_root = Path(frame_dir)
    png_files = sorted(frame_root.glob("slide_*.png"))
    if not png_files:
        raise FileNotFoundError(f"No slide PNGs found in {frame_root}")
    expected = {path.stem for path in png_files}
    audio_files = sorted(frame_root.glob("slide_*.mp3"))
    actual_audio = {path.stem for path in audio_files if path.stat().st_size > 0}
    if actual_audio != expected:
        raise RuntimeError(
            "Narration audio cardinality mismatch: "
            f"missing={sorted(expected - actual_audio)}, "
            f"stray={sorted(actual_audio - expected)}"
        )
    for audio in audio_files:
        _probe_audio_duration(str(audio))
    if speed <= 0 or speed > 2:
        raise ValueError("speed must be greater than zero and at most 2")
    if max_workers is None:
        max_workers = min(2, os.cpu_count() or 2)
    video_filter = (
        "scale=1920:1200:force_original_aspect_ratio=decrease,"
        "pad=1920:1200:(ow-iw)/2:(oh-ih)/2:black"
    )

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                encode_slide, str(png), str(frame_root), video_filter, pad_sec, True
            ): png
            for png in png_files
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda path: Path(path).name)

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    concat_list = frame_root / "filelist.txt"
    concat_list.write_text(
        "".join(f"file '{Path(path).resolve()}'\n" for path in results),
        encoding="utf-8",
    )
    ffmpeg = _resolve_executable("FFMPEG", "ffmpeg")
    concat_output = output.with_name(f"{output.stem}_concat.mp4") if speed != 1 else output
    concat = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(concat_list.resolve()), "-c", "copy", str(concat_output)],
        capture_output=True, text=True,
    )
    if concat.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {concat.stderr.strip()}")
    if speed != 1:
        sped = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(concat_output),
             "-vf", f"setpts=PTS/{speed}", "-af", f"atempo={speed}",
             "-c:v", "libx264", "-preset", "fast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k", str(output)],
            capture_output=True, text=True,
        )
        if sped.returncode != 0:
            raise RuntimeError(f"ffmpeg speedup failed: {sped.stderr.strip()}")
        concat_output.unlink(missing_ok=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"Video assembly produced no output: {output}")
    probe = subprocess.run(
        [_resolve_executable("FFPROBE", "ffprobe"), "-v", "error",
         "-show_entries", "stream=codec_type", "-of", "json", str(output)],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(f"ffprobe rejected assembled video: {probe.stderr.strip()}")
    streams = {item.get("codec_type") for item in json.loads(probe.stdout).get("streams", [])}
    if not {"audio", "video"}.issubset(streams):
        raise RuntimeError(f"Assembled output lacks audio/video streams: {output}")


def _validate_numbered_files(directory: Path, suffix: str, expected_count: int) -> list:
    paths = sorted(directory.glob(f"slide_*{suffix}"))
    expected_names = {f"slide_{index:03d}{suffix}" for index in range(1, expected_count + 1)}
    actual_names = {path.name for path in paths if path.is_file() and path.stat().st_size > 0}
    if actual_names != expected_names:
        raise RuntimeError(
            f"{suffix} cardinality mismatch: missing={sorted(expected_names - actual_names)}, "
            f"stray={sorted(actual_names - expected_names)}"
        )
    return paths

# ─── Main (full pipeline) ──────────────────────────────────────────────────

def run_full_pipeline(paper_dir: str, annotated_tex_path: str) -> dict:
    """Run the fail-closed narrated pipeline and publish outputs atomically."""
    paper_root = Path(paper_dir).resolve()
    tex_path = paper_root / "slides-beamer" / "main.tex"
    pdf_path = paper_root / "slides-beamer" / "main.pdf"
    annotated_source = Path(annotated_tex_path).resolve()
    for required in (tex_path, pdf_path, annotated_source):
        if not required.is_file() or required.stat().st_size == 0:
            raise FileNotFoundError(f"Required nonempty input not found: {required}")

    video_dir = paper_root / "video"
    frame_dir = video_dir / "video_frames"
    video_dir.mkdir(parents=True, exist_ok=True)
    frame_dir.mkdir(parents=True, exist_ok=True)
    paper_name = paper_root.name.split(" - ")[-1]
    video_path = video_dir / f"{paper_name}_narrated.mp4"
    partial_video = video_dir / f".{paper_name}_narrated.partial.mp4"
    video_path.unlink(missing_ok=True)
    partial_video.unlink(missing_ok=True)

    preamble = parse_beamer_preamble(str(tex_path))
    frames = parse_beamer_frames(str(tex_path))
    rendered = render_slides(str(pdf_path), str(frame_dir), dpi=int(os.environ.get("RENDER_DPI", "200")))
    physical_pages = len(rendered)
    annotated_copy = video_dir / "main_with_narration.tex"
    annotated_partial = video_dir / ".main_with_narration.tex.partial"
    annotated_partial.unlink(missing_ok=True)
    narration_result = write_narrations_to_files(
        str(frame_dir), str(tex_path), str(annotated_source)
    )
    _validate_numbered_files(frame_dir, ".txt", physical_pages)
    narrations = extract_narrations_from_tex(str(annotated_source))
    (video_dir / "narrations.json").write_text(
        json.dumps(narrations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (video_dir / "frames_data.json").write_text(
        json.dumps({"preamble": preamble, "frames": frames}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    batch_tts_parallel(str(frame_dir))
    audio_files = _validate_numbered_files(frame_dir, ".mp3", physical_pages)
    for audio in audio_files:
        _probe_audio_duration(str(audio))
    try:
        assemble_video(str(frame_dir), str(partial_video), pad_sec=0.5, speed=1.25)
        cover_path = Path(generate_cover(rendered[0], str(video_dir / "cover.png")))
        preference = None
        match = re.search(
            r"% PREFERENCE:\s*(pc|society|both)",
            tex_path.read_text(encoding="utf-8"),
        )
        if match:
            preference = match.group(1)
        metadata_path = Path(generate_metadata(
            preamble, frames, "cover.png", str(video_dir / "video_meta.json"),
            preference_tier=preference,
        ))
        if annotated_source != annotated_copy.resolve():
            shutil.copy2(annotated_source, annotated_partial)
            os.replace(annotated_partial, annotated_copy)
        os.replace(partial_video, video_path)
    except Exception:
        partial_video.unlink(missing_ok=True)
        annotated_partial.unlink(missing_ok=True)
        video_path.unlink(missing_ok=True)
        raise
    return {
        "paper_name": paper_name, "paper_dir": str(paper_root),
        "video_dir": str(video_dir), "video": str(video_path),
        "cover": str(cover_path), "metadata": str(metadata_path),
        "frames": str(frame_dir), "num_slides": physical_pages,
        "narrations": narration_result["count"],
    }


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail-closed narrated slide video pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render")
    render.add_argument("pdf")
    render.add_argument("frame_dir")
    render.add_argument("--dpi", type=int, default=200)
    plan = commands.add_parser("plan")
    plan.add_argument("tex")
    narrations = commands.add_parser("narrations")
    narrations.add_argument("frame_dir")
    narrations.add_argument("tex")
    narrations.add_argument("--annotated-tex", required=True)
    tts = commands.add_parser("tts")
    tts.add_argument("frame_dir")
    tts.add_argument("--lang", default="zh")
    assemble = commands.add_parser("assemble")
    assemble.add_argument("frame_dir")
    assemble.add_argument("output")
    assemble.add_argument("--pad-sec", type=float, default=0.5)
    assemble.add_argument("--speed", type=float, default=1.25)
    cover = commands.add_parser("cover")
    cover.add_argument("first_frame")
    cover.add_argument("output")
    metadata = commands.add_parser("metadata")
    metadata.add_argument("tex")
    metadata.add_argument("cover")
    metadata.add_argument("output")
    full = commands.add_parser("full")
    full.add_argument("paper_dir")
    full.add_argument("--annotated-tex", required=True)
    return parser


def _main() -> int:
    args = _build_cli().parse_args()
    if args.command == "render":
        print(json.dumps(render_slides(args.pdf, args.frame_dir, args.dpi)))
    elif args.command == "plan":
        print(json.dumps(get_page_plan(args.tex), ensure_ascii=False, indent=2))
    elif args.command == "narrations":
        result = write_narrations_to_files(
            args.frame_dir, args.tex, annotated_tex_path=args.annotated_tex
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "tts":
        frame_dir = Path(args.frame_dir)
        text_files = sorted(frame_dir.glob("slide_*.txt"))
        if not text_files or any(not path.read_text(encoding="utf-8").strip() for path in text_files):
            raise RuntimeError("TTS requires at least one nonempty slide narration")
        batch_tts_parallel(str(frame_dir), lang=args.lang)
        expected = {path.stem for path in text_files}
        audio = {
            path.stem for path in frame_dir.glob("slide_*.mp3")
            if path.is_file() and path.stat().st_size > 0
        }
        if audio != expected:
            raise RuntimeError(
                f"TTS audio cardinality mismatch: missing={sorted(expected - audio)}, "
                f"stray={sorted(audio - expected)}"
            )
        for path in frame_dir.glob("slide_*.mp3"):
            _probe_audio_duration(str(path))
        print(len(audio))
    elif args.command == "assemble":
        assemble_video(
            args.frame_dir, args.output, pad_sec=args.pad_sec, speed=args.speed
        )
        print(str(Path(args.output).resolve()))
    elif args.command == "cover":
        print(generate_cover(args.first_frame, args.output))
    elif args.command == "metadata":
        preamble = parse_beamer_preamble(args.tex)
        frames = parse_beamer_frames(args.tex)
        print(generate_metadata(preamble, frames, args.cover, args.output))
    elif args.command == "full":
        print(json.dumps(
            run_full_pipeline(args.paper_dir, args.annotated_tex),
            ensure_ascii=False, indent=2,
        ))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
