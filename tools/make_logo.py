"""Renders the Rendered Facts channel logo in Blender (same toolkit as the video animations).

  tools\\blender\\<version>\\blender.exe -b --factory-startup -P tools\\make_logo.py -- <variant> <out.png> [size]

variant: cube | monogram | tile
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "sb_blender"))

import bpy  # noqa: E402
import sb  # noqa: E402
from mathutils import Matrix  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
variant, out = args[0], os.path.abspath(args[1])
size = int(args[2]) if len(args) > 2 else 1024

CYAN = "#00E5FF"
NAVY = "#0d1626"
FONT = r"C:\Windows\Fonts\ariblk.ttf"  # Arial Black: reads well at tiny sizes

bpy.ops.wm.read_factory_settings(use_empty=True)
sb._state.update({"seconds": 1.0, "assets": {}, "camera": None, "target": None,
                  "hdri_dir": os.path.join(ROOT, "assets", "hdri")})
sb.setup("studio", tint=NAVY)


def letters(content, at, size, color="#ffffff", depth=0.22, glow=0.0, rot=(90, 0, 0)):
    t = sb.text(content, at=at, size=size, color=color, depth=depth, glow=glow, rot=rot)
    if os.path.exists(FONT):
        t.data.font = bpy.data.fonts.load(FONT, check_existing=True)
    t.data.space_character = 0.92
    t.data.bevel_depth = 0.025 * size
    bsdf = t.data.materials[0].node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.38  # satin: no blotchy studio reflections
    bsdf.inputs["Coat Weight"].default_value = 0.0
    return t


def glow_edges(obj, color, thickness=0.05, strength=7):
    """A glowing wireframe copy of obj's edges."""
    edges = obj.copy()
    edges.data = obj.data.copy()
    sb._link(edges)
    edges.modifiers.clear()  # wire the cube's hard edges, not its bevel
    mod = edges.modifiers.new("Wire", "WIREFRAME")
    mod.thickness = thickness
    mod.use_even_offset = True
    edges.data.materials.clear()
    edges.data.materials.append(sb.material(color, "plastic", glow=strength))
    return edges


if variant == "cube":
    cube = sb.shape("cube", size=3.4, at=(0, 0, 0), color="#24365c", preset="matte")
    cube.rotation_euler = (math.radians(-14), 0, math.radians(24))
    edges = glow_edges(cube, CYAN, thickness=0.08)
    edges.rotation_euler = cube.rotation_euler
    face = letters("RF", at=(0, -1.72, -0.08), size=1.6, depth=0.14)  # big, on the front face
    face.parent = cube
    sb.light(CYAN, at=(4, 5, 3), energy=900, size=3)
    sb.light("#ffffff", at=(-4, -7, 5), energy=700, size=4)
    sb.camera("static", target=(0, 0, 0), distance=10.5, height=0.0, lens=50, blur=None)
elif variant == "monogram":
    mono = letters("RF", at=(0, 0, 0.4), size=3.3, depth=0.32, rot=(90, 0, -16))
    bar = sb.shape("cube", size=1, at=(0.1, 0.2, -1.4), color=CYAN, glow=6, bevel=False)
    bar.scale = (4.0, 0.14, 0.3)
    bar.rotation_euler = (0, 0, math.radians(-16))
    sb.light(CYAN, at=(4, 4, 2), energy=1400, size=3)          # cyan rim on the letter sides
    sb.light("#ffffff", at=(-3, -7, 4), energy=700, size=5)
    sb.camera("static", target=(0, 0, 0), distance=10, height=0.8, lens=50, blur=None)
elif variant == "tile":
    tile = sb.shape("cube", size=1, at=(0, 0.3, 0), color="#00b4d8", preset="plastic", bevel=False)
    tile.data.transform(Matrix.Diagonal((3.6, 0.6, 3.6, 1.0)))  # real size in the mesh, so bevels stay round
    mod = tile.modifiers.new("Round", "BEVEL")
    mod.width, mod.segments, mod.affect = 0.8, 12, "EDGES"
    tile.rotation_euler = (math.radians(4), 0, math.radians(-14))
    t = letters("RF", at=(0, 0, 0), size=1.9, color="#0b1424", depth=0.3)  # dark letters: strong contrast
    t.parent = tile
    t.location = (0, -0.31, -0.02)  # on the front face
    sb.light("#ffffff", at=(-4, -6, 5), energy=700, size=4)
    sb.light("#b8f6ff", at=(4, 4, 3), energy=600, size=3)
    sb.camera("static", target=(0, 0, 0), distance=10, height=0.4, lens=50, blur=None)
else:
    raise SystemExit(f"unknown variant {variant}")

scene = bpy.context.scene
scene.camera.data.shift_y = 0.0  # the video camera leaves room for subtitles; a logo is centered
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.resolution_percentage = 100
sb.finish("final")
scene.render.use_motion_blur = False
scene.eevee.taa_render_samples = 64
scene.render.image_settings.file_format = "PNG"
scene.frame_set(1)
scene.render.filepath = out
bpy.ops.render.render(write_still=True)
print(f"[logo] {variant} -> {out}")
