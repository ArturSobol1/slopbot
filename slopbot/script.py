"""Writes the video script with Claude, as structured JSON."""
from .claude import ask
from .config import ELEVENLABS_MODEL

STYLE_RULES = """\
You write scripts for viral 40-second vertical explainer videos (YouTube Shorts / TikTok).
Short is the point: viewers who finish a Short (and loop it) are what make YouTube push it.

Rules for every script:
- The first line is the hook, and it decides whether people swipe away in the first second:
  - Under 12 words, sayable in about 3 seconds.
  - Lead with the single most surprising, concrete fact about THIS topic: a contradiction, an extreme
    number or a "that shouldn't work" moment. E.g. "This bomber is as wide as a 747, yet radar sees a bird."
  - Never open with "Did you know", "Have you ever wondered", "Let's talk about", "Meet the...",
    a greeting or background history.
  - It makes a promise the rest of the script pays off.
- The second line raises the stakes or opens the mystery. No slow setup before the interesting part.
- Short, punchy spoken sentences. Each line is at most 20 words and reads naturally out loud.
- Write the way a person talks, not like an essay: contractions, varied rhythm, the odd aside.
- Plain language a 14-year-old understands. No filler like "In this video".
- Stick to well-established facts. Do not invent statistics, dates or quotes; if unsure, leave it out.
- "keywords": 1-2 of the most important words in that line, copied exactly as they appear in "text".
  They get highlighted in the captions.
- "description": a 1-2 sentence YouTube description followed by 3-5 hashtags.
- "title": a short clickable title, under 60 characters.
"""

TAG_RULES = """\
- The voice engine understands delivery tags in square brackets, such as [curious], [excited],
  [whispers], [sarcastic], [laughs] and [sighs]. Use at most 3 in the whole script, only where they add
  real emotion, at the start of a line. Never put tags in "keywords".
"""

EXPLAINER_RULES = """\
Format: a single narrator ("speaker": "narrator") explains the topic.
- 7-10 lines, 80-100 words in total (about 40 seconds spoken). Count them: never over 100.
  Pick the 3-4 most striking facts and cut everything else; one idea per line.
- The second-to-last line is a satisfying payoff that answers the hook's promise.
- The last line is a question to the viewer that anyone can answer in a comment in a few words:
  an opinion, a choice or a guess about this topic (e.g. "Would you fly in it, yes or no?",
  "Tiger or T-34: which would you rather crew?"). Under 12 words. Never "like and subscribe",
  and never a question that needs research to answer.
- "subject": the main real-world thing the video is about, as you would search for it in a photo
  archive (e.g. "B-2 Spirit", "Great Barrier Reef", "Apollo 11"). Empty if the topic is abstract.
- Visuals for each line (an editor later picks the best match from these searches):
  - "photo": a Wikimedia Commons search for REAL footage and photos, when the line is about a
    specific, named, real thing (e.g. "B-2 Spirit refueling", "B-2 Spirit takeoff",
    "Whiteman Air Force Base"). Use proper names plus at most one action word. Empty string when
    nothing specific fits the line.
  - "footage": a 1-3 word stock-video search for a generic shot that fits the line
    (e.g. "radar screen", "storm clouds", "jet engine"). Always fill this in.
  - No celebrities or private people.
"""

DIALOGUE_RULES = """\
Format: a comedic two-character dialogue.
- Character A is {a_name}: {a_personality}
- Character B is {b_name}: {b_personality}
- B opens by asking about the topic. A explains; B interrupts with confusion, wrong guesses and jokes,
  which A corrects. A ends with a clear one-line takeaway and B gets the last joke.
- 12-18 lines, 140-190 words in total. Use "speaker": "A" or "B".
- Characters may tease each other, but keep it friendly and not mean-spirited.
- "subject", "photo" and "footage": always empty strings.
"""


def _schema(speakers: list[str]) -> dict:
    line = {
        "type": "object",
        "properties": {
            "speaker": {"type": "string", "enum": speakers},
            "text": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "photo": {"type": "string"},
            "footage": {"type": "string"},
        },
        "required": ["speaker", "text", "keywords", "photo", "footage"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "subject": {"type": "string"},
            "lines": {"type": "array", "items": line},
        },
        "required": ["title", "description", "subject", "lines"],
        "additionalProperties": False,
    }


MAX_WORDS = 105  # a little slack over the 100 asked for


def _words(script: dict) -> int:
    import re
    return sum(len(re.sub(r"\[[^\]]*\]", "", line["text"]).split()) for line in script["lines"])


def write_script(fmt: str, topic: str, characters: dict) -> dict:
    if fmt == "explainer":
        rules, speakers = EXPLAINER_RULES, ["narrator"]
    else:
        a, b = characters["A"], characters["B"]
        rules = DIALOGUE_RULES.format(a_name=a["name"], a_personality=a["personality"],
                                      b_name=b["name"], b_personality=b["personality"])
        speakers = ["A", "B"]
    system = STYLE_RULES + (TAG_RULES if ELEVENLABS_MODEL.startswith("eleven_v3") else "") + "\n" + rules

    prompt = f"Write the script. Topic: {topic}"
    script = ask(system, prompt, _schema(speakers))
    if not script.get("lines"):
        raise RuntimeError("Claude returned an empty script.")
    words = _words(script)
    if fmt == "explainer" and words > MAX_WORDS:  # too long for ~40 s: one tightening pass
        print(f"[script] {words} words is too long, asking Claude to tighten it")
        tighter = ask(system, f"{prompt}\n\nYour draft had {words} words; the limit is {MAX_WORDS}. "
                              "Rewrite it to 80-100 words, keeping the hook and the closing question.\n\n"
                              f"Draft: {script}", _schema(speakers))
        if tighter.get("lines") and _words(tighter) < words:
            script = tighter
    script["topic"] = topic
    script["format"] = fmt
    return script
