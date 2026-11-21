"""Builds the animated caption file (.ass subtitles, burned in by ffmpeg).

- Big captions: 1-3 words at a time, bold white with a black outline, a small "pop" when each
  chunk appears, and keywords highlighted in color.
- Top caption (explainer only): the whole current sentence in a small box at the top.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from .config import H, W
from .voice import Word

WHITE = "&H00FFFFFF&"


@dataclass
class TimedLine:
    text: str
    words: list[Word]       # times relative to the line start
    start: float            # line start in the final video
    end: float              # when the next line starts (or the video ends)
    keywords: list[str]
    highlight: str          # "#RRGGBB"


def hex_to_ass(color: str) -> str:
    c = color.lstrip("#")
    return f"&H00{c[4:6]}{c[2:4]}{c[0:2]}&".upper()


def _ts(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    return f"{cs // 360000}:{(cs // 6000) % 60:02}:{(cs // 100) % 60:02}.{cs % 100:02}"


def _clean(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def _chunks(words: list[Word], max_words: int = 3, max_chars: int = 14) -> list[list[Word]]:
    chunks, current = [], []
    for word in words:
        if current and (len(current) >= max_words
                        or len(" ".join(w.text for w in current + [word])) > max_chars):
            chunks.append(current)
            current = []
        current.append(word)
        if word.text[-1] in ".,!?;:":
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Big,Arial Black,100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,4,5,60,60,0,1
Style: Top,Arial,40,&H00FFFFFF,&H00FFFFFF,&H60000000,&H00000000,-1,0,0,0,100,100,0,0,3,12,0,8,110,110,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write(lines: list[TimedLine], out: Path, caption_y: int, top_caption: bool) -> None:
    events = []
    for line in lines:
        if top_caption:
            events.append(f"Dialogue: 0,{_ts(line.start)},{_ts(line.end)},Top,,0,0,0,,{_clean(line.text)}")

        keywords = {_norm(w) for k in line.keywords for w in k.split()}
        color = hex_to_ass(line.highlight)
        chunks = _chunks(line.words)
        for i, chunk in enumerate(chunks):
            start = line.start + chunk[0].start
            if i + 1 < len(chunks):
                end = line.start + chunks[i + 1][0].start
            else:
                end = min(line.start + chunk[-1].end + 0.3, line.end)
            end = max(end, start + 0.1)

            parts = []
            for word in chunk:
                text = _clean(word.text)
                parts.append(f"{{\\c{color}}}{text}{{\\c{WHITE}}}" if _norm(word.text) in keywords else text)
            pop = f"{{\\pos({W // 2},{caption_y})\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)}}"
            events.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Big,,0,0,0,,{pop}{' '.join(parts)}")

    out.write_text(HEADER + "\n".join(events) + "\n", encoding="utf-8")
