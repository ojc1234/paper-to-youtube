#!/usr/bin/env python3
"""본편 + 쇼츠를 YouTube 에 업로드하고, 썸네일·설명 링크·댓글·history 를 처리한다.

python youtube_upload.py <paper_dir> [--dry-run] [--privacy private|unlisted|public]

필요 파일 (paper_dir/video/ 아래):
  <name>_narrated.mp4     본편 (paper-slides-to-video 결과)
  shorts.mp4              쇼츠 (make_shorts.py 결과, 없으면 쇼츠 생략)
  thumbnail.jpg           썸네일 (make_thumbnail.py 결과)
  youtube_meta.json       {
                            "title": "...", "description": "...", "tags": ["양자컴퓨팅", ...],
                            "categoryId": "28",                # 28 = 과학기술
                            "shorts": {"title": "... #shorts", "description": "...", "tags": [...]},
                            "shorts_comment": "본편 보기 👉 {main_url}"   # 선택
                          }
인증: OAuth 클라이언트 파일(데스크톱 앱) 경로를 YT_CLIENT_SECRET 환경변수 또는 --client-secret 로 지정.
      첫 실행 때 브라우저가 열려 로그인/허용 → 토큰은 YT_TOKEN (기본 ~/.paper-to-youtube/token.json) 에 저장.

쇼츠의 '관련 동영상' 연결은 YouTube API 가 지원하지 않아 Studio 에서 직접 해야 한다.
대신 쇼츠 설명란 첫 줄과 댓글에 본편 링크를 넣는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
CFG_DIR = Path(os.environ.get("PAPER_YT_HOME", Path.home() / ".paper-to-youtube"))


def service(client_secret: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token = Path(os.environ.get("YT_TOKEN", CFG_DIR / "token.json"))
    creds = None
    if token.is_file():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
            creds = flow.run_local_server(port=0)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def upload(yt, path: Path, snippet: dict, privacy: str) -> str:
    from googleapiclient.http import MediaFileUpload
    body = {
        "snippet": snippet,
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"  업로드 {path.name}: {int(status.progress() * 100)}%")
    return resp["id"]


def snippet_of(meta: dict, default_tags: list[str]) -> dict:
    title = meta["title"].strip()
    if len(title) > 100:
        raise ValueError(f"제목이 100자를 넘습니다 ({len(title)}자): {title}")
    desc = meta.get("description", "")
    if len(desc.encode("utf-8")) > 5000:
        raise ValueError("설명이 5000바이트를 넘습니다")
    if "<" in title + desc or ">" in title + desc:
        raise ValueError("제목/설명에 < > 문자는 쓸 수 없습니다")
    return {
        "title": title, "description": desc,
        "tags": meta.get("tags", default_tags)[:30],
        "categoryId": str(meta.get("categoryId", "28")),
        "defaultLanguage": meta.get("defaultLanguage", "ko"),
        "defaultAudioLanguage": meta.get("defaultAudioLanguage", "ko"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paper_dir")
    ap.add_argument("--privacy", default=os.environ.get("YT_PRIVACY", "private"),
                    choices=["private", "unlisted", "public"])
    ap.add_argument("--client-secret", default=os.environ.get("YT_CLIENT_SECRET", str(CFG_DIR / "client_secret.json")))
    ap.add_argument("--history", default=None, help="history.json 경로 (기본: paper_dir 의 2단계 상위)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    pdir = Path(a.paper_dir).resolve()
    vdir = pdir / "video"
    meta = json.loads((vdir / "youtube_meta.json").read_text(encoding="utf-8"))
    mains = sorted(p for p in vdir.glob("*_narrated.mp4") if not p.name.startswith("."))
    if not mains:
        raise FileNotFoundError(f"본편 영상(*_narrated.mp4)이 없습니다: {vdir}")
    main_mp4 = mains[0]
    shorts_mp4 = vdir / "shorts.mp4"
    thumb = vdir / "thumbnail.jpg"
    tags = meta.get("tags", [])
    main_snip = snippet_of(meta, tags)
    shorts_meta = meta.get("shorts")
    if shorts_meta and shorts_mp4.is_file():
        shorts_meta = {**shorts_meta, "categoryId": meta.get("categoryId", "28")}
        snippet_of(shorts_meta, tags)  # 업로드 전에 제목 길이 등 검증

    print(f"본편  : {main_mp4.name} ({main_mp4.stat().st_size/1e6:.1f} MB)")
    print(f"썸네일: {'있음' if thumb.is_file() else '없음'}")
    print(f"쇼츠  : {'있음' if shorts_meta and shorts_mp4.is_file() else '없음'}")
    print(f"공개  : {a.privacy}")
    if a.dry_run:
        print("DRY-RUN OK (업로드하지 않음)")
        return 0

    if not Path(a.client_secret).is_file():
        raise FileNotFoundError(f"OAuth 클라이언트 파일이 없습니다: {a.client_secret}")
    yt = service(a.client_secret)

    result = {"uploaded_at": datetime.now(timezone.utc).isoformat()}
    vid = upload(yt, main_mp4, main_snip, a.privacy)
    main_url = f"https://youtu.be/{vid}"
    result["main"] = {"id": vid, "url": main_url}
    print("본편 업로드 완료:", main_url)
    if thumb.is_file():
        from googleapiclient.http import MediaFileUpload
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb))).execute()
            print("썸네일 설정 완료")
        except Exception as e:
            print(f"[WARN] 썸네일 설정 실패 (채널 전화번호 인증이 필요할 수 있음): {e}")

    if shorts_meta and shorts_mp4.is_file():
        sdesc = f"▶ 본편 전체 리뷰: {main_url}\n\n" + shorts_meta.get("description", "")
        s_snip = snippet_of({**shorts_meta, "description": sdesc}, tags)
        sid = upload(yt, shorts_mp4, s_snip, a.privacy)
        result["shorts"] = {"id": sid, "url": f"https://youtube.com/shorts/{sid}"}
        print("쇼츠 업로드 완료:", result["shorts"]["url"])
        comment = meta.get("shorts_comment", "본편 전체 리뷰 보기 👉 {main_url}").format(main_url=main_url)
        try:
            yt.commentThreads().insert(part="snippet", body={"snippet": {
                "videoId": sid, "topLevelComment": {"snippet": {"textOriginal": comment}}}}).execute()
            print("쇼츠 댓글(본편 링크) 작성 완료 — 고정은 Studio/앱에서 직접")
        except Exception as e:
            print(f"[WARN] 댓글 작성 실패 (비공개 영상은 댓글이 막힐 수 있음): {e}")
        print("※ 쇼츠 '관련 동영상' 연결은 YouTube Studio > 쇼츠 > 세부정보 에서 본편을 직접 선택하세요.")

    (vdir / "upload_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    hist = Path(a.history) if a.history else pdir.parent.parent / "history.json"
    entries = json.loads(hist.read_text(encoding="utf-8")) if hist.is_file() else []
    src = {}
    pj = pdir / "paper.json"
    if pj.is_file():
        src = json.loads(pj.read_text(encoding="utf-8"))
    entries.append({
        "arxiv_id": src.get("arxiv_id", ""), "title": src.get("title", main_snip["title"]),
        "summary": src.get("summary", meta.get("description", "")[:1500]),
        "video_title": main_snip["title"], "main_url": main_url,
        "shorts_url": result.get("shorts", {}).get("url", ""), "uploaded_at": result["uploaded_at"],
    })
    hist.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"history 갱신: {hist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
