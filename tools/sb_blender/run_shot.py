"""Runs one Claude-written shot script inside Blender and renders it. Runs INSIDE Blender:

  blender -b --factory-startup -P run_shot.py -- <job.json>

job.json: {"code": path to the shot script, "out": output path (folder for previews, .mp4 for final),
           "mode": "preview" | "final", "seconds": float, "assets": {name: model path},
           "times": [seconds to preview], "quality": "fast" for a lighter final render}
Previews also write <folder>/stats.json with the scene's face count.
Writes <out>.error.txt (preview: <folder>/error.txt) if the script fails.
"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import sb  # noqa: E402

job = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8-sig"))
mode = job["mode"]
out = os.path.abspath(job["out"])
error_file = os.path.join(out, "error.txt") if mode == "preview" else out + ".error.txt"
if mode == "preview":
    os.makedirs(out, exist_ok=True)
t0 = time.time()

bpy.ops.wm.read_factory_settings(use_empty=True)
sb._state.update({"seconds": float(job["seconds"]), "assets": job.get("assets", {}),
                  "camera": None, "target": None, "hdri_dir": job.get("hdri_dir", ""),
                  "places": job.get("places", {})})
namespace = {name: getattr(sb, name) for name in dir(sb) if not name.startswith("_")}
namespace.update({"SECONDS": float(job["seconds"]), "math": __import__("math"),
                  "random": __import__("random"), "Vector": sb.Vector, "bpy": bpy})
code = open(job["code"], encoding="utf-8-sig").read()
try:
    if "setup(" not in code:
        sb.setup("studio")
    exec(compile(code, "shot.py", "exec"), namespace)
except Exception:
    tb = traceback.format_exc().splitlines()
    shown = [line for line in tb if "shot.py" in line or not line.startswith("  File")][-8:]
    with open(error_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(shown))
    print("[shot] FAILED:", shown[-1], flush=True)
    sys.exit(1)

scene = bpy.context.scene
r = scene.render
# Scene weight: object count and unique geometry (copies share their mesh data, so they are cheap).
objects = len(scene.objects)
faces = sum(len(m.polygons) for m in {o.data for o in scene.objects if o.type == "MESH" and not o.hide_render})
print(f"[shot] {objects} objects, {faces} unique faces", flush=True)
if mode == "preview":
    with open(os.path.join(out, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump({"objects": objects, "faces": faces}, fh)
r.resolution_x, r.resolution_y = (540, 960) if mode == "preview" else (1080, 1920)
r.resolution_percentage = 100  # set before finish(): the vignette blur is sized from it
r.fps = sb.FPS
sb.finish("preview" if job.get("quality") == "fast" else mode)
if job.get("quality") == "fast":
    scene.eevee.taa_render_samples = 16  # lighter final: fewer samples, no motion blur

if mode == "preview":
    r.image_settings.file_format = "PNG"
    for i, t in enumerate(job.get("times", [0.3, sb._state["seconds"] / 2, sb._state["seconds"] - 0.3])):
        scene.frame_set(sb._frame(t))
        r.filepath = os.path.join(out, f"frame_{i}.png")
        bpy.ops.render.render(write_still=True)
else:
    scene.frame_end = max(sb._frame(sb._state["seconds"]) - 1, 1)
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format = "MPEG4"
    r.ffmpeg.codec = "H264"
    r.ffmpeg.constant_rate_factor = "HIGH"
    r.filepath = out
    bpy.ops.render.render(animation=True)
print(f"[shot] {mode} done in {time.time() - t0:.0f}s", flush=True)
