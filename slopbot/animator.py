"""Claude directs and animates the video's shots in Blender.

  1. Plan:    Claude decides per line: 3D animation (most lines) or real footage, sets one visual
              style, and lists the 3D models it needs.
  2. Models:  each model is searched on Sketchfab; Claude picks from thumbnail sheets.
  3. Design:  Claude writes a short script per shot using the SlopBot toolkit (tools/sb_blender/sb.py).
  4. Review:  every shot is previewed (3 low-res frames); Claude checks them and rewrites what's off.
              Up to REVIEW_ROUNDS rounds.
  5. Render:  final 1080x1920 clips, one per line, exactly as long as the line.
"""
import json
import re
import subprocess
import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from . import models3d
from .claude import ask
from .config import ROOT, blender_path
from .hdri import ensure_hdris
from .visuals import _contact_sheet, _font
from .voice import strip_tags

REVIEW_ROUNDS = 4  # short videos have few shots, so each gets more polish
HEAVY_PREVIEW_SECONDS = 60  # previews normally take ~5 s; slower scenes would take too long to render
RUNNER = ROOT / "tools" / "sb_blender" / "run_shot.py"
NOT_ALLOWED = {"rings(": "glowing rings are not allowed", "dust(": "floating sparkles are not allowed"}
FORBIDDEN = ["import ", "__", "open(", "exec(", "eval(", "compile(", "os.", "sys.", "subprocess",
             "bpy.ops.wm", "bpy.ops.render", "filepath", "bpy.app", "globals(", "locals(", ".write("]

API = """\
SlopBot animation toolkit (all functions are already available; do not import anything).
Coordinates: the default camera sits in front at -Y looking toward +Y; x = left/right, z = up,
y = depth. The frame is VERTICAL 9:16: at camera distance 12 about 8.5 units of width and 15 units
of height are visible. The camera is framed so its target appears in the UPPER-MIDDLE of the screen
(about 5 units of room above the target, 10 below); the lower part is covered by subtitles. Times are seconds from the start of the shot; SECONDS is
the shot length. Angles are degrees. Colors are "#rrggbb".

Rendering polish is automatic: real HDRI lighting, ray-traced reflections, depth of field, bloom on
anything with glow, a soft vignette, beveled edges.

setup(env, tint=None)          MUST be the first call.
   REAL PLACES (photo-real surroundings; use these for shots with 3D models): this video's own
        places, photographed real locations picked for this topic, are listed in the brief by code
        name, e.g. setup("runway"). Use only the place names listed there.
      In a place, call add_floor(under=hero) so the model stands on the place's real ground
      (grass, asphalt, concrete...) with its real shadow. Lighting comes from the photo's sun.
      SCALE: a place is photographed from head height, so 1 unit = 1 metre. Give models their REAL
      size (car 4.5, tank 7-10, fighter jet 14-19, helicopter 15-30, small boat 10-40) and move the
      camera back to fit them. Handheld things (guns, tools) may be shown larger as a close-up hero.
      Never shrink something big: a 16-unit bridge or a 10-unit battleship looks like a toy.
   BACKDROPS (no surroundings; for diagrams, graphs and text-heavy shots):
        "studio" (tinted gradient backdrop) | "white" (bright clean studio) | "dark" (near-black, for
        neon diagrams only) | "sky" (blue sky, ocean far below: things in flight)
   tint: the backdrop color for studio/dark/white, e.g. a dark shade of the palette.
model(name, size=5, at=(0,0,0), rot=(0,0,0))   a downloaded 3D model (names listed per video),
   scaled so its largest side = size, centered on `at`. Facing direction is unknown until you see a
   preview: fix with rot (degrees). Models look far better than shapes: use them for real objects.
shape(kind, size=1, at, rot, color, preset="plastic", glow=0, alpha=1, scale=None)
   kind: cube sphere cylinder cone torus plane ico disc capsule (cubes/cylinders get beveled edges).
   preset: matte clay rubber plastic glossy metal chrome gold glass neon.
text(content, at, size=1, color, glow=0, preset="glossy")   solid beveled 3D text; glow>0 = neon text.
card(words, at, width=4, height=1.1, color="#1e2533", text_color, accent=None)
   rounded infographic card with text and an optional glowing accent strip. Best way to show
   stats, names, list items, comparisons.
label(target, words, offset=(1.8,0,1.4), color)   documentary callout: glowing dot + leader line + text.
arrow(start, end, color="#ffd400", width=0.07)   glowing arrow between points/objects.
line(points, color, width=0.05, glow=3)         glowing tube through points (paths, graphs, waves).
draw_on(line_obj, start, end)                   animates a line being drawn.

count_up(obj_at, start_value, end_value, prefix, suffix, start, end, size=1.6, glow=0)  animated number.

light(color, at, energy=500, size=3, aim=(0,0,0))       extra soft light; a colored rim light behind
   the subject (e.g. at (3, 6, 4)) makes it pop from the background.
add_floor(under=obj) or add_floor(z)   the ground. In a real place: the place's own ground under the
   model. In studio/white: a soft reflective floor fading into the backdrop.
group(*objs) -> handle   copy(obj, at)   scatter(obj, count, area=(10,10,4), center, seed)
material(color, preset, glow, alpha)  for your own bpy objects.
Animation:
key(obj, prop, [(t, value), ...], smooth=True)   prop: "location" | "rotation" (degrees) | "scale" | "hide"
appear(obj, at, dur=0.45)  pops in with a slight overshoot.   disappear(obj, at, dur=0.4)
slide_in(obj, from_offset=(0,0,-3), at, dur=0.6)   move(obj, to, start, end)
spin(obj, turns=1, axis="z")   bob(obj, amount=0.15, period=2.5)   pulse(obj, amount=0.12, period=1)
Camera (call once, last):
camera(kind, target=(0,0,0), distance=12, height=1.5, angle=0, sweep=50, lens=50, follow=None,
       blur=2.8, shake=0)
   kind: static | orbit (sweeps `sweep` degrees) | push (moves in) | pull (moves out) | pan | crane (rises)
         | top (looks down) | drift (subtle). angle: 0 = front, 90 = right side, 180 = behind.
   blur: depth-of-field f-stop (1.8-4 = cinematic background blur; None = all sharp, for flat diagrams).
   follow=obj keeps aiming at a moving object. shake 0.02-0.05 adds a subtle handheld feel.
You may also use math, random, Vector and plain bpy for anything the toolkit lacks.

EXAMPLE (hero model in a real place; "meadow" stands for one of this video's places):
setup("meadow")
cat = model("cat", size=5, at=(0, 0, 0.3), rot=(0, 0, -20))
add_floor(under=cat)                                  # stands on the real grass, real shadow
light("#ffb36b", at=(3, 6, 4), energy=600)            # gentle warm rim light from behind
tag = card("25-150 Hz", at=(0, -0.8, 3.9), width=3.2, height=0.9, accent="#ffb36b")
appear(tag, at=0.8)
camera("orbit", target=(0, 0, 0.8), distance=11, height=1.2, sweep=30, blur=2.2)

EXAMPLE (neon diagram):
setup("dark", tint="#0d1626")
pts = [(-3.5 + i * 0.1, 0, 1.0 + 1.2 * math.sin(i * 0.4)) for i in range(71)]
draw_on(line(pts, color="#00e5ff", width=0.06), start=0.2, end=2.4)
spike = line([(1.2, -0.1, 1.0), (1.4, -0.1, 2.8), (1.6, -0.1, -0.6), (1.8, -0.1, 1.0)], color="#ff4d6d", width=0.05)
appear(spike, at=2.2)
label((1.5, 0, 2.8), "HIDDEN CRY", offset=(0.8, -0.3, 1.2), color="#ff8fa3")
camera("push", target=(0, 0, 1.4), distance=11, height=0.6, blur=None)
"""

PLAN_SYSTEM = """\
You direct a vertical (9:16) explainer video. Visuals can be polished 3D animations rendered in
Blender (3D models from Sketchfab, shapes with nice materials, infographic cards, glowing diagrams,
labels, arrows, counters, real places (fields, forests, cities, hangars...), cinematic camera moves) and/or real
footage and stock clips (Wikimedia Commons, Pixabay).

First decide the approach for the WHOLE video:
- "all_3d": every line is a 3D animation. Best for science, how-things-work, abstract ideas,
  mechanisms, body/space/tech topics, or when real footage would be generic or hard to find.
- "mixed": 3D animations plus real footage. Best when real-world footage adds a lot: history, real
  events, places, sports, animal behavior, people. Footage at most about half the lines.
Then for every line choose "animation" or "footage" (only "animation" when all_3d), and for
animations describe one concrete, readable shot: the hero subject, what moves, supporting
elements, and the camera move. One idea per shot; visual metaphors and diagrams are great.
Pick ONE visual style: an env, a backdrop tint, and a palette of 3-4 hex colors that suits the topic.
REAL PLACES: model shots happen in photographed real locations that are searched for THIS video
(a library of ~1000 real-world 360-degree photos: airfields, harbors, deserts, castles, ruins, dams,
farmland, forests, city streets, hangars, factories...). In "places", describe 1-3 settings where
the story really happens, each with a short code name (e.g. "runway", "ming_wall", "dockyard"), a
search of 3-6 plain keywords like photo tags ("runway airport asphalt field"), and what it must
show. Be specific to the subject: a jump jet on an airfield, a battleship in a harbor, a desert
rifle in the desert, a WW1 tank in mud. Set style.env to the main place's code name. Shots may use
another listed place when the story moves. Pure diagrams can use studio, white or dark. Never put
models in an empty dark void or space, or in a place that makes no sense for them.
Huge things (bridges, dams, canals, walls, buildings, landmarks, big ships, rockets on the pad) can't
stand in a place at real size: show them with REAL FOOTAGE, or explain their mechanism in a clean
diagram shot (studio/white/dark) clearly made of simple shapes. Never plan a shrunken model of them
in a real place. Machines up to ~60 m (vehicles, aircraft, guns, engines) work great as models.
List the 3D models to fetch from Sketchfab: concrete physical things that appear in the animations
(e.g. "AV-8B Harrier", "human heart", "Roman legionary", "B-2 bomber"). At most 6. Name the EXACT
type (a specific aircraft, tank, ship or gun model, not a generic "fighter"), and give 2 alternative
searches (other names, variants, e.g. "Sea Harrier", "Harrier jump jet"). Don't request models for
things simple shapes or text can show. Give each a short code name.
"""

DESIGN_SYSTEM = """\
You are a senior 3D motion designer writing Blender shot scripts for a vertical explainer video.
The bar is a top science/explainer channel (Kurzgesagt-level clarity, Apple-keynote polish), not a
school project. Write one script per requested shot using ONLY the toolkit below.

Design principles:
- ONE clear hero per shot, BIG: the main subject should fill 50-80% of the frame width.
- Real-world look for models: put them in a REAL PLACE env standing on add_floor(under=hero), like a
  cinematic photo of the real thing. The place gives depth; use camera blur (DOF) on model shots.
- No sci-fi filler: no floating sparkles, no glowing rings or pulsing circles, no starfields, no
  black voids behind models. Overlays (cards, labels, arrows, lines) only when they explain something.
- Light like a pro: places light themselves from the photo's sun; add at most a gentle rim light.
  In studio/dark, add a colored rim light from the palette, and give diagrams glow.
- Materials with intent: models for real things; glossy/metal/glass/neon presets for shapes. Never
  leave flat default grey. Text goes on card()s or as glowing labels, never floating plain.
- Motion with rhythm: elements appear one after another (staggered 0.2-0.5 s), pop with appear() or
  slide_in(), the camera always moves slowly and smoothly. Finish the key action by ~80% of the shot.
- Keep it clean: 3-6 elements per shot is plenty. Empty frames and clutter both look cheap.
- Consistency: same env, tint and palette across shots unless the story changes place.
- Cinematic, like a high-end documentary or a game trailer: low camera angles for vehicles, lens
  35-85, close-ups on the detail the line is about (the swivelling nozzles, the gun breech, the
  rivets), slow push-ins and orbits, depth of field. Model shots carry at most ONE card or label;
  let the model and the place do the talking. Prefer a strong hero shot over a diagram.
- The first shot is the hook: the most impressive model shot of the video, already moving at frame one.
- Real objects move only the way they really move: vehicles drive, aircraft fly or hover, guns
  recoil, parts turn on their real axis. NEVER slide, wobble, shake, tilt or seesaw a whole model
  to show bending, swaying, vibration, flexing or forces: keep the model still and show the
  behaviour with overlays (arrows, lines, a ghost outline), or in a diagram shot made of shapes.

Rules:
- Start with setup(env, tint). Use the video's style env and palette unless a shot clearly needs another.
- Everything important must stay in frame and clear of the subtitles: within about x in [-3.2, 3.2]
  and z from 1.5 below the camera target to 3.8 above it, at distance 12 (scale with distance).
  The lower third holds big subtitles and the very top ~6% is covered by the Shorts app's buttons,
  so nothing important goes there.
  Point the camera at the center of the subject; anything more than ~2 units below the target sits
  behind the subtitles.
  Never write the narration itself as 3D text. Use 3D text only for short names, numbers, or labels
  (max ~3 words).
- Every shot needs motion: a camera move AND something animating. Nothing should be frozen.
- Make things readable: big simple shapes, strong color contrast with the background, glow on diagrams.
- Model orientation is unknown at first: start with rot=(0,0,0); previews will show you what to fix.
- Keep scripts short (under ~45 lines), no imports, no file access.
- Keep it renderable: downloaded models can have 100k+ faces each, so use at most ~8 copies of a
  model in one shot. For armies, crowds or swarms, use a few models up front and simple shapes
  for the rest.

""" + API

REVIEW_SYSTEM = """\
You are the art director reviewing low-res preview frames of 3D shots for a vertical explainer
video. Each sheet shows the start, middle and end frames side by side (previews are grainier than
the final render; ignore grain). For every shot decide "ok" or "fix". The bar: would this look
polished on a top explainer channel?
Fix a shot when:
- the hero is cut off, too small (under ~50% of frame width), hidden, or badly off-center;
- a model faces away, lies sideways or is upside down (fix with rot);
- text is unreadable, misspelled, too small, or sits in the lower third (subtitles go there) or the
  very top ~6% of the frame (the Shorts app's buttons);
- it looks cheap: flat grey shapes, plain floating text, dull lighting, a mostly empty frame, clutter;
- a model floats in a dark void, space or a plain backdrop instead of standing in a real place on
  add_floor(under=...), or the shot uses sparkles, starfields or glowing rings / pulsing circles;
- the place makes no sense for the subject (a ship on grass, a jet among skyscrapers, a medieval
  wall in a modern street): switch to a better place from the brief;
- the model looks like a toy, a low-detail blob, or the wrong type of the thing named in the line;
- the scale is wrong for the place: a bridge, ship or building shrunk to toy size in a real photo
  (replace it with a diagram shot), or a model floating instead of standing on the ground;
- a whole model slides, wobbles, tilts or rocks to show a force or flexing (keep it still and use
  overlays), or anything moves in a way the real thing never would;
- it feels like a slide rather than a film shot: too many cards, labels or arrows, a flat front view;
- nothing moves between frames, or the shot doesn't show what the line says;
- the script raised an error, or the scene is TOO HEAVY to render (both shown in the text).
For "fix", return the COMPLETE improved script (fix the problems AND raise the polish).
For "ok", return an empty code string.

""" + API

_PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["approach", "reason", "style", "places", "assets", "lines"],
    "properties": {
        "approach": {"type": "string", "enum": ["all_3d", "mixed"]},
        "reason": {"type": "string"},
        "style": {"type": "object", "additionalProperties": False, "required": ["env", "tint", "palette"],
                  "properties": {"env": {"type": "string"}, "tint": {"type": "string"},
                                 "palette": {"type": "array", "items": {"type": "string"}}}},
        "assets": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "search", "alternates"],
            "properties": {"name": {"type": "string"}, "search": {"type": "string"},
                           "alternates": {"type": "array", "items": {"type": "string"}}}}},
        "places": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "search", "want"],
            "properties": {"name": {"type": "string"}, "search": {"type": "string"},
                           "want": {"type": "string"}}}},
        "lines": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["line", "visual", "idea"],
            "properties": {"line": {"type": "integer"}, "visual": {"type": "string", "enum": ["animation", "footage"]},
                           "idea": {"type": "string"}}}}}}
_PICK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["picks"], "properties": {
    "picks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["name", "choice"],
        "properties": {"name": {"type": "string"}, "choice": {"type": "integer"}}}}}}
_SHOTS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["shots"], "properties": {
    "shots": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["line", "code"],
        "properties": {"line": {"type": "integer"}, "code": {"type": "string"}}}}}}
_REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["shots"], "properties": {
    "shots": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["line", "verdict", "code"],
        "properties": {"line": {"type": "integer"}, "verdict": {"type": "string", "enum": ["ok", "fix"]},
                       "code": {"type": "string"}}}}}}


def _check(code: str) -> str | None:
    """Returns a problem description if the script uses something it must not."""
    for bad in FORBIDDEN:
        if bad in code:
            return f"forbidden construct {bad.strip()!r} (no imports, dunder access or file/system calls)"
    for bad, why in NOT_ALLOWED.items():
        if bad in code:
            return f"{why} - remove {bad.rstrip('(')}()"
    if "setup(" not in code:
        return "the script must start with setup(env)"
    return None


def _run(job: dict, job_file: Path, timeout: int = 900) -> str | None:
    """Runs one Blender job; returns an error message or None."""
    job_file.write_text(json.dumps(job), encoding="utf-8")
    try:
        proc = subprocess.run([blender_path(), "-b", "--factory-startup", "-P", str(RUNNER), "--", str(job_file)],
                              capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"Blender took longer than {timeout // 60} minutes (the scene is too heavy)"
    err_file = Path(job["out"]) / "error.txt" if job["mode"] == "preview" else Path(job["out"] + ".error.txt")
    if err_file.exists():
        return err_file.read_text(encoding="utf-8")
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout)[-800:]
    return None


def _sheet(frames_dir: Path, out: Path, caption: str) -> bool:
    frames = [Image.open(p).convert("RGB") for p in sorted(frames_dir.glob("frame_*.png"))]
    if not frames:
        return False
    w, h = frames[0].size
    sheet = Image.new("RGB", (w * len(frames), h + 50), (20, 20, 20))
    for i, f in enumerate(frames):
        sheet.paste(f, (i * w, 50))
    ImageDraw.Draw(sheet).text((10, 10), caption[:120], fill=(255, 220, 0), font=_font(26))
    sheet.thumbnail((1400, 1000))
    sheet.save(out, quality=85)
    return True


def _pick_models(plan: dict, work: Path) -> tuple[dict[str, str], list[str]]:
    """Searches Sketchfab for each requested model and lets Claude pick. Returns ({name: path}, credits)."""
    wanted = plan.get("assets", [])[:6]
    if not wanted:
        return {}, []
    sheet_dir = work / "model_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    options, sheets = {}, []
    for asset in wanted:
        found, seen = [], set()
        for query in [asset["search"], *asset.get("alternates", [])][:3]:  # exact name, then other names
            try:
                for m in models3d.search(query, limit=6):
                    if m["uid"] not in seen and len(found) < 9:
                        seen.add(m["uid"])
                        found.append(m)
            except requests.RequestException as exc:
                print(f"          Sketchfab search failed for {query!r}: {exc}")
        if not found:
            print(f"[models] no free model for {asset['name']!r}")
            continue
        thumbs = []
        for m in found:
            try:
                thumbs.append(Image.open(BytesIO(requests.get(m["thumb"], timeout=30).content)).convert("RGB"))
            except Exception:
                thumbs.append(None)
        path = sheet_dir / f"{re.sub(r'[^a-z0-9]+', '_', asset['name'].lower())}.jpg"
        _contact_sheet(thumbs, [(f"{m['name'][:20]} {m['faces'] // 1000}k", (255, 220, 0)) for m in found], path)
        options[asset["name"]] = found
        sheets.append(path)
    if not sheets:
        return {}, []
    print(f"[models] Claude is choosing 3D models for: {', '.join(options)}")
    prompt = "Pick the best model for each needed object (0 = none of them is right):\n" + "\n".join(
        f"- {name} (searched {next(a['search'] for a in wanted if a['name'] == name)!r}): sheet {sheets[i].name}"
        for i, name in enumerate(options))
    system = ("You pick 3D models for a cinematic explainer video from Sketchfab search thumbnails "
              "(each label ends with the model's face count, e.g. 85k). It must be the EXACT thing named: "
              "the right aircraft, tank, ship or gun type, not a similar-looking one (an F-16 is not a "
              "Harrier); variants of the same family count as right (a Sea Harrier for a Harrier, a "
              "Tiger II for a Tiger). Among correct ones, choose the MOST DETAILED: prefer 50k+ faces over "
              "a 10k one whenever it looks at least as good, with real textures and fine geometry; a single "
              "clean object, not a whole scene, not low-poly, not a cartoon or chibi. Answer 0 if none is "
              "the right type: real footage is better than the wrong model.")
    picks = {p["name"]: p["choice"] for p in ask(system, prompt, _PICK_SCHEMA, images=sheets)["picks"]}
    assets, credits = {}, []
    for name, found in options.items():
        choice = picks.get(name, 0)
        if not 1 <= choice <= len(found):
            continue
        try:
            path = models3d.download(found[choice - 1])
            assets[name] = str(path)
            credits.append(models3d.credit(path))
            print(f"[models] {name}: {found[choice - 1]['name']}")
        except Exception as exc:
            print(f"[models] {name}: download failed ({exc})")
    return assets, credits


def _pick_places(plan: dict, work: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Searches the Poly Haven library for each setting the plan describes; Claude picks from the
    panorama thumbnails. Returns ({code name: .hdr path}, {code name: what the photo shows})."""
    from . import hdri
    wanted = plan.get("places", [])[:3]
    if not wanted:
        return {}, {}
    sheet_dir = work / "place_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    options, sheets = {}, []
    for place in wanted:
        try:
            found = hdri.search(place["search"], limit=8)
            if len(found) < 4:  # thin results: also search with the description's words
                ids = {f["id"] for f in found}
                found += [f for f in hdri.search(place["want"], limit=8) if f["id"] not in ids][:8 - len(found)]
        except Exception as exc:
            print(f"[places] search failed for {place['search']!r}: {exc}")
            found = []
        if not found:
            print(f"[places] nothing found for {place['name']!r}")
            continue
        thumbs = []
        for f in found:
            try:
                thumbs.append(Image.open(BytesIO(requests.get(f["thumb"], timeout=30).content)).convert("RGB"))
            except Exception:
                thumbs.append(None)
        path = sheet_dir / f"{re.sub(r'[^a-z0-9]+', '_', place['name'].lower())}.jpg"
        _contact_sheet(thumbs, [(f["name"][:24], (120, 220, 255)) for f in found], path)
        options[place["name"]] = found
        sheets.append(path)
    if not sheets:
        return {}, {}
    print(f"[places] Claude is choosing real places for: {', '.join(options)}")
    want = {w["name"]: w for w in wanted}
    prompt = "Pick the best photo for each setting (0 = none of them fits):\n" + "\n".join(
        f"- {name}: {want[name]['want']} (sheet {sheets[i].name})" for i, name in enumerate(options))
    system = ("You pick real-world 360-degree photos (shown as wide panoramas) to stand 3D models in for a "
              "cinematic explainer video. The camera will look at a model standing on the photo's ground, "
              "so choose a place whose ground and surroundings truly fit the setting: an open runway for "
              "jets, a dock for ships, mud for WW1 tanks, ancient stone for ancient history. Prefer open "
              "ground in front of the camera, good light and a believable, atmospheric scene. Never pick an "
              "indoor room, a parking lot or a city street unless the setting asks for it. The place must "
              "not contradict the story's country or era: no European castle or cafe for ancient China, no "
              "modern street for a medieval scene. When no photo matches the region, prefer neutral open "
              "land (hills, fields, desert, forest) over a wrong landmark. Answer 0 if none fits.")
    picks = {k["name"]: k["choice"] for k in ask(system, prompt, _PICK_SCHEMA, images=sheets)["picks"]}
    places, notes = {}, {}
    for name, found in options.items():
        choice = picks.get(name, 0)
        if not 1 <= choice <= len(found):
            print(f"[places] {name}: no photo fits")
            continue
        try:
            places[name] = str(hdri.download_place(found[choice - 1]["id"]))
            notes[name] = f"{want[name]['want']} (photo: {found[choice - 1]['name']})"
            print(f"[places] {name}: {found[choice - 1]['name']}")
        except Exception as exc:
            print(f"[places] {name}: download failed ({exc})")
    return places, notes


def _places_note(places: dict, notes: dict) -> str:
    if places:
        return ("This video's REAL PLACES for setup() (use only these for model shots):\n"
                + "\n".join(f"  \"{name}\": {notes.get(name, '')}" for name in places))
    return ("No places were found for this video; use one of the stock places for model shots: meadow, "
            "forest, mountains, desert, snow, city, hangar, airfield (runway), airbase, harbor, coast, "
            "desert_road, ruins, castle, muddy_forest, farmland, dam, railway, industrial, valley, interior.")


def animate(script: dict, run_dir: Path, durations: list[float]) -> tuple[list, list[str], list[int]]:
    """Returns (shots per line or None, credits, indexes of lines that should use real footage)."""
    from .visuals import Shot

    lines = script["lines"]
    work = run_dir / "anim"
    work.mkdir(exist_ok=True)
    state_file = work / "state.json"
    if state_file.exists():  # resuming a run: the shots were already designed and reviewed
        state = json.loads(state_file.read_text(encoding="utf-8"))
        print("[anim] resuming: reusing the reviewed shot scripts")
        return _render_finals(lines, work, durations, state["assets"], state["credits"], str(ensure_hdris()),
                              state["anim_lines"], {int(k): v for k, v in state["codes"].items()},
                              {int(k): v for k, v in state["errors"].items()}, state.get("heavy", []),
                              state.get("places", {}))

    # 1. Plan
    print("[anim] Claude is planning the shots...")
    listing = "\n".join(f"Line {i + 1} ({durations[i]:.1f}s): {strip_tags(l['text'])}" for i, l in enumerate(lines))
    plan = ask(PLAN_SYSTEM, f"Title: {script['title']}\nTopic: {script.get('topic', '')}\n\n{listing}", _PLAN_SCHEMA)
    (work / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    ideas = {p["line"]: p for p in plan["lines"]}
    if plan.get("approach") == "all_3d":
        anim_lines = list(range(len(lines)))
    else:
        anim_lines = [i for i in range(len(lines)) if ideas.get(i + 1, {}).get("visual", "animation") == "animation"]
    footage_lines = [i for i in range(len(lines)) if i not in anim_lines]
    print(f"[anim] approach: {plan.get('approach')} ({plan.get('reason', '')[:100]})")
    print(f"[anim] {len(anim_lines)} animated shots, {len(footage_lines)} real-footage lines, "
          f"style {plan['style']['env']} tint {plan['style'].get('tint')} {plan['style']['palette']}")
    hdri_dir = str(ensure_hdris())

    # 2. Models and places
    assets, credits = _pick_models(plan, work)
    places, place_notes = _pick_places(plan, work)
    if places and plan["style"]["env"] not in places and plan["style"]["env"] not in ("studio", "dark", "white"):
        plan["style"]["env"] = next(iter(places))  # the planned main place wasn't found: use the first one
    places_note = _places_note(places, place_notes)

    # 3. Design
    print("[anim] Claude is writing the shot scripts...")
    brief = (f"Video style: env {plan['style']['env']!r}, tint {plan['style'].get('tint')!r}, "
             f"palette {plan['style']['palette']}.\n"
             f"3D models available to model(): {sorted(assets) or 'none (use shapes and text)'}.\n"
             f"{places_note}\n\n"
             "Write a script for each of these shots:\n" + "\n".join(
                 f"Line {i + 1} ({durations[i]:.1f}s) narration: {strip_tags(lines[i]['text'])}\n"
                 f"  idea: {ideas.get(i + 1, {}).get('idea', 'illustrate the line')}" for i in anim_lines))
    codes = {s["line"] - 1: s["code"] for s in ask(DESIGN_SYSTEM, brief, _SHOTS_SCHEMA)["shots"]}

    # 4. Preview and review rounds
    heavy: dict[int, str] = {}  # line -> why its scene is too heavy to render in time

    def preview_all(round_no: int, which: list[int]) -> dict[int, str]:
        errors = {}
        for i in which:
            heavy.pop(i, None)
            code = codes.get(i, "")
            problem = _check(code)
            shot_dir = work / f"r{round_no}" / f"line_{i + 1:02}"
            shot_dir.mkdir(parents=True, exist_ok=True)
            if problem:
                errors[i] = problem
                continue
            (shot_dir / "shot.py").write_text(code, encoding="utf-8")
            began = time.monotonic()
            err = _run({"code": str(shot_dir / "shot.py"), "out": str(shot_dir / "frames"), "mode": "preview",
                        "seconds": durations[i], "assets": assets, "hdri_dir": hdri_dir, "places": places},
                       shot_dir / "job.json")
            took = time.monotonic() - began
            if err:
                errors[i] = err
            else:
                if took > HEAVY_PREVIEW_SECONDS:
                    stats_file = shot_dir / "frames" / "stats.json"
                    stats = json.loads(stats_file.read_text(encoding="utf-8")) if stats_file.exists() else {}
                    heavy[i] = (f"the 3-frame preview took {took:.0f}s ({stats.get('objects', '?')} objects, "
                                f"{stats.get('faces', 0) / 1e6:.1f}M unique faces)")
                    print(f"[anim] line {i + 1}: heavy scene, {heavy[i]}")
                _sheet(shot_dir / "frames", work / f"r{round_no}" / f"shot_{i + 1:02}.jpg",
                       f"Line {i + 1}: {strip_tags(lines[i]['text'])}")
        return errors

    pending = list(anim_lines)
    errors: dict[int, str] = {}  # line -> error from its latest preview attempt
    for round_no in range(REVIEW_ROUNDS + 1):
        print(f"[anim] previewing {len(pending)} shots (round {round_no + 1})...")
        new_errors = preview_all(round_no, pending)
        for i in pending:
            if i in new_errors:
                errors[i] = new_errors[i]
            else:
                errors.pop(i, None)
        if round_no == REVIEW_ROUNDS:
            break
        sheets = [work / f"r{round_no}" / f"shot_{i + 1:02}.jpg" for i in pending
                  if (work / f"r{round_no}" / f"shot_{i + 1:02}.jpg").exists()]
        def describe(i: int) -> str:
            return (f"Line {i + 1} ({durations[i]:.1f}s): {strip_tags(lines[i]['text'])}\n"
                    f"  idea: {ideas.get(i + 1, {}).get('idea', '')}\n"
                    + (f"  ERROR: {errors[i]}\n" if i in errors else f"  preview: shot_{i + 1:02}.jpg\n")
                    + (f"  TOO HEAVY: {heavy[i]}; the final render would take too long. Use far fewer objects "
                       "and model copies (simple shapes for crowds).\n" if i in heavy else "")
                    + f"  current script:\n{codes.get(i, '')}\n")

        models_note = f"\n3D models available: {sorted(assets) or 'none'}.\n{places_note}"
        prompt = "Shots to review:\n" + "\n".join(describe(i) for i in pending) + models_note
        print(f"[anim] Claude is reviewing the previews (round {round_no + 1})...")
        verdicts = ask(REVIEW_SYSTEM, prompt, _REVIEW_SCHEMA, images=sheets or None)["shots"]
        fixes = {v["line"] - 1: v["code"] for v in verdicts if v["verdict"] == "fix" and v["code"].strip()}
        broken = [i for i in pending if (i in errors or i in heavy) and i not in fixes]
        if broken:  # a failing shot can't be "ok": ask again for just those
            print(f"[anim] asking Claude to repair {len(broken)} failing shots...")
            prompt = ("These shots FAILED. Each needs verdict \"fix\" with a complete, working script that "
                      "avoids the error:\n" + "\n".join(describe(i) for i in broken) + models_note)
            for v in ask(REVIEW_SYSTEM, prompt, _REVIEW_SCHEMA)["shots"]:
                if v["line"] - 1 in broken and v["code"].strip():
                    fixes[v["line"] - 1] = v["code"]
        pending = [i for i in fixes if i in anim_lines]  # broken shots Claude didn't fix fall back later
        codes.update(fixes)
        print(f"[anim] {len(pending)} shots need changes")
        if not pending:
            break

    # Save the reviewed shots so an interrupted run can be resumed (make_video.py --resume).
    state_file.write_text(json.dumps({"anim_lines": anim_lines, "assets": assets, "credits": credits,
                                      "codes": codes, "errors": errors, "heavy": list(heavy),
                                      "places": places}, indent=2), encoding="utf-8")
    return _render_finals(lines, work, durations, assets, credits, hdri_dir, anim_lines, codes, errors,
                          list(heavy), places)


def _render_finals(lines, work: Path, durations, assets, credits, hdri_dir, anim_lines, codes, errors, heavy=(),
                   places=None):
    """5. Final renders. Shots that fail (or are too heavy) are retried at lighter quality, then left to
    real footage. Finished clips get a .done marker so a resumed run skips them."""
    from .visuals import Shot

    shots: list = [None] * len(lines)
    footage_lines = [i for i in range(len(lines)) if i not in anim_lines]
    shot_dir = work / "final"
    shot_dir.mkdir(exist_ok=True)
    for i in anim_lines:
        if i in errors:
            print(f"[anim] line {i + 1}: still failing after review, using real footage instead")
            footage_lines.append(i)
            continue
        out = shot_dir / f"line_{i + 1:02}.mp4"
        done = out.with_suffix(".done")
        if done.exists() and out.exists():
            shots[i] = Shot("video", out, True)
            continue
        code_file = shot_dir / f"line_{i + 1:02}.py"
        code_file.write_text(codes[i], encoding="utf-8")
        job = {"code": str(code_file), "out": str(out), "mode": "final", "seconds": durations[i] + 0.1,
               "assets": assets, "hdri_dir": hdri_dir, "places": places or {}}
        timeout = int(120 + 90 * durations[i])  # normal shots render in ~30 s per second of video
        print(f"[anim] rendering line {i + 1} ({durations[i]:.1f}s)...")
        err = _run({**job, "quality": "fast"} if i in heavy else job, shot_dir / f"line_{i + 1:02}.json", timeout)
        if (err or not out.exists()) and i not in heavy:
            print(f"[anim] line {i + 1}: final render failed ({(err or '')[:120]}); retrying at lighter quality")
            out.unlink(missing_ok=True)
            Path(str(out) + ".error.txt").unlink(missing_ok=True)
            err = _run({**job, "quality": "fast"}, shot_dir / f"line_{i + 1:02}.json", timeout)
        if err or not out.exists():
            print(f"[anim] line {i + 1}: final render failed ({(err or '')[:120]}), using real footage instead")
            footage_lines.append(i)
            continue
        done.write_text("ok", encoding="utf-8")
        shots[i] = Shot("video", out, True)
    return shots, credits, sorted(footage_lines)
