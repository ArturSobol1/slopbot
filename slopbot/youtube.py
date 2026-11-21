"""Upload a finished run to YouTube as a Short (YouTube Data API v3, OAuth desktop flow).

One-time setup is in README.md ("Uploading to YouTube"). The first upload opens your browser so
you can sign in to Google yourself; the resulting token is saved in secrets/youtube_token.json and
reused after that. Neither the token nor the client secret is ever printed.
"""
import json
import random
import time
from datetime import datetime
from pathlib import Path

from . import describe
from .config import ROOT, env

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
SECRETS = ROOT / "secrets"
PRIVACY = ("private", "unlisted", "public")
RETRY_STATUS = {500, 502, 503, 504}
MAX_RETRIES = 8


def _client_secret() -> Path:
    if env("YOUTUBE_CLIENT_SECRET"):
        candidates = [Path(env("YOUTUBE_CLIENT_SECRET"))]
    else:
        candidates = [SECRETS / "client_secret.json", ROOT / "client_secret.json",
                      *sorted(SECRETS.glob("client_secret*.json")), *sorted(ROOT.glob("client_secret*.json"))]
    for path in candidates:
        path = path if path.is_absolute() else ROOT / path
        if path.is_file():
            return path
    raise SystemExit("No YouTube client secret found. Follow 'Uploading to YouTube' in README.md and save "
                     f"the downloaded file as {SECRETS / 'client_secret.json'}.")


def _token_file() -> Path:
    path = Path(env("YOUTUBE_TOKEN_FILE", str(SECRETS / "youtube_token.json")))
    return path if path.is_absolute() else ROOT / path


def _credentials():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token = _token_file()
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:  # revoked, or a "Testing" OAuth app's 7-day token ran out
            print("[youtube] saved sign-in expired, signing in again")
            creds = None
    if not creds or not creds.valid:
        secret = _client_secret()
        print("[youtube] opening your browser - sign in to Google with the channel's account "
              "and allow uploads")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True, prompt="consent",
                                      success_message="SlopBot can now upload. You can close this tab.")
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _title(title: str) -> str:
    title = title.replace("<", "").replace(">", "").strip()[:100]
    if "#shorts" not in title.lower() and len(title) + len(" #Shorts") <= 100:
        title += " #Shorts"
    return title


def request_body(run_dir: Path, privacy: str = "private", redescribe: bool = False) -> dict:
    """The video metadata that would be sent to YouTube (creates youtube.json if it's missing)."""
    if privacy not in PRIVACY:
        raise SystemExit(f"--privacy must be one of {', '.join(PRIVACY)}")
    meta = describe.load_or_write(run_dir, redo=redescribe)
    description = describe.upload_description(meta)  # credits without links; also fixes older youtube.json
    if len(description.encode("utf-8")) > 5000:
        raise SystemExit(f"The description is {len(description.encode('utf-8'))} bytes; YouTube allows 5000. "
                         "Shorten the description in youtube.json (keep the credits - they're required).")
    return {
        "snippet": {
            "title": _title(meta["title"]),
            "description": description,
            "tags": meta.get("tags", []),
            "categoryId": env("YOUTUBE_CATEGORY_ID", "27"),  # 27 = Education, 28 = Science & Technology
        },
        "status": {
            "privacyStatus": privacy,             # always explicit: the API defaults to public
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,       # AI disclosure: AI voice and AI-made visuals
            "embeddable": True,
        },
    }


def upload(run_dir: str | Path, privacy: str = "private", dry_run: bool = False,
           force: bool = False, redescribe: bool = False) -> str | None:
    """Upload run_dir/final.mp4. Returns the video URL (None for a dry run)."""
    run_dir = Path(run_dir).resolve()
    video = run_dir / "final.mp4"
    if not video.exists():
        raise SystemExit(f"No final.mp4 in {run_dir}")
    record = run_dir / "youtube_upload.json"
    if record.exists() and not force and not dry_run:
        done = json.loads(record.read_text(encoding="utf-8"))
        raise SystemExit(f"Already uploaded: {done.get('url')} ({done.get('privacy')}). "
                         "Pass --force to upload it again as a new video.")

    body = request_body(run_dir, privacy, redescribe)
    if dry_run:
        size = video.stat().st_size / 1e6
        print(f"[youtube] dry run - would upload {video} ({size:.1f} MB) with:")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        return None

    import httplib2
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=_credentials(), cache_discovery=False)
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"[youtube] uploading \"{body['snippet']['title']}\" as {privacy}...")
    response, retries = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"[youtube] {status.progress() * 100:.0f}%")
            retries = 0
        except HttpError as exc:
            if exc.resp.status not in RETRY_STATUS:
                raise RuntimeError(f"YouTube refused the upload: {_reason(exc)}") from None
            retries = _backoff(retries, f"HTTP {exc.resp.status}")
        except (OSError, httplib2.HttpLib2Error) as exc:  # dropped connection: resume where it stopped
            retries = _backoff(retries, type(exc).__name__)

    video_id = response["id"]
    url = f"https://youtube.com/shorts/{video_id}"
    actual = response.get("status", {}).get("privacyStatus", privacy)
    record.write_text(json.dumps({
        "id": video_id, "url": url, "studio": f"https://studio.youtube.com/video/{video_id}/edit",
        "privacy": actual, "title": body["snippet"]["title"],
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[youtube] done ({actual}): {url}")
    if actual != privacy:
        print(f"[youtube] note: YouTube set it to {actual}, not {privacy}. Change it in YouTube Studio: "
              f"https://studio.youtube.com/video/{video_id}/edit")
    return url


def _backoff(retries: int, what: str) -> int:
    retries += 1
    if retries > MAX_RETRIES:
        raise RuntimeError(f"Upload kept failing ({what}); gave up after {MAX_RETRIES} retries. "
                           "Run upload.py on this folder again later.")
    wait = min(60, 2 ** retries) + random.random()
    print(f"[youtube] {what}, retrying in {wait:.0f}s")
    time.sleep(wait)
    return retries


def _reason(exc) -> str:
    try:
        error = json.loads(exc.content)["error"]
        reasons = ", ".join(e.get("reason", "") for e in error.get("errors", []))
        message = f"{error.get('message', '')} ({reasons})" if reasons else error.get("message", "")
    except Exception:
        message = str(exc)
    if "youtubeSignupRequired" in message:
        message += (" - the Google account you signed in with has no YouTube channel. Create one at "
                    "https://www.youtube.com/create_channel, or if your channel is a Brand Account, delete "
                    f"{_token_file()} and sign in again, picking that channel.")
    if "uploadLimitExceeded" in message:
        message += (" - your channel's 24-hour upload limit is used up (new channels get a low one). Try again "
                    "later, or verify the channel with a phone number at https://www.youtube.com/verify "
                    "to raise it.")
    elif "quotaExceeded" in message:
        message += " - the Google Cloud project's daily API quota is used up; try again tomorrow."
    return message
