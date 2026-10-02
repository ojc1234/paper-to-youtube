#!/usr/bin/env python3
"""arXiv에서 새 논문을 가져와 '이미 다룬 논문 / 기존 유튜브 영상과 비슷한 논문'을 걸러낸 뒤
paper-to-bilibili 호환 papers.json 을 만든다.

사용 예:
    python fetch_papers.py --category quant-ph --max 1 --root "D:/paper-youtube"
    python fetch_papers.py --category quant-ph --max 1 --root ... --youtube-check   # YOUTUBE_API_KEY 필요

중복 판정:
  1) history.json 에 같은 arXiv ID 가 있으면 제외
  2) history.json 의 기존 영상 제목·요약과 TF-IDF 코사인 유사도 >= --threshold 이면 제외
  3) --youtube-check: YouTube 검색 상위 결과(제목+설명)와 유사도 >= --threshold 이면 제외
의존성: 표준 라이브러리 + scikit-learn(없으면 단순 단어 겹침 비율로 대체)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_API = "https://export.arxiv.org/api/query?{q}"
YT_SEARCH = "https://www.googleapis.com/youtube/v3/search?{q}"
UA = "paper-to-youtube/1.0 (personal research channel)"


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_arxiv(category: str, limit: int) -> list[dict]:
    q = urllib.parse.urlencode({
        "search_query": f"cat:{category}",
        "sortBy": "submittedDate", "sortOrder": "descending",
        "start": 0, "max_results": limit,
    })
    root = ET.fromstring(http_get(ARXIV_API.format(q=q)))
    papers = []
    for e in root.findall(f"{ATOM}entry"):
        abs_url = e.findtext(f"{ATOM}id", "").strip()
        m = re.search(r"abs/(\d{4}\.\d{4,5})(v\d+)?", abs_url)
        if not m:
            continue
        papers.append({
            "arxiv_id": m.group(1),
            "title": " ".join(e.findtext(f"{ATOM}title", "").split()),
            "summary": " ".join(e.findtext(f"{ATOM}summary", "").split()),
            "authors": [a.findtext(f"{ATOM}name", "") for a in e.findall(f"{ATOM}author")],
            "published": e.findtext(f"{ATOM}published", ""),
            "url": abs_url,
        })
    return papers


def similarity(a: str, corpus: list[str]) -> float:
    """a 와 corpus 각 문서의 최대 유사도(0~1)."""
    corpus = [c for c in corpus if c and c.strip()]
    if not corpus:
        return 0.0
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        m = vec.fit_transform([a] + corpus)
        return float(cosine_similarity(m[0:1], m[1:]).max())
    except ImportError:
        tok = lambda s: set(re.findall(r"[a-z]{3,}", s.lower()))
        ta = tok(a)
        return max((len(ta & tok(c)) / max(1, len(ta | tok(c))) for c in corpus), default=0.0)


def youtube_corpus(query: str, api_key: str, n: int = 10) -> list[dict]:
    q = urllib.parse.urlencode({
        "part": "snippet", "q": query, "type": "video",
        "maxResults": n, "key": api_key,
    })
    data = json.loads(http_get(YT_SEARCH.format(q=q)))
    return [{
        "id": it["id"].get("videoId"),
        "text": f'{it["snippet"]["title"]} {it["snippet"]["description"]}',
        "title": it["snippet"]["title"],
    } for it in data.get("items", [])]


def load_history(path: Path) -> list[dict]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def short_name(title: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title)
             if w.lower() not in {"a", "an", "the", "of", "for", "and", "on", "in", "with", "to", "via"}]
    return " ".join(words[:3]) or "paper"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="quant-ph")
    ap.add_argument("--max", type=int, default=1, help="오늘 만들 영상 수")
    ap.add_argument("--scan", type=int, default=50, help="arXiv 에서 훑어볼 최신 논문 수")
    ap.add_argument("--root", default=os.environ.get("PP_ROOT", "."), help="작업 루트 폴더")
    ap.add_argument("--threshold", type=float, default=0.35, help="유사도 기준 (TF-IDF 코사인)")
    ap.add_argument("--youtube-check", action="store_true", help="YouTube 검색 결과와도 비교 (YOUTUBE_API_KEY)")
    ap.add_argument("--keywords", default="", help="쉼표로 구분한 관심 키워드 (있으면 해당 논문 우선)")
    ap.add_argument("--out", default=None, help="papers.json 경로 (기본: <root>/papers_today.json)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    work = root / "논문영상"
    work.mkdir(parents=True, exist_ok=True)
    history_path = root / "history.json"
    history = load_history(history_path)
    done_ids = {h.get("arxiv_id") for h in history}
    hist_texts = [f'{h.get("title","")} {h.get("summary","")}' for h in history]

    candidates = fetch_arxiv(args.category, args.scan)
    kws = [k.strip().lower() for k in args.keywords.split(",") if k.strip()]
    if kws:
        candidates.sort(key=lambda p: -sum(k in (p["title"] + p["summary"]).lower() for k in kws))

    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if args.youtube_check and not api_key:
        print("[WARN] --youtube-check 지정됐지만 YOUTUBE_API_KEY 가 없어 YouTube 비교는 건너뜀", file=sys.stderr)

    selected, skipped = [], []
    for p in candidates:
        if len(selected) >= args.max:
            break
        text = f'{p["title"]} {p["summary"]}'
        if p["arxiv_id"] in done_ids:
            skipped.append((p, "이미 만든 논문"))
            continue
        s_hist = similarity(text, hist_texts)
        if s_hist >= args.threshold:
            skipped.append((p, f"내 기존 영상과 유사 {s_hist:.2f}"))
            continue
        if args.youtube_check and api_key:
            try:
                yt = youtube_corpus(p["title"], api_key)
                s_yt = similarity(text, [v["text"] for v in yt])
                if s_yt >= args.threshold:
                    skipped.append((p, f"유튜브 기존 영상과 유사 {s_yt:.2f}"))
                    continue
                p["youtube_max_similarity"] = round(s_yt, 3)
            except Exception as e:  # 쿼터 초과 등은 비교 없이 진행
                print(f"[WARN] YouTube 검색 실패: {e}", file=sys.stderr)
        p["history_max_similarity"] = round(s_hist, 3)
        dir_name = f'arXiv {p["published"][:4]} - {short_name(p["title"])}'
        p["dir_name"] = dir_name
        p["dir_path"] = str((work / dir_name).resolve())
        Path(p["dir_path"]).mkdir(parents=True, exist_ok=True)
        (Path(p["dir_path"]) / "paper.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        selected.append(p)
        time.sleep(0.2)

    for p, why in skipped:
        print(f'SKIP {p["arxiv_id"]} ({why}): {p["title"][:70]}')
    for p in selected:
        print(f'PICK {p["arxiv_id"]}: {p["title"]}')

    out = Path(args.out) if args.out else root / "papers_today.json"
    out.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PAPERS_JSON: {out}")
    return 0 if selected else 3


if __name__ == "__main__":
    sys.exit(main())
