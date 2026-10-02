"""
gen_transcript.py — Generate transcript_raw.txt from parsed Beamer frames.

Usage:
    from gen_transcript import generate_raw_transcript
    generate_raw_transcript(r'论文分享\SCIENCE ROBOTICS 2025 - MT3')
"""

import os
import re
import sys
from pathlib import Path

# Add parent scripts dir for import
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def generate_raw_transcript(paper_dir: str) -> str:
    """
    1. Parse main.tex → frames
    2. Build PDF page mapping (accounts for section-separator pages)
    3. Write transcript_raw.txt to <paper_dir>/video/
    Returns path to transcript file.
    """
    from slides_to_video import parse_beamer_frames

    tex_path = os.path.join(paper_dir, 'slides-beamer', 'main.tex')
    video_dir = os.path.join(paper_dir, 'video')
    os.makedirs(video_dir, exist_ok=True)

    with open(tex_path, 'r', encoding='utf-8') as f:
        content = f.read()

    frames = parse_beamer_frames(tex_path)

    # PDF page mapping (accounts for section-separator pages)
    events = []
    for m in re.finditer(r'\\begin\{frame\}', content):
        events.append((m.start(), 'frame'))
    for m in re.finditer(r'\\section\{([^}]*)\}', content):
        events.append((m.start(), f'section:{m.group(1)}'))
    events.sort()

    pdf_page = 0
    frame_idx = 0
    section_pages = {}   # pdf_page -> section_name
    frame_to_pdf = {}    # frame.page_num -> pdf_page

    for pos, kind in events:
        pdf_page += 1
        if kind.startswith('section:'):
            section_pages[pdf_page] = kind.split(':', 1)[1]
        else:
            if frame_idx < len(frames):
                frame_to_pdf[frames[frame_idx]['page_num']] = pdf_page
                frame_idx += 1

    # Section blurbs
    blurb_map = {}
    for m in re.finditer(r'\\renewcommand\{\\secblurb\}\{([^}]*)\}', content):
        bp = m.end()
        bt = m.group(1)
        for sm in re.finditer(r'\\section\{([^}]*)\}', content):
            if sm.start() > bp:
                blurb_map[sm.group(1)] = bt
                break

    lines = []

    # Opening slide (always slide 001)
    lines.append(
        "001: | | 今天是2026年6月30日。"
        "机器人学习与具身智能领域。"
        "接下来让我们开始。"
    )

    # TOC slide — skip
    # lines already accounted for

    # Section-separator pages
    for pg, sec_name in sorted(section_pages.items()):
        blurb = blurb_map.get(sec_name, '')
        lines.append(f"{pg:03d}: {sec_name} | SECTION | {blurb}")

    # Content frames
    for f in frames:
        pg = frame_to_pdf.get(f['page_num'], f['page_num'])
        title = f['title']
        section = f['section']
        items = f.get('items', [])
        key_terms = ', '.join(items[:3]) if items else ''

        # Skip silent frames
        if f['is_titlepage'] or f['is_toc'] or f['is_qa']:
            continue
        if f['is_plain']:
            # Figure-only slide — note it
            lines.append(f"{pg:03d}: {section} | FIGURE | {title if title else 'figure slide'}")
            continue

        lines.append(f"{pg:03d}: {section} | {title} | {key_terms}")

    # Sort by page number
    lines.sort(key=lambda x: int(x[:3]))

    # Write
    output_path = os.path.join(video_dir, 'transcript_raw.txt')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"Transcript: {output_path} ({len(lines)} slides)")
    return output_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python gen_transcript.py <paper_dir>")
        sys.exit(1)
    generate_raw_transcript(sys.argv[1])
