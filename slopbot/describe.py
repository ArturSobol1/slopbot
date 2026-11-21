"""YouTube metadata for a finished run: a short description, hashtags and tags (youtube.json).

The credits (3D models, footage, music) are CC BY license requirements, so they are kept as a
separate block and always appended to the uploaded description.
"""
import json
import re
from pathlib import Path
from urllib.parse import unquote

from .claude import ask

SYSTEM = """\
You write YouTube Shorts metadata for short educational explainer videos.
- "description": 1-2 short sentences (at most 200 characters in total) that make people want to
  watch. Plain, accurate, no clickbait lies, no hashtags, no emojis, no links.
- "hashtags": 3-5 relevant hashtags, each starting with #, CamelCase, no spaces
  (e.g. #MilitaryHistory). Do not include #Shorts; it is added automatically.
- "tags": 5-12 search keywords for the YouTube tags field (plain words or short phrases, no #).
Never use the characters < or >.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["description", "hashtags", "tags"],
    "additionalProperties": False,
}

CREDIT_STARTS = ("Pictures and footage:", "Music:")


def credits_block(credits: list[str], music_credit: str) -> str:
    """The attribution lines the CC BY licenses require (empty string if nothing needs credit)."""
    parts = []
    if credits:
        parts.append("\n".join(["Pictures and footage:", *credits]))
    if music_credit:
        parts.append(music_credit)
    return "\n\n".join(parts)


def credits_from_upload_info(run_dir: Path) -> str:
    """Credits block of an older run, read back from its upload_info.txt."""
    path = run_dir / "upload_info.txt"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith(CREDIT_STARTS):
            return "\n".join(lines[i:]).strip()
    return ""


def _clean(text: str) -> str:
    return text.replace("<", "").replace(">", "").strip()


COMMONS_LINE = re.compile(r"^https?://commons\.wikimedia\.org/wiki/File:(\S+)\s+-\s+(.*)$")
URL = re.compile(r"\s*https?://\S+")


def credits_without_links(credits: str) -> str:
    """The credits block with every link removed, for the YouTube description.

    New channels can't have external links in descriptions, so each credit keeps the work's title,
    its author, the license and the site it came from, which is what CC BY attribution needs.
    """
    lines = []
    for line in credits.splitlines():
        commons = COMMONS_LINE.match(line.strip())
        if commons:
            name = unquote(commons.group(1)).replace("_", " ")
            name = re.sub(r"\.\w{2,4}$", "", name)  # drop .jpg / .webm / ...
            line = f"\"{name}\" by {commons.group(2)}, Wikimedia Commons"
        elif line.startswith("3D model") and "sketchfab.com" in line:
            line = URL.sub("", line) + ", Sketchfab"
        else:
            stock = re.match(r"^https?://(?:www\.)?(pixabay|pexels)\.com/\S+\s+-\s+(.*)$", line.strip())
            if stock:
                line = f"Stock footage by {stock.group(2)}, {stock.group(1).capitalize()}"
        line = URL.sub("", line).replace("(incompetech.com)", "(Incompetech)")
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def upload_description(meta: dict) -> str:
    """Short description + hashtags, a blank line, then the link-free credits."""
    return f"{meta['description']}\n\n{credits_without_links(meta.get('credits', ''))}".strip()


def _hashtag(tag: str) -> str:
    tag = re.sub(r"[^\w]", "", tag.lstrip("#"))
    return f"#{tag}" if tag else ""


def _fallback(script: dict) -> dict:
    """Use the script's own description if Claude can't be reached."""
    text = script.get("description", "")
    hashtags = re.findall(r"#\w+", text)
    return {"description": re.sub(r"#\w+", "", text).strip(), "hashtags": hashtags, "tags": []}


def _ask_claude(script: dict) -> dict:
    narration = "\n".join(line["text"] for line in script["lines"])
    prompt = (f"Video title: {script['title']}\n"
              f"Topic: {script.get('subject') or script['title']}\n\n"
              f"Narration:\n{narration}\n\n"
              "Write the description, hashtags and tags for this Short.")
    return ask(SYSTEM, prompt, SCHEMA)


def write(run_dir: Path, script: dict, credits: str) -> dict:
    """Ask Claude for the description + hashtags, save youtube.json and upload_info.txt."""
    run_dir = Path(run_dir)
    try:
        answer = _ask_claude(script)
    except (Exception, SystemExit) as exc:  # the video is already rendered: never lose it over metadata
        print(f"[youtube] Claude couldn't write the description ({exc}); using the script's")
        answer = _fallback(script)

    hashtags = []
    for tag in [*answer.get("hashtags", [])[:5], "#Shorts"]:
        tag = _hashtag(tag)
        if tag and tag.lower() not in {h.lower() for h in hashtags}:
            hashtags.append(tag)

    tags, length = [], 0
    for tag in answer.get("tags", []):
        tag = _clean(tag).replace('"', "").replace(",", " ").strip()
        cost = len(tag) + (2 if " " in tag else 0) + 1  # quotes around phrases + comma
        if tag and tag.lower() not in {t.lower() for t in tags} and length + cost <= 450:
            tags.append(tag)
            length += cost

    short = _clean(answer.get("description", ""))
    description = f"{short}\n{' '.join(hashtags)}".strip()
    credits = _clean(credits)
    meta = {
        "title": _clean(script["title"]),
        "description": description,
        "hashtags": hashtags,
        "tags": tags,
        "credits": credits,
    }
    meta["full_description"] = upload_description(meta)  # what YouTube gets: credits without links
    (run_dir / "youtube.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    # upload_info.txt keeps the credits with their links, for other platforms and your records
    (run_dir / "upload_info.txt").write_text(f"{meta['title']}\n\n{description}\n\n{credits}".strip() + "\n",
                                             encoding="utf-8")
    print(f"[youtube] description + {len(hashtags)} hashtags saved to youtube.json")
    return meta


def load_or_write(run_dir: Path, redo: bool = False) -> dict:
    """youtube.json of a run, creating it first for runs made before this step existed."""
    run_dir = Path(run_dir)
    path = run_dir / "youtube.json"
    if path.exists() and not redo:
        return json.loads(path.read_text(encoding="utf-8"))
    script = json.loads((run_dir / "script.json").read_text(encoding="utf-8"))
    if path.exists():
        credits = json.loads(path.read_text(encoding="utf-8")).get("credits", "")
    else:
        credits = credits_from_upload_info(run_dir)
    return write(run_dir, script, credits)
