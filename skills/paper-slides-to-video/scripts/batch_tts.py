"""Batch TTS with retry — scan a frame_dir for slide_*.txt files, generate MP3s using edge-tts.
Usage: uv run --project <skill-dir> python scripts/batch_tts.py <frame_dir> [--max-workers 8] [--max-retries 3]
"""
import sys, os, glob, time, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from slides_to_video import tts_slide


def retry_tts(text, mp3_path, max_retries=3, lang='zh'):
    """Retry TTS with exponential backoff. Returns True on success."""
    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(2 ** attempt)
        ok = tts_slide(text, mp3_path, lang=lang)
        if ok and os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
            return True
        if os.path.exists(mp3_path):
            try:
                os.remove(mp3_path)
            except OSError:
                pass
    return False


def batch_tts(frame_dir, max_workers=8, max_retries=3):
    """Process all slide_*.txt files in frame_dir to MP3s."""
    txt_files = sorted(glob.glob(os.path.join(frame_dir, 'slide_*.txt')))
    if not txt_files:
        print("No slide_*.txt files found")
        return 0, 0

    max_slide = max(int(os.path.basename(f).replace('slide_', '').replace('.txt', '')) for f in txt_files)

    # First pass: parallel batch
    tasks = []
    for i in range(1, max_slide + 1):
        txt = os.path.join(frame_dir, f'slide_{i:03d}.txt')
        mp3 = os.path.join(frame_dir, f'slide_{i:03d}.mp3')
        if os.path.exists(txt) and not os.path.exists(mp3):
            with open(txt, 'r', encoding='utf-8') as f:
                tasks.append((i, f.read(), mp3))

    if not tasks:
        # Check for 0-byte MP3s
        zero_bytes = [f for f in glob.glob(os.path.join(frame_dir, 'slide_*.mp3')) if os.path.getsize(f) == 0]
        if zero_bytes:
            print(f"No new TTS tasks, but {len(zero_bytes)} zero-byte MP3s found — retrying")
            for mp3 in zero_bytes:
                slide_num = int(os.path.basename(mp3).replace('slide_', '').replace('.mp3', ''))
                txt = os.path.join(frame_dir, f'slide_{slide_num:03d}.txt')
                if os.path.exists(txt):
                    os.remove(mp3)
                    with open(txt, 'r', encoding='utf-8') as f:
                        tasks.append((slide_num, f.read(), mp3))
        if not tasks:
            print("All MP3s up to date")
            return 0, 0

    print(f"TTS batch: {len(tasks)} slides ({max_workers} workers)")

    def do_tts(args):
        idx, text, mp3_path = args
        ok = retry_tts(text, mp3_path, max_retries=0)  # no retry in parallel
        return idx, ok

    fails = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(do_tts, t): t[0] for t in tasks}
        for f in as_completed(futures):
            idx, ok = f.result()
            if not ok:
                fails.append(idx)
            print(f'  slide_{idx:03d}: {"OK" if ok else "FAIL"}')

    # Second pass: sequential retry for failures
    if fails:
        print(f"\nRetrying {len(fails)} failed slides sequentially (max {max_retries} attempts)...")
        still_failed = []
        for idx in fails:
            txt = os.path.join(frame_dir, f'slide_{idx:03d}.txt')
            mp3 = os.path.join(frame_dir, f'slide_{idx:03d}.mp3')
            with open(txt, 'r', encoding='utf-8') as f:
                text = f.read()
            ok = retry_tts(text, mp3, max_retries=max_retries)
            print(f'  slide_{idx:03d}: {"OK" if ok else "ALL RETRIES FAILED"}')
            if not ok:
                still_failed.append(idx)

        print(f"\nDone: {len(tasks)} total, {len(still_failed)} unrecovered failures")
        return len(tasks), len(still_failed)
    else:
        print(f"\nDone: {len(tasks)} total, 0 failures")
        return len(tasks), 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Batch TTS with retry')
    parser.add_argument('frame_dir', help='Directory containing slide_*.txt and slide_*.png files')
    parser.add_argument('--max-workers', type=int, default=8)
    parser.add_argument('--max-retries', type=int, default=3)
    args = parser.parse_args()
    batch_tts(args.frame_dir, args.max_workers, args.max_retries)
