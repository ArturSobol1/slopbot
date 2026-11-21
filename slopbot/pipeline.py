"""Topic in, finished video out."""
import json
import random
import re
from datetime import datetime
from pathlib import Path

from . import captions, describe, render, voice
from .config import ASSETS, NARRATOR_VOICE_ID, OUTPUT, load_characters
from .script import write_script

LINE_GAP = {"explainer": 0.15, "dialogue": 0.18}  # pause between separately generated takes (seconds)
TAIL = 0.6                                        # silence after the last line
EXPLAINER_HIGHLIGHT = "#00E5FF"
MEDIA = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO = {".mp3", ".wav", ".m4a", ".ogg"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "video"


def _pick(folder: Path, suffixes: set[str]) -> Path | None:
    files = [p for p in folder.glob("*") if p.suffix.lower() in suffixes]
    return random.choice(files) if files else None


def make_video(fmt: str, topic: str | None = None, script_path: str | None = None,
               offline: bool = False, speed: float = 1.0, music: bool = True,
               visuals: str = "animated", resume: str | None = None,
               music_file: str | None = None) -> Path:
    characters = load_characters()

    # 1. Script
    if resume:  # finish an interrupted run in its own folder (voice comes from the cache)
        run_dir = Path(resume).resolve()
        script = json.loads((run_dir / "script.json").read_text(encoding="utf-8"))
        print(f"[script] resuming {run_dir.name}")
    elif script_path:
        script = json.loads(Path(script_path).read_text(encoding="utf-8"))
        print(f"[script] using {script_path}")
    else:
        print(f"[script] asking Claude for a {fmt} about: {topic}")
        script = write_script(fmt, topic, characters)
    lines = script["lines"]

    if not resume:
        run_dir = OUTPUT / f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(script['title'])}"
        run_dir.mkdir(parents=True)
    (run_dir / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[script] \"{script['title']}\" - {len(lines)} lines")

    # 2. Voice
    speakers = [line.get("speaker", "narrator") for line in lines]
    if offline:
        voices = [1 if s == "B" else 0 for s in speakers]
    else:
        voices = [characters[s]["voice_id"] if s in characters else NARRATOR_VOICE_ID for s in speakers]
    narration = voice.synthesize([line["text"] for line in lines], voices, run_dir / "audio",
                                 run_dir / "voice.wav", speed, LINE_GAP[fmt], offline)
    starts = [timing.start for timing in narration.lines]
    total = narration.duration + TAIL
    ends = starts[1:] + [total]

    # 3. Captions
    timed = []
    for line, speaker, timing, end in zip(lines, speakers, narration.lines, ends):
        color = characters[speaker]["color"] if fmt == "dialogue" and speaker in characters else EXPLAINER_HIGHLIGHT
        keywords = [voice.strip_tags(k) for k in line.get("keywords", [])]
        timed.append(captions.TimedLine(voice.strip_tags(line["text"]), timing.words, timing.start, end,
                                        keywords, color))
    if fmt == "explainer":
        captions.write(timed, run_dir / "captions.ass", caption_y=1330, top_caption=False)
    else:
        captions.write(timed, run_dir / "captions.ass", caption_y=880, top_caption=False)

    # 4. Visuals + render
    # Music: a file you chose, else your own tracks in assets/music, else Claude picks one from Incompetech.
    track, music_start, music_credit = None, 0.0, ""
    if music_file:
        track = Path(music_file)
    elif music:
        track = _pick(ASSETS / "music", AUDIO)
        if track is None and not offline:
            from .music import pick_track
            try:
                picked = pick_track(script, total)
            except Exception as exc:  # music is a nice-to-have: never lose the video over it
                print(f"[music] skipped ({exc})")
                picked = None
            if picked:
                track, music_credit, music_start = picked
    credits = []
    if fmt == "explainer":
        durations = [e - s for s, e in zip(starts, ends)]
        if offline:
            shots = [None] * len(lines)
        elif visuals == "animated":
            from .animator import animate
            from .visuals import choose_shots
            shots, credits, footage_lines = animate(script, run_dir, durations)
            if footage_lines:
                real, real_credits = choose_shots(script, run_dir, durations, only=footage_lines)
                for i in footage_lines:
                    shots[i] = real[i]
                credits += real_credits
        else:
            from .visuals import choose_shots
            shots, credits = choose_shots(script, run_dir, durations)
        print("[render] building video...")
        final = render.explainer(run_dir, shots, starts, total, track, music_start)
    else:
        background = _pick(ASSETS / "backgrounds", MEDIA)
        if not background:
            print("[render] no gameplay clips in assets/backgrounds - using a plain animated background")
        speaking = {"A": [], "B": []}
        for speaker, start, end in zip(speakers, starts, ends):
            if speaker in speaking:
                speaking[speaker].append((start, end))
        print("[render] building video...")
        final = render.dialogue(run_dir, background, characters, speaking, total, track, music_start)

    # 5. Upload info: Claude's short description + hashtags, then the license credits
    # (youtube.json + upload_info.txt)
    describe.write(run_dir, script, describe.credits_block(credits, music_credit))
    print(f"[done] {final} ({total:.1f}s)")
    return final
