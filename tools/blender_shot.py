"""Renders one vertical (1080x1920) shot of a 3D model in Blender. Runs INSIDE Blender:

  blender -b --factory-startup -P tools/blender_shot.py -- <model.glb> <out.mp4|png> <shot> <seconds> [nose] [samples]

shot:    hero | orbit | flyby | push_in | underside | top_down
seconds: clip length; 0 renders a single still frame (PNG) instead, handy for previews
nose:    which way the model's nose points after import: +x, -x, +y, -y, or auto (default)
samples: Eevee render samples (default 16; higher = cleaner but slower)
"""
import math
import os
import sys
import time

import bpy
from mathutils import Matrix, Vector

args = sys.argv[sys.argv.index("--") + 1:]
# Absolute paths: Blender resolves bare relative output paths against the drive root, not the cwd.
MODEL, OUT = os.path.abspath(args[0]), os.path.abspath(args[1])
SHOT, SECONDS = args[2], float(args[3])
NOSE = args[4] if len(args) > 4 else "auto"
SAMPLES = int(args[5]) if len(args) > 5 else 16
W, H, FPS = 1080, 1920, 30
GEAR_WORDS = ("gear", "wheel", "tire", "tyre", "strut", "landing")
SPAN = 10.0  # the model is scaled so its largest dimension is this many units

t_start = time.time()
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# --- Model -------------------------------------------------------------------------------
bpy.ops.import_scene.gltf(filepath=MODEL)
meshes = [o for o in scene.objects if o.type == "MESH"]
bpy.context.view_layer.update()


def world_box(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    return (Vector([min(p[i] for p in pts) for i in range(3)]),
            Vector([max(p[i] for p in pts) for i in range(3)]))


hidden = 0
if SHOT != "ground":  # flying shots: retract the landing gear
    all_lo, all_hi = world_box(meshes)
    longest = max(all_hi - all_lo)
    height = all_hi.z - all_lo.z
    body_mid_z = (all_lo.z + all_hi.z) / 2

    def footprint(o):
        lo_o, hi_o = world_box([o])
        return (hi_o.x - lo_o.x) * (hi_o.y - lo_o.y)

    skin_bottom = world_box([max(meshes, key=footprint)])[0].z  # underside of the main airframe part
    for o in meshes:
        lo_o, hi_o = world_box([o])
        named_gear = any(w in o.name.lower() for w in GEAR_WORDS)
        # gear legs and wheels hang clearly below the airframe's skin
        hangs_below = lo_o.z < skin_bottom - 0.08 * height
        # gear doors and small fittings: small parts sitting low on the body
        small_and_low = max(hi_o - lo_o) < 0.2 * longest and (lo_o.z + hi_o.z) / 2 < body_mid_z - 0.05 * height
        if named_gear or hangs_below or small_and_low:
            o.hide_render = True
            hidden += 1

root = bpy.data.objects.new("Model", None)
scene.collection.objects.link(root)
for o in list(scene.objects):
    if o.parent is None and o is not root:
        o.parent = root
bpy.context.view_layer.update()

pts = [o.matrix_world @ Vector(c) for o in meshes if not o.hide_render for c in o.bound_box]
lo = Vector([min(p[i] for p in pts) for i in range(3)])
hi = Vector([max(p[i] for p in pts) for i in range(3)])
size = hi - lo
scale = SPAN / max(size)

if NOSE == "auto":  # aircraft are usually wider (wings) than long: nose is across the long axis
    NOSE = "-y" if size.x >= size.y else "+x"
yaw = {"+y": 0.0, "-y": math.pi, "+x": math.pi / 2, "-x": -math.pi / 2}[NOSE]  # turns the nose to +Y
rot = Matrix.Rotation(yaw, 4, "Z")
root.scale = (scale, scale, scale)
root.rotation_euler = (0, 0, yaw)
base = -(rot @ (((lo + hi) / 2) * scale))
root.location = base
print(f"[blender] model size {tuple(round(v * scale, 2) for v in size)}, nose {NOSE}, hid {hidden} gear parts",
      flush=True)

# --- World: sky gradient, ocean far below, sun --------------------------------------------
world = bpy.data.worlds.new("Sky")
scene.world = world
world.use_nodes = True
nt = world.node_tree
coords = nt.nodes.new("ShaderNodeTexCoord")
sep = nt.nodes.new("ShaderNodeSeparateXYZ")
ramp = nt.nodes.new("ShaderNodeValToRGB")
nt.links.new(coords.outputs["Generated"], sep.inputs[0])
nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
cr = ramp.color_ramp
# "Generated" Z is the view direction's height: 0 at the horizon, 1 straight up
cr.elements[0].position, cr.elements[0].color = 0.0, (0.62, 0.76, 0.92, 1)    # bright haze at the horizon
cr.elements[1].position, cr.elements[1].color = 0.55, (0.04, 0.17, 0.55, 1)   # deep blue overhead
mid = cr.elements.new(0.12)
mid.color = (0.25, 0.47, 0.85, 1)
nt.links.new(ramp.outputs["Color"], nt.nodes["Background"].inputs["Color"])
nt.nodes["Background"].inputs["Strength"].default_value = 1.0

bpy.ops.mesh.primitive_plane_add(size=6000, location=(0, 0, -60))
ocean = bpy.context.active_object
mat = bpy.data.materials.new("Ocean")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.01, 0.06, 0.13, 1)
bsdf.inputs["Roughness"].default_value = 0.22
noise = mat.node_tree.nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 900
bump = mat.node_tree.nodes.new("ShaderNodeBump")
bump.inputs["Strength"].default_value = 0.25
mat.node_tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
mat.node_tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
ocean.data.materials.append(mat)

sun_data = bpy.data.lights.new("Sun", "SUN")
sun_data.energy = 6.0
sun_data.color = (1.0, 0.95, 0.87)  # warm daylight against the blue sky
sun_data.angle = math.radians(2)
sun = bpy.data.objects.new("Sun", sun_data)
scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(55), 0, math.radians(200))  # behind the cameras, lighting the front

# --- Camera and motion --------------------------------------------------------------------
cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 50  # a longer lens from further away: less distortion, more like real aerial filming
cam_data.sensor_fit = "HORIZONTAL"
cam_data.sensor_width = 36
cam_data.clip_end = 20000
cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
focus = bpy.data.objects.new("Focus", None)
scene.collection.objects.link(focus)
track = cam.constraints.new("TRACK_TO")
track.target = focus
track.track_axis = "TRACK_NEGATIVE_Z"
track.up_axis = "UP_Y"


def ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def around(angle_deg: float, dist: float, elev_deg: float) -> Vector:
    """Camera position at `angle` from the nose (0 = straight ahead), `elev` above the horizon."""
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    return Vector((dist * math.sin(a) * math.cos(e), dist * math.cos(a) * math.cos(e), dist * math.sin(e)))


def frame_state(t: float):
    """Returns (camera position, model offset, focus point) for t in 0..1."""
    s = ease(t)
    if SHOT == "orbit":
        return around(-70 + 140 * s, 13.5, 9), Vector(), Vector()
    if SHOT == "flyby":   # plane passes the camera, camera turns to follow it
        offset = Vector((0, -40 + 80 * t, 0))
        return Vector((14, 0, -2)), offset, offset
    if SHOT == "push_in":
        return around(28, 28 - 12 * s, 6 + 5 * s), Vector(), Vector()
    if SHOT == "underside":   # plane flies overhead
        offset = Vector((0, -32 + 64 * t, 0))
        return Vector((3, 0, -16)), offset, offset
    if SHOT == "top_down":
        return around(180 + 25 * s, 22, 72), Vector(), Vector()
    # hero: slow arc across the front, slightly above
    return around(-35 + 55 * s, 14 - 2 * s, 11), Vector(), Vector()


frames = max(int(round(SECONDS * FPS)), 1)
for f in range(frames):
    t = f / max(frames - 1, 1)
    cam_pos, offset, aim = frame_state(t)
    cam.location = cam_pos
    focus.location = aim
    root.location = base + offset
    roll = math.radians(3.0) * math.sin(2 * math.pi * 0.35 * f / FPS)      # gentle banking
    pitch = math.radians(0.8) * math.sin(2 * math.pi * 0.21 * f / FPS + 1)
    root.rotation_euler = (pitch, roll, yaw)
    for obj, prop in ((cam, "location"), (focus, "location"), (root, "location"), (root, "rotation_euler")):
        obj.keyframe_insert(prop, frame=f + 1)

# --- Render -------------------------------------------------------------------------------
r = scene.render
for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
    try:
        r.engine = engine
        break
    except TypeError:
        continue
scene.eevee.taa_render_samples = SAMPLES
r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
r.fps = FPS
try:
    r.use_motion_blur = True
except AttributeError:
    scene.eevee.use_motion_blur = True
try:
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Punchy"
except TypeError:
    pass
scene.frame_start, scene.frame_end = 1, frames

if SECONDS == 0:
    r.image_settings.file_format = "PNG"
    r.filepath = OUT
    bpy.ops.render.render(write_still=True)
else:
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format = "MPEG4"
    r.ffmpeg.codec = "H264"
    r.ffmpeg.constant_rate_factor = "HIGH"
    r.filepath = OUT
    bpy.ops.render.render(animation=True)
print(f"[blender] rendered {frames} frame(s) of '{SHOT}' in {time.time() - t_start:.0f}s -> {OUT}", flush=True)
