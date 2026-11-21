"""Text-to-speech with word timings.

Consecutive lines spoken by the same voice are generated in ONE request, so the delivery flows
naturally across sentences instead of resetting every line. The word timings are then split
back into lines for the captions and the picture cuts.

The offline engine uses the voice built into Windows, so the pipeline can be tested without an
API key (its word timings are estimated).
"""
import base64
import hashlib
from array import array
import json
import os
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import requests

from . import ff
from .config import ELEVENLABS_MODEL, ROOT, require_key

CACHE = ROOT / "cache" / "tts"

SAMPLE_RATE = 44100
IS_V3 = ELEVENLABS_MODEL.startswith("eleven_v3")
MAX_CHARS = 2500 if IS_V3 else 9000  # per request
TAG = re.compile(r"\[[^\]]*\]")


def strip_tags(text: str) -> str:
    """Removes delivery tags like [excited] (spoken by v3, never shown in captions)."""
    return re.sub(r"\s+", " ", TAG.sub(" ", text)).strip()


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class LineTiming:
    start: float        # when this line begins in the final audio
    words: list[Word]   # times relative to `start`


@dataclass
class Narration:
    wav: Path
    duration: float
    lines: list[LineTiming]


def _to_wav(src: Path, dst: Path, speed: float = 1.0) -> None:
    args = ["-i", src]
    if speed != 1.0:
        args += ["-af", f"atempo={speed}"]
    args += ["-ar", SAMPLE_RATE, "-ac", 1, "-sample_fmt", "s16", dst]
    ff.run(args)


def _alignment_words(alignment: dict) -> list[Word]:
    words, current, start, end, depth = [], "", 0.0, 0.0, 0
    for char, s, e in zip(alignment["characters"],
                          alignment["character_start_times_seconds"],
                          alignment["character_end_times_seconds"]):
        if char == "[":
            depth += 1
        if depth or char.isspace():
            if current:
                words.append(Word(current, start, end))
                current = ""
            if char == "]":
                depth = max(depth - 1, 0)
            continue
        if not current:
            start = s
        current += char
        end = e
    if current:
        words.append(Word(current, start, end))
    return words


def _voice_settings(speed: float) -> dict:
    speed = min(max(speed, 0.7), 1.2)
    if IS_V3:
        return {"stability": 0.5, "similarity_boost": 0.8, "speed": speed}  # v3 "Natural" mode
    return {"stability": 0.4, "similarity_boost": 0.8, "style": 0.35, "use_speaker_boost": True, "speed": speed}


def _elevenlabs(text: str, voice_id: str, out_mp3: Path, speed: float,
                previous_text: str | None, next_text: str | None) -> list[Word]:
    key = require_key("ELEVENLABS_API_KEY", "find it at https://elevenlabs.io/app/settings/api-keys")
    body = {"text": text, "model_id": ELEVENLABS_MODEL, "voice_settings": _voice_settings(speed)}
    if not IS_V3:  # v3 doesn't support request stitching
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text

    # Re-rendering the same script reuses earlier audio instead of paying for it again.
    digest = hashlib.sha1(json.dumps([voice_id, body], sort_keys=True).encode()).hexdigest()[:16]
    cached_mp3, cached_words = CACHE / f"{digest}.mp3", CACHE / f"{digest}.json"
    if cached_mp3.exists() and cached_words.exists():
        shutil.copyfile(cached_mp3, out_mp3)
        return [Word(**w) for w in json.loads(cached_words.read_text(encoding="utf-8"))]

    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": key}, json=body, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    out_mp3.write_bytes(base64.b64decode(data["audio_base64"]))
    words = _alignment_words(data.get("alignment") or data["normalized_alignment"])
    CACHE.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out_mp3, cached_mp3)
    cached_words.write_text(json.dumps([w.__dict__ for w in words]), encoding="utf-8")
    return words


_SAPI_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = @($s.GetInstalledVoices() | Where-Object { $_.Enabled })
if ($voices.Count -gt 0) { $s.SelectVoice($voices[[int]$env:SLOPBOT_VOICE % $voices.Count].VoiceInfo.Name) }
$s.SetOutputToWaveFile($env:SLOPBOT_OUT)
$s.Speak($env:SLOPBOT_TEXT)
$s.Dispose()
"""


def _offline(text: str, voice_index: int, out_raw: Path) -> None:
    env = {**os.environ, "SLOPBOT_TEXT": text, "SLOPBOT_OUT": str(out_raw), "SLOPBOT_VOICE": str(voice_index)}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SAPI_SCRIPT],
                   env=env, check=True, capture_output=True)


def _estimate_words(text: str, duration: float) -> list[Word]:
    tokens = text.split()
    weights = [len(t) + 2 for t in tokens]
    lead = min(0.15, duration * 0.05)
    span = max(duration - 2 * lead, 0.1)
    words, t = [], lead
    for token, weight in zip(tokens, weights):
        length = span * weight / sum(weights)
        words.append(Word(token, t, t + length))
        t += length
    return words


MAX_PAUSE = float(os.environ.get("SLOPBOT_MAX_PAUSE", "0.5"))  # seconds; 0 turns trimming off


def _tighten(wav: Path, words: list[Word], max_gap: float = MAX_PAUSE) -> list[Word]:
    """Shortens overly long pauses (v3 likes ~1 s dramatic pauses after short sentences).

    Pauses are found in the audio itself (the word timings often stretch across them). Only
    silences longer than `max_gap` are touched, and they are shortened to exactly `max_gap`,
    keeping the start and end of the pause (breaths, word tails) intact. Returns the word timings
    shifted to match.
    """
    if max_gap <= 0:
        return words
    keep_after, keep_before = max_gap * 0.6, max_gap * 0.4
    with wave.open(str(wav)) as w:
        samples = array("h", w.readframes(w.getnframes()))

    window = SAMPLE_RATE // 100  # 10 ms
    threshold = (32768 * 10 ** (-50 / 20)) ** 2 * window  # -50 dBFS, as a sum of squares
    silent = [sum(x * x for x in samples[i:i + window]) < threshold
              for i in range(0, len(samples) - window, window)]
    cuts, run_start = [], None
    for n, quiet in enumerate(silent + [False]):
        if quiet and run_start is None:
            run_start = n
        elif not quiet and run_start is not None:
            if run_start > 0 and n < len(silent) and (n - run_start) / 100 > max_gap:  # inner pauses only
                start = run_start * window + int(keep_after * SAMPLE_RATE)
                end = n * window - int(keep_before * SAMPLE_RATE)
                if end - start > 0.02 * SAMPLE_RATE:
                    cuts.append((start, end))
            run_start = None
    if not cuts:
        return words

    fade = int(0.015 * SAMPLE_RATE)
    out, prev = array("h"), 0
    for start, end in cuts + [(len(samples), len(samples))]:
        chunk = samples[prev:start]
        for i in range(min(fade, len(chunk))):  # fade in at the start of each kept piece...
            if prev:
                chunk[i] = int(chunk[i] * i / fade)
        if start < len(samples):  # ...and out at its end
            for i in range(min(fade, len(chunk))):
                chunk[-1 - i] = int(chunk[-1 - i] * i / fade)
        out.extend(chunk)
        prev = end
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(out.tobytes())

    def remap(t: float) -> float:
        pos = t * SAMPLE_RATE
        removed = sum(min(e, pos) - s for s, e in cuts if s < pos)
        return (pos - removed) / SAMPLE_RATE

    return [Word(word.text, remap(word.start), remap(word.end)) for word in words]


def _split_by_line(words: list[Word], counts: list[int]) -> list[list[Word]]:
    """Hands the piece's words back to its lines, in order."""
    if sum(counts) != len(words) and words:  # tokenizing differed: share out proportionally
        scale = len(words) / max(sum(counts), 1)
        counts = [round(c * scale) for c in counts]
        counts[-1] = len(words) - sum(counts[:-1])
    out, i = [], 0
    for c in counts:
        out.append(words[i:i + max(c, 0)])
        i += max(c, 0)
    return out


def synthesize(texts: list[str], voices: list, out_dir: Path, out_wav: Path,
               speed: float, gap: float, offline: bool) -> Narration:
    """`voices` holds an ElevenLabs voice ID per line (or a Windows voice index when offline)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Group consecutive lines with the same voice into one request.
    groups: list[list[int]] = []
    for i, voice in enumerate(voices):
        if (groups and not offline and voices[groups[-1][0]] == voice
                and sum(len(texts[j]) + 1 for j in groups[-1]) + len(texts[i]) <= MAX_CHARS):
            groups[-1].append(i)
        else:
            groups.append([i])

    pieces = []
    for g, members in enumerate(groups):
        joined = " ".join(texts[i] for i in members)
        print(f"[voice] take {g + 1}/{len(groups)}: {strip_tags(joined)[:70]}")
        wav = out_dir / f"take_{g:03}.wav"
        if offline:
            raw = out_dir / f"take_{g:03}_raw.wav"
            _offline(strip_tags(joined), voices[members[0]], raw)
            _to_wav(raw, wav, speed)
        else:
            mp3 = out_dir / f"take_{g:03}.mp3"
            words = _elevenlabs(
                joined, voices[members[0]], mp3, speed,
                previous_text=" ".join(texts[:members[0]])[-500:] or None,
                next_text=" ".join(texts[members[-1] + 1:])[:500] or None)
            _to_wav(mp3, wav)
            words = _tighten(wav, words)
        with wave.open(str(wav)) as w:
            frames = w.getnframes()
        if offline:
            words = _estimate_words(strip_tags(joined), frames / SAMPLE_RATE)
        counts = [len(strip_tags(texts[i]).split()) for i in members]
        pieces.append((wav, frames, members, _split_by_line(words, counts)))

    # Stitch the takes together with a short pause between them.
    lines: list[LineTiming | None] = [None] * len(texts)
    gap_frames = int(gap * SAMPLE_RATE)
    written = 0
    with wave.open(str(out_wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        for p, (wav, frames, members, per_line) in enumerate(pieces):
            offset = written / SAMPLE_RATE
            with wave.open(str(wav)) as w:
                out.writeframes(w.readframes(frames))
            written += frames
            for k, (i, words) in enumerate(zip(members, per_line)):
                if k == 0:
                    start = offset
                elif words:
                    start = offset + max(words[0].start - 0.08, 0)
                else:  # a line that was only a tag: give it a token slot after the previous line
                    start = lines[members[k - 1]].start + 0.05
                lines[i] = LineTiming(start, [Word(w.text, offset + w.start - start, offset + w.end - start)
                                              for w in words])
            if p < len(pieces) - 1:
                out.writeframes(b"\x00\x00" * gap_frames)
                written += gap_frames
    return Narration(out_wav, written / SAMPLE_RATE, lines)
