"""Paths, video settings and API settings (read from .env)."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ASSETS = ROOT / "assets"
OUTPUT = ROOT / "output"

# 9:16 vertical video for Shorts / TikTok / Reels
W, H, FPS = 1080, 1920, 30


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


def require_key(name: str, hint: str) -> str:
    value = env(name)
    if not value:
        raise SystemExit(f"{name} is missing. Add it to {ROOT / '.env'} ({hint}).")
    return value


CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-opus-5-5")
ELEVENLABS_MODEL = env("ELEVENLABS_MODEL", "eleven_v3")
NARRATOR_VOICE_ID = env("NARRATOR_VOICE_ID", "cjVigY5qzO86Huf0OWal")  # Eric - smooth, trustworthy


def blender_path() -> str:
    """Blender for 3D animations: BLENDER_PATH in .env, or the portable copy in tools/blender/."""
    if env("BLENDER_PATH"):
        return env("BLENDER_PATH")
    found = sorted((ROOT / "tools" / "blender").glob("*/blender.exe"))
    if not found:
        raise SystemExit("Blender not found. Put a portable Blender 4.5 in tools/blender/ "
                         "(https://download.blender.org/release/Blender4.5/) or set BLENDER_PATH in .env.")
    return str(found[-1])


def load_characters() -> dict:
    """The two characters used by the dialogue format (assets/characters/characters.json)."""
    path = ASSETS / "characters" / "characters.json"
    chars = json.loads(path.read_text(encoding="utf-8"))
    for key, char in chars.items():
        char["image_path"] = ASSETS / "characters" / char["image"]
        if not char["image_path"].exists():
            raise SystemExit(f"Character {key} image not found: {char['image_path']}")
    return chars
