---
name: pdf-to-markdown
description: Convert a PDF (especially academic papers) to clean Markdown using MinerU — preserves reading order, LaTeX equations, tables, and figures, and runs on the GPU. Use when the user asks to convert/extract a PDF to markdown/text. Invoke directly — no need for the user to type the skill name.
user-invocable: true
---

# PDF → Markdown (MinerU)

Converts PDFs to clean, structure-preserving Markdown using **MinerU**
(`opendatalab/MinerU`), the highest-fidelity open-source engine for academic
papers. Runs the **pipeline backend on the GPU** (CUDA). Output keeps
heading hierarchy, inline LaTeX math (`$...$`), figures, and tables (as HTML).

## When to use

- The user asks to "convert / extract / turn this PDF into markdown (or text)."
- Best fit: **academic papers** — multi-column layouts, LaTeX math, tables, references.
- Also handles images, `.docx`, `.pptx`, `.xlsx` (pass the file directly).

## How to run

MinerU lives in a dedicated isolated env and is driven through the bundled
`convert.py` wrapper, which calls MinerU's Python API **in-process**.

> Do **not** use the `mineru` CLI here — in 3.4.0 it spins up a local API server
> that returns `502 Bad Gateway` on Windows. The wrapper bypasses that.

```
"$MINERU_PYTHON" \
  "<SKILLS_DIR>/pdf-to-markdown/convert.py" \
  "<input.pdf>" "<output_dir>" --lang en

- Arg 1 — input file (pdf/image/docx/pptx/xlsx).
- Arg 2 — output directory.
- `--lang` — OCR language hint: `en` for English, `ch` for Chinese/mixed (default `ch`,
  which also handles English). Only affects OCR'd (scanned) pages.
- The script prints `DONE -> <path to .md>` on success.

### Output layout

```
<output_dir>/<name>/auto/<name>.md       # the markdown
<output_dir>/<name>/auto/images/          # extracted figures (referenced from the md)
<output_dir>/<name>/auto/<name>_*.json    # layout/content metadata (can be ignored)
```

## After converting

1. Read the printed `DONE -> ...md` path and report it to the user.
2. Optionally show a short preview (title + first headings).
3. Note where extracted images were written.
4. Quality is normally high: headings as `#`, inline math as `$...$`, tables as
   `<table>` HTML (with LaTeX inside cells). If a scanned PDF comes out garbled,
   re-run with `--method ocr`.

## Preflight / setup (only if something is missing)

- Interpreter with MinerU (env `MINERU_PYTHON`, default `python`).
- Verify CUDA: `$MINERU_PYTHON -c "import torch; print(torch.cuda.is_available())"` → `True`.
- Models are pre-downloaded to `~/.cache/huggingface/hub/` (one-time).

If the env is missing, recreate it (install `uv` first if needed):
```
uv venv --python 3.12 "<your-env>"
uv pip install --python "<your-env>/Scripts/python.exe" torch torchvision --index-url https://download.pytorch.org/whl/cu124
uv pip install --python "<your-env>/Scripts/python.exe" -U "mineru[core]"
"<your-env>/Scripts/mineru-models-download.exe" -s huggingface -m pipeline
```
Then set `MINERU_PYTHON` to `<your-env>/Scripts/python.exe` (Windows) or
`<your-env>/bin/python` (macOS/Linux). On macOS/Linux the models-download binary is
`mineru-models-download` (no `.exe` suffix).

## Troubleshooting
- **`cuda.is_available()` is False** → reinstall torch from the `cu124` index (see setup).
  The pipeline still runs on CPU if needed, just slower.
- **Model download fails / slow** → re-run `mineru-models-download` with `-s modelscope`.
- **Scanned PDF garbled** → add `--method ocr` to the `convert.py` call.
- **`vlm-engine` / `hybrid-engine` backends** → these need vLLM/SGLang (Linux); they 502
  on Windows. Stick with the default `pipeline` backend used by the wrapper.

## Lightweight fallback (simple / Office docs)

For non-paper files where layout fidelity doesn't matter, Microsoft's **MarkItDown**
is faster and simpler — but it mangles academic two-column PDFs and tables, so prefer
MinerU for papers:
```
"$MINERU_PYTHON" -m pip install markitdown
"$MINERU_PYTHON" -m markitdown "<input>" > out.md
```
