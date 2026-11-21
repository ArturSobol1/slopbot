"""Chooses a picture for every line of an explainer.

For each line it gathers candidates from free sources:
  - Wikimedia Commons videos: REAL footage of specific things (e.g. official Air Force B-2 video)
  - Wikimedia Commons photos: real photos of specific things
  - Pixabay: generic stock video clips
then shows Claude a numbered contact sheet per line and lets it pick the best match.

Real videos are often long B-roll reels with several shots and title cards, so for every chosen
video the shots inside it are detected and Claude picks the one that fits the line.
"""
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from .claude import ask
from .config import env
from .ff import FFMPEG
from .voice import strip_tags

USER_AGENT = "SlopBot/0.3 (personal video tool)"
MAX_ORIGINAL_BYTES = 150 * 1024 * 1024  # bigger Commons originals use a transcoded copy instead


@dataclass
class Candidate:
    kind: str           # "photo", "real" (Commons video) or "stock" (Pixabay video)
    id: str
    thumb_url: str
    credit: str
    width: int
    height: int
    duration: float = 0
    url: str = ""       # direct file to download (videos)
    title: str = ""     # Commons file title


@dataclass
class Shot:
    kind: str           # "photo" or "video"
    path: Path
    portrait: bool
    start: float = 0.0  # where to start inside the video


LABELS = {"photo": ("PHOTO", (255, 220, 0)), "real": ("REAL VIDEO", (60, 220, 90)),
          "stock": ("STOCK VIDEO", (0, 200, 255))}

_last_call = [0.0]


def _get(session: requests.Session, url: str, polite: bool = False, timeout: float = 30,
         **kwargs) -> requests.Response:
    """GET with a few short retries on rate limits. `polite` spaces calls out (Wikimedia asks for this).

    Waits are capped, so a server asking for a long pause makes this fail fast instead of hanging.
    """
    for attempt in range(4):
        if polite:
            wait = 1.0 - (time.monotonic() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.monotonic()
        resp = session.get(url, timeout=timeout, **kwargs)
        if resp.status_code not in (429, 502, 503, 504):
            resp.raise_for_status()
            return resp
        retry_after = resp.headers.get("Retry-After", "")
        wait = max(float(retry_after) if retry_after.isdigit() else 0, 3 * 2 ** attempt)  # 3, 6, 12, 24 s
        print(f"          {resp.status_code} from {url.split('/')[2]}, retrying in {min(wait, 20):.0f}s")
        time.sleep(min(wait, 20))
    resp.raise_for_status()
    return resp


def _download(session: requests.Session, url: str, out: Path) -> None:
    out.write_bytes(_get(session, url, polite=True, timeout=300).content)


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


class Commons:
    API = "https://commons.wikimedia.org/w/api.php"

    def __init__(self, session: requests.Session):
        self.s = session
        self.cache: dict[tuple, list[Candidate]] = {}

    def _query(self, query: str, filetype: str, prop: str, prefix: str) -> list[dict]:
        try:
            resp = _get(self.s, self.API, polite=True, params={
                "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                "gsrlimit": 12, "gsrsearch": f"{query} filetype:{filetype}", "prop": prop,
                f"{prefix}prop": "url|size|mime|extmetadata" + ("|derivatives" if prefix == "vi" else ""),
                f"{prefix}urlwidth": 480, f"{prefix}extmetadatafilter": "LicenseShortName|Artist"})
        except requests.RequestException as exc:
            print(f"          Wikimedia search failed for {query!r}: {exc}")
            return []
        pages = sorted(resp.json().get("query", {}).get("pages", {}).values(), key=lambda p: p["index"])
        return [(page, (page.get(prop) or [{}])[0]) for page in pages]

    @staticmethod
    def _credit(info: dict) -> str:
        meta = info.get("extmetadata", {})
        license_ = meta.get("LicenseShortName", {}).get("value", "see file page")
        artist = _strip_html(meta.get("Artist", {}).get("value", "unknown"))[:80]
        return f"{info.get('descriptionurl', '')} - {artist} ({license_})"

    def photos(self, query: str, limit: int = 3) -> list[Candidate]:
        if not query.strip():
            return []
        key = ("photo", query)
        if key not in self.cache:
            found = []
            for page, info in self._query(query, "bitmap", "imageinfo", "ii"):
                w, h = info.get("width", 0), info.get("height", 0)
                if info.get("mime") not in ("image/jpeg", "image/png") or min(w, h) < 700 or not 0.4 < w / h < 2.6:
                    continue
                found.append(Candidate("photo", page["title"], info["thumburl"], self._credit(info), w, h,
                                       title=page["title"]))
            self.cache[key] = found
        return self.cache[key][:limit]

    def videos(self, query: str, limit: int = 3) -> list[Candidate]:
        if not query.strip():
            return []
        key = ("video", query)
        if key not in self.cache:
            found = []
            for page, info in self._query(query, "video", "videoinfo", "vi"):
                w, h, dur = info.get("width", 0), info.get("height", 0), info.get("duration", 0) or 0
                if min(w, h) < 360 or not 2 <= dur <= 900 or not info.get("thumburl"):
                    continue
                url = info["url"]
                if info.get("size", 0) > MAX_ORIGINAL_BYTES:  # use the sharpest transcode up to 1080p
                    derivs = [d for d in info.get("derivatives", [])
                              if d.get("height", 0) <= 1080 and d.get("src")]
                    if not derivs:
                        continue
                    best = max(derivs, key=lambda d: d.get("height", 0))
                    url, w, h = best["src"], best.get("width", w), best.get("height", h)
                found.append(Candidate("real", page["title"], info["thumburl"], self._credit(info), w, h,
                                       duration=dur, url=url, title=page["title"]))
            self.cache[key] = found
        return self.cache[key][:limit]

    def download_photo(self, cand: Candidate, out: Path) -> Path:
        resp = _get(self.s, self.API, polite=True, params={
            "action": "query", "format": "json", "titles": cand.title,
            "prop": "imageinfo", "iiprop": "url", "iiurlwidth": 2160})
        info = next(iter(resp.json()["query"]["pages"].values()))["imageinfo"][0]
        url = info.get("thumburl") if cand.width > 2160 else info["url"]
        _download(self.s, url or info["url"], out)
        return out


class Pixabay:
    def __init__(self, session: requests.Session, key: str):
        self.s = session
        self.key = key
        self.cache: dict[str, list[Candidate]] = {}

    def videos(self, query: str, limit: int = 3) -> list[Candidate]:
        if not query.strip():
            return []
        if query not in self.cache:
            try:
                resp = _get(self.s, "https://pixabay.com/api/videos/", params={
                    "key": self.key, "q": query[:100], "per_page": 15, "safesearch": "true"})
            except requests.RequestException as exc:
                print(f"          Pixabay search failed for {query!r}: {exc}")
                return []
            found = []
            for hit in resp.json().get("hits", []):
                if hit.get("isLowQuality"):
                    continue
                files = [f for f in hit.get("videos", {}).values() if f.get("url") and f.get("width")]
                sharp = [f for f in files if min(f["width"], f["height"]) >= 1080]
                file = min(sharp, key=lambda f: f["width"] * f["height"]) if sharp else \
                    max(files, key=lambda f: f["width"] * f["height"], default=None)
                thumb = next((f.get("thumbnail") for f in files if f.get("thumbnail")), None)
                if file and thumb:
                    found.append(Candidate("stock", f"pixabay-{hit['id']}", thumb,
                                           f"{hit['pageURL']} - {hit.get('user', 'unknown')} (Pixabay License)",
                                           file["width"], file["height"], hit.get("duration", 0), file["url"]))
            self.cache[query] = found
        return self.cache[query][:limit]


def _font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _contact_sheet(thumbs: list, labels: list[tuple[str, tuple]], out: Path) -> None:
    tile_w, tile_h, cols = 420, 300, 4
    rows = max((len(labels) + cols - 1) // cols, 1)
    sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), (30, 30, 30))
    draw = ImageDraw.Draw(sheet)
    font = _font(28)
    for n, (thumb, (text, color)) in enumerate(zip(thumbs, labels)):
        x, y = (n % cols) * tile_w, (n // cols) * tile_h
        if thumb:
            t = thumb.copy()
            t.thumbnail((tile_w - 10, tile_h - 10))
            sheet.paste(t, (x + (tile_w - t.width) // 2, y + (tile_h - t.height) // 2))
        label = f" {n + 1} {text} "
        draw.rectangle(draw.textbbox((x + 8, y + 8), label, font=font), fill=color)
        draw.text((x + 8, y + 8), label, fill=(0, 0, 0), font=font)
    sheet.save(out, quality=85)


def _scene_shots(path: Path, duration: float, max_shots: int = 8) -> list[tuple[float, float]]:
    """Splits a video into its separate shots using ffmpeg's scene-change detection."""
    proc = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-an", "-vf",
                           "scale=320:-2,select='gt(scene,0.32)',showinfo", "-f", "null", "-"],
                          capture_output=True, text=True, errors="replace")
    cuts = [float(t) for t in re.findall(r"pts_time:([\d.]+)", proc.stderr)]
    bounds = [0.0] + [c for c in cuts if 0.5 < c < duration - 0.5] + [duration]
    shots = [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a >= 1.0]
    if len(shots) > max_shots:  # keep the longest shots, in order
        keep = set(sorted(shots, key=lambda s: s[1] - s[0], reverse=True)[:max_shots])
        shots = [s for s in shots if s in keep]
    return shots or [(0.0, duration)]


def _frame_at(path: Path, t: float) -> Image.Image | None:
    proc = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
                           "-frames:v", "1", "-vf", "scale=480:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                          capture_output=True)
    try:
        return Image.open(BytesIO(proc.stdout)).convert("RGB")
    except Exception:
        return None


PICK_SYSTEM = """\
You are the picture editor for a short vertical explainer video. For every narration line you get a
contact sheet of numbered candidates:
  REAL VIDEO = real footage of the actual thing (shown as one still frame; it may contain other shots too)
  PHOTO = a real photo
  STOCK VIDEO = a generic stock clip
Choose the candidate that best shows what that line is talking about, as a viewer would expect to see it.
- Accuracy first: never pick something that contradicts the narration (a different aircraft model,
  an animal when the line is about a plane, a jet when it talks about a bird...).
- Moving footage keeps viewers watching: when a REAL VIDEO and a PHOTO fit equally well, pick the video.
  Real footage or photos of the actual subject beat generic stock clips; stock is fine for generic ideas
  (radar, clouds, maps, engines) when it genuinely matches.
- Reject green screens, logos, title cards, text-heavy frames, diagrams with tiny text, and collages.
- Prefer variety: avoid picking the same file for different lines when there is a good alternative.
- Answer 0 when nothing on the sheet fits.
"""

SHOT_SYSTEM = """\
You are the picture editor for a short vertical explainer video. Each chosen video is a reel of
several shots; for every narration line you get a sheet of numbered frames, one per shot, with the
shot's start time and length. Pick the shot that best shows what the line says.
- Skip title cards, slates, text overlays, black or blurry frames, and logos.
- A shot at least as long as the line needs is best; shorter ones run into the next shot.
- When several lines use the same video, give them different shots.
- Answer 0 if no shot is usable.
"""

_PICKS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["picks"], "properties": {
    "picks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["line", "choice"],
        "properties": {"line": {"type": "integer"}, "choice": {"type": "integer"}}}}}}


def choose_shots(script: dict, run_dir: Path, durations: list[float],
                 only: list[int] | None = None) -> tuple[list[Shot | None], list[str]]:
    """Picks footage for every line, or only for the line indexes in `only`."""
    key = env("PIXABAY_API_KEY")
    if not key:
        raise SystemExit("The explainer format needs PIXABAY_API_KEY in .env (free: sign up at "
                         "https://pixabay.com, then see https://pixabay.com/api/docs/), or run with --offline.")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    commons, pixabay = Commons(session), Pixabay(session, key)
    lines = script["lines"]
    subject = script.get("subject", "")
    active = set(only) if only is not None else set(range(len(lines)))

    # 1. Candidates for each line: real videos, real photos, stock clips
    per_line: list[list[Candidate]] = []
    for i, line in enumerate(lines):
        if i not in active:
            per_line.append([])
            continue
        specific = line.get("photo", "")
        footage = line.get("footage") or line.get("visual", "")  # "visual" = older scripts
        real = commons.videos(specific, 3) or commons.videos(subject, 3)
        photos = commons.photos(specific, 3)
        if len(photos) < 2 and subject:
            photos += [c for c in commons.photos(subject, 4) if c.id not in {p.id for p in photos}][:2]
        stock = pixabay.videos(footage, 3) or pixabay.videos(" ".join(footage.split()[:1]), 3)
        per_line.append(real + photos + stock)
        print(f"[footage] {i + 1}/{len(lines)} {len(real)} real videos, {len(photos)} photos, {len(stock)} stock clips")

    # 2. Contact sheets
    sheet_dir = run_dir / "sheets"
    sheet_dir.mkdir(exist_ok=True)

    def fetch_thumb(url):
        try:
            return url, Image.open(BytesIO(_get(session, url).content)).convert("RGB")
        except Exception:
            return url, None

    with ThreadPoolExecutor(3) as pool:
        thumbs = dict(pool.map(fetch_thumb, {c.thumb_url for cands in per_line for c in cands}))
    sheets = {}
    for i in sorted(active):
        path = sheet_dir / f"line_{i + 1:02}.jpg"
        _contact_sheet([thumbs.get(c.thumb_url) for c in per_line[i]], [LABELS[c.kind] for c in per_line[i]], path)
        sheets[i] = path

    # 3. Claude picks a candidate per line
    print("[footage] Claude is choosing the best picture for each line...")
    prompt = "Narration lines and their contact sheets:\n" + "\n".join(
        f"Line {i + 1} ({sheets[i].name}, {len(per_line[i])} candidates): {strip_tags(lines[i]['text'])}"
        for i in sorted(active))
    picks = {p["line"]: p["choice"]
             for p in ask(PICK_SYSTEM, prompt, _PICKS_SCHEMA, images=list(sheets.values()))["picks"]}

    # 4. Download the winners (fallback: a photo of the main subject)
    fallback_pool = commons.photos(subject, 6) if subject else []
    clip_dir = run_dir / "clips"
    clip_dir.mkdir(exist_ok=True)
    chosen: list[Candidate | None] = []
    files: dict[str, Path] = {}
    for i, cands in enumerate(per_line):
        if i not in active:
            chosen.append(None)
            continue
        choice = picks.get(i + 1, 0)
        cand = cands[choice - 1] if 1 <= choice <= len(cands) else None
        if cand is None and fallback_pool:
            used = {c.id for c in chosen if c}
            cand = next((c for c in fallback_pool if c.id not in used), fallback_pool[0])
            print(f"          line {i + 1}: nothing fit, using a photo of {subject!r}")
        # If the pick can't be downloaded, try the line's other candidates, then photos of the subject.
        options = [cand] + [c for c in cands if c is not cand] + fallback_pool if cand is not None else []
        cand = None
        for option in options:
            if option.id in files:
                cand = option
                break
            try:
                if option.kind == "photo":
                    path = clip_dir / f"line_{i + 1:02}{Path(option.title).suffix.lower()}"
                    commons.download_photo(option, path)
                else:
                    suffix = Path(option.url.split("?")[0]).suffix.lower() or ".mp4"
                    path = clip_dir / f"line_{i + 1:02}{suffix}"
                    print(f"          line {i + 1}: downloading {LABELS[option.kind][0].lower()}")
                    _download(session, option.url, path)
                files[option.id] = path
                cand = option
                break
            except Exception as exc:
                print(f"          line {i + 1}: download failed ({str(exc)[:90]}), trying the next picture")
        if options and cand is None:
            print(f"          line {i + 1}: nothing could be downloaded, using a plain background")
        chosen.append(cand)

    # 5. Real videos are reels of several shots: let Claude pick the shot for each line
    starts: dict[int, float] = {}
    reel_lines = [i for i, c in enumerate(chosen) if c and c.kind == "real"]
    if reel_lines:
        shot_sheets, shot_lists = [], {}
        for i in reel_lines:
            shots = _scene_shots(files[chosen[i].id], chosen[i].duration)
            shot_lists[i] = shots
            frames = [_frame_at(files[chosen[i].id], (a + b) / 2) for a, b in shots]
            path = sheet_dir / f"line_{i + 1:02}_shots.jpg"
            _contact_sheet(frames, [(f"{a:.0f}s, {b - a:.0f}s long", (60, 220, 90)) for a, b in shots], path)
            shot_sheets.append(path)
        print(f"[footage] Claude is choosing the best shot inside {len(reel_lines)} real videos...")
        prompt = "Lines that use real video, and the shots inside each video:\n" + "\n".join(
            f"Line {i + 1} ({shot_sheets[k].name}, {len(shot_lists[i])} shots; "
            f"needs about {durations[i]:.1f}s; same video as lines "
            f"{[j + 1 for j in reel_lines if chosen[j].id == chosen[i].id and j != i] or 'none'}): "
            f"{strip_tags(lines[i]['text'])}"
            for k, i in enumerate(reel_lines))
        shot_picks = {p["line"]: p["choice"]
                      for p in ask(SHOT_SYSTEM, prompt, _PICKS_SCHEMA, images=shot_sheets)["picks"]}
        for i in reel_lines:
            shots = shot_lists[i]
            choice = shot_picks.get(i + 1, 0)
            if 1 <= choice <= len(shots):
                starts[i] = shots[choice - 1][0]
            else:  # fall back to the longest shot
                starts[i] = max(shots, key=lambda s: s[1] - s[0])[0]

    shots_out: list[Shot | None] = []
    credits, credited = [], set()
    for i, cand in enumerate(chosen):
        if cand is None:
            shots_out.append(None)
            continue
        kind = "photo" if cand.kind == "photo" else "video"
        shots_out.append(Shot(kind, files[cand.id], cand.height > cand.width, starts.get(i, 0.0)))
        if cand.id not in credited:
            credits.append(cand.credit)
            credited.add(cand.id)
    return shots_out, credits
