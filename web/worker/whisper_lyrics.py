"""faster-whisper 로 노래 가사를 받아 적는다 (음악 영상용).
  <whisper venv python> whisper_lyrics.py audio.mp3 out.json [model=small]
out.json = [{"start": s, "end": e, "text": "..."}]
"""
import json, os, sys
from faster_whisper import WhisperModel

audio, out = sys.argv[1], sys.argv[2]
name = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("P2Y_WHISPER_MODEL", "small")
m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2)
segs, info = m.transcribe(audio, vad_filter=True, beam_size=1, condition_on_previous_text=False)
res = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs if s.text.strip()]
json.dump({"language": info.language, "segments": res}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
print(f"{len(res)} segments, language={info.language}")
