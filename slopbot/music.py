"""Background music: Claude picks a fitting track from Kevin MacLeod's Incompetech catalog
(1,400+ royalty-free tracks, CC BY 4.0 - free to use in monetized videos with the credit line,
which goes into upload_info.txt).

  1. Claude names the mood it wants (from the catalog's mood tags).
  2. The catalog is narrowed to ~40 matching tracks; Claude picks one from their descriptions.
  3. The track is downloaded (cached) and started where it has energy, not on a quiet intro.
"""
import array
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .claude import ask
from .config import ROOT
from .ff import FFMPEG
from .voice import strip_tags

CATALOG_URL = "https://incompetech.com/music/royalty-free/pieces.json"
MP3_URL = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/{}"
CACHE = ROOT / "cache" / "music"
HEADERS = {"User-Agent": "SlopBot/0.4 (personal video tool)"}
FEELS = ["Action", "Aggressive", "Bouncy", "Bright", "Calming", "Dark", "Driving", "Eerie", "Epic", "Grooving",
         "Humorous", "Intense", "Mysterious", "Mystical", "Relaxed", "Somber", "Suspenseful", "Uplifting",
         "Unnerving"]

MOOD_SYSTEM = """\
You choose background music for a vertical short-form explainer video (YouTube Shorts / TikTok).
Good reel music sits UNDER a narrator: steady energy from the first second, a clear pulse, nothing
that fights the voice (no vocals, no busy lead melody), and a mood that matches the topic, e.g.
war/military history: dark, driving, epic or tense cinematic; science and tech: driving, mysterious or
uplifting electronic; animals and nature: bright, bouncy or calming; mysteries and true crime:
mysterious, suspenseful, eerie; funny topics: humorous, bouncy.
Choose 2-4 moods to look for and 1-4 to avoid, from this list only: """ + ", ".join(FEELS) + """.
Give a BPM range (most good reel beds are 90-140) and a short description of the ideal track."""

PICK_SYSTEM = """\
You pick the background track for a short explainer video from a list of royalty-free tracks by
Kevin MacLeod. Prefer: modern, cinematic or beat-driven tracks with steady energy that work as a bed
under narration and fit the topic. Avoid: novelty or comedy pieces (unless the video is funny),
holiday, medieval/tavern, lounge and elevator music, solo piano noodling, pure ambience with no pulse,
anything with vocals, and anything whose description suggests it is cheesy or silly."""

_MOOD_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["want", "avoid", "bpm_min", "bpm_max", "ideal"],
                "properties": {"want": {"type": "array", "items": {"type": "string", "enum": FEELS}},
                               "avoid": {"type": "array", "items": {"type": "string", "enum": FEELS}},
                               "bpm_min": {"type": "integer"}, "bpm_max": {"type": "integer"},
                               "ideal": {"type": "string"}}}
_PICK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["choice", "reason"],
                "properties": {"choice": {"type": "integer"}, "reason": {"type": "string"}}}


def _catalog() -> list[dict]:
    """The Incompetech catalog, cached for a week."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "pieces.json"
    if not path.exists() or time.time() - path.stat().st_mtime > 7 * 86400:
        resp = requests.get(CATALOG_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _seconds(length: str) -> int:
    try:
        h, m, s = (int(x) for x in length.split(":"))
        return h * 3600 + m * 60 + s
    except (ValueError, AttributeError):
        return 0


def _energetic_start(track: Path, need: float) -> float:
    """Where the track first reaches its typical loudness, so the video doesn't open on a quiet
    intro. Keeps at least `need` seconds of music after the start when possible."""
    rate, window = 4000, 2000  # half-second windows
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(track), "-ac", "1", "-ar", str(rate),
                          "-f", "s16le", "-"], capture_output=True).stdout
    samples = array.array("h", raw[: len(raw) // 2 * 2])
    rms = [(sum(x * x for x in samples[i:i + window]) / window) ** 0.5
           for i in range(0, len(samples) - window, window)]
    if len(rms) < 8:
        return 0.0
    smooth = [sum(rms[i:i + 4]) / 4 for i in range(len(rms) - 3)]  # 2-second average
    typical = sorted(smooth)[int(len(smooth) * 0.6)]
    length = len(samples) / rate
    start = next((i * window / rate for i, v in enumerate(smooth) if v >= 0.8 * typical), 0.0)
    return max(0.0, min(start, length - need))


def pick_track(script: dict, duration: float) -> tuple[Path, str, float] | None:
    """Returns (mp3 path, credit line, start offset in seconds), or None if nothing could be picked."""
    try:
        catalog = _catalog()
    except requests.RequestException as exc:
        print(f"[music] couldn't load the Incompetech catalog ({exc}); no music")
        return None
    summary = (f"Title: {script['title']}\nNarration: "
               + " ".join(strip_tags(line["text"]) for line in script["lines"])[:1500])
    print("[music] Claude is choosing the mood...")
    mood = ask(MOOD_SYSTEM, summary, _MOOD_SCHEMA)
    want, avoid = set(mood["want"]), set(mood["avoid"])

    def score(piece: dict) -> float:
        feels = {f.strip() for f in (piece.get("feel") or "").split(",")}
        s = 2 * len(feels & want) - 3 * len(feels & avoid)
        try:
            bpm = int(piece.get("bpm") or 0)
            s += 1 if mood["bpm_min"] <= bpm <= mood["bpm_max"] else 0
        except ValueError:
            pass
        return s

    usable = [p for p in catalog if p.get("filename") and _seconds(p.get("length") or "") >= 45]
    shortlist = sorted(usable, key=score, reverse=True)[:40]
    listing = "\n".join(
        f"{n}. {p['title']} | {p.get('feel') or ''} | {p.get('bpm') or '?'} bpm | {(p.get('instruments') or '')[:60]} | "
        f"{' '.join((p.get('description') or '').split())[:170]}"
        for n, p in enumerate(shortlist, 1))
    print(f"[music] Claude is picking a track (moods: {', '.join(mood['want'])})...")
    pick = ask(PICK_SYSTEM, f"{summary}\n\nIdeal track: {mood['ideal']}\n\nTracks:\n{listing}", _PICK_SCHEMA)
    if not 1 <= pick["choice"] <= len(shortlist):
        print("[music] Claude found nothing that fits; no music")
        return None
    piece = shortlist[pick["choice"] - 1]
    path = CACHE / piece["filename"]
    if not path.exists():
        resp = requests.get(MP3_URL.format(quote(piece["filename"])), headers=HEADERS, timeout=120)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    start = _energetic_start(path, duration)
    print(f"[music] \"{piece['title']}\" by Kevin MacLeod, from {start:.0f}s ({pick['reason'][:90]})")
    credit = (f"Music: \"{piece['title']}\" Kevin MacLeod (incompetech.com), licensed under Creative "
              "Commons: By Attribution 4.0 License http://creativecommons.org/licenses/by/4.0/")
    return path, credit, start
