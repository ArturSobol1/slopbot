"""SlopBot animation toolkit for Blender. Claude writes short shot scripts using these functions.

Coordinate conventions (the default camera sits in front, at -Y, looking toward +Y):
  x = left/right on screen, z = up, y = depth (positive = further away).
  The frame is vertical 9:16; the camera target appears in the upper-middle of the screen and the
  lower third is left for subtitles.
Times are in seconds from the start of the shot. Angles are in degrees. Colors are "#rrggbb".

Polish built in: real HDRI lighting (Poly Haven, CC0), ray-traced reflections, depth of field,
bloom on glowing things, a soft vignette, beveled edges and material presets.
"""
import math
import os
import random

import bpy
from mathutils import Vector

FPS = 30
_state = {"seconds": 4.0, "assets": {}, "camera": None, "target": None, "hdri_dir": "",
          "blur": 2.8}


# ---------------------------------------------------------------- helpers
def _frame(t: float) -> int:
    return int(round(t * FPS)) + 1


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb(color) -> tuple:
    """'#rrggbb' or (r, g, b) in 0-1 (sRGB) -> linear RGBA for Blender."""
    if isinstance(color, str):
        c = color.lstrip("#")
        color = tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (_lin(color[0]), _lin(color[1]), _lin(color[2]), 1.0)


def _link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _vec(v) -> Vector:
    if hasattr(v, "matrix_world"):
        return v.matrix_world.translation.copy()
    return Vector(v)


def _set_interp(kind: str):
    bpy.context.preferences.edit.keyframe_new_interpolation_type = kind


def _set(node, name: str, value):
    """Sets a node setting whether it's a property or (in newer Blender) an input socket."""
    if hasattr(node, name.lower().replace(" ", "_")):
        try:
            setattr(node, name.lower().replace(" ", "_"), value)
            return
        except Exception:
            pass
    for key in (name, name.title(), name.capitalize()):
        if key in node.inputs:
            node.inputs[key].default_value = value
            return


# ---------------------------------------------------------------- materials
PRESETS = {
    "matte": {"rough": 0.65}, "clay": {"rough": 0.9}, "rubber": {"rough": 0.75},
    "plastic": {"rough": 0.28, "coat": 0.4}, "glossy": {"rough": 0.12, "coat": 1.0},
    "metal": {"metal": 1.0, "rough": 0.3}, "chrome": {"metal": 1.0, "rough": 0.04},
    "gold": {"metal": 1.0, "rough": 0.18, "color": "#e0a526"},
    "glass": {"rough": 0.02, "transmission": 1.0}, "neon": {"glow": 8.0, "rough": 0.3},
}


def material(color="#cccccc", preset: str = "plastic", glow: float = 0.0, alpha: float = 1.0,
             metal: float | None = None, rough: float | None = None, name: str = "Mat"):
    """A surface. preset: matte clay rubber plastic glossy metal chrome gold glass neon.
    glow > 0 makes it emit light (bloom makes it shine); alpha < 1 is see-through."""
    p = dict(PRESETS.get(preset, {}))
    color = p.pop("color", color) if preset == "gold" and color == "#cccccc" else color
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = rgb(color)
    bsdf.inputs["Metallic"].default_value = metal if metal is not None else p.get("metal", 0.0)
    bsdf.inputs["Roughness"].default_value = rough if rough is not None else p.get("rough", 0.4)
    bsdf.inputs["Alpha"].default_value = alpha
    if p.get("coat"):
        bsdf.inputs["Coat Weight"].default_value = p["coat"]
    if p.get("transmission"):
        bsdf.inputs["Transmission Weight"].default_value = 1.0
        bsdf.inputs["IOR"].default_value = 1.45
        try:
            mat.use_raytrace_refraction = True
        except AttributeError:
            pass
    glow = glow or p.get("glow", 0.0)
    if glow:
        bsdf.inputs["Emission Color"].default_value = rgb(color)
        bsdf.inputs["Emission Strength"].default_value = glow
    return mat


def _apply(obj, mat):
    if obj.data is not None and hasattr(obj.data, "materials"):
        obj.data.materials.clear()
        obj.data.materials.append(mat)


# ---------------------------------------------------------------- scene
VISIBLE_HDRI = {"sky", "sunset", "dramatic", "landscape", "night"}
# Real places (ground-level photo environments); add_floor() projects the photo's own ground.
PLACES = {"meadow", "forest", "mountains", "desert", "snow", "city", "city_night", "warehouse", "workshop",
          "hangar", "airfield", "airbase", "helipad", "harbor", "coast", "desert_road", "rocky_desert",
          "ruins", "castle", "muddy_forest", "farmland", "dam", "railway", "industrial", "valley", "lakeside",
          "sunset_hill", "night_field", "interior"}
PLACE_EYE_HEIGHT = 2.2  # height the place photo was taken from, in scene units (sets the ground's scale)


def _hdri_path(name: str) -> str:
    """This video's own places (picked per video, passed in the job) first, then the preset files."""
    return _state.get("places", {}).get(name) or os.path.join(_state["hdri_dir"], f"{name}.hdr")


def _is_place(env: str) -> bool:
    return env in PLACES or env in _state.get("places", {})


def setup(env: str = "studio", tint: str | None = None, floor: bool | None = None,
          seconds: float | None = None, hdri_turn: float = 0.0):
    """Starts the shot.
    env: a real place for 3D models: meadow | forest | mountains | desert | snow | city | city_night
         | warehouse | workshop | hangar | airfield | lakeside | sunset_hill | night_field | interior
         (call add_floor(z) at the model's base to stand it on the place's real ground);
         or studio (tinted backdrop) | dark (near-black backdrop, diagrams only) | white (bright clean
         backdrop) | sky (real blue sky with clouds) | sunset | dramatic (fiery sky) | landscape | night
         | space (stars).
    tint: backdrop color for studio/dark/white (defaults suit each). hdri_turn: rotate the environment."""
    seconds = seconds or _state["seconds"]
    _state["seconds"] = seconds
    _state["place"] = None
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, max(_frame(seconds) - 1, 1)
    scene.render.fps = FPS
    world = bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    out = nt.nodes["World Output"]
    for n in list(nt.nodes):
        if n != out:
            nt.nodes.remove(n)

    def hdri(name: str, strength: float):
        path = _hdri_path(name)
        coords = nt.nodes.new("ShaderNodeTexCoord")
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.inputs["Rotation"].default_value = (0, 0, math.radians(hdri_turn))
        tex = nt.nodes.new("ShaderNodeTexEnvironment")
        if os.path.exists(path):
            tex.image = bpy.data.images.load(path, check_existing=True)
        nt.links.new(coords.outputs["Generated"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        bg = nt.nodes.new("ShaderNodeBackground")
        bg.inputs["Strength"].default_value = strength
        nt.links.new(tex.outputs["Color"], bg.inputs["Color"])
        return bg

    def backdrop(bottom, top):
        """What the camera sees behind everything: a soft vertical gradient."""
        coords = nt.nodes.new("ShaderNodeTexCoord")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        nt.links.new(coords.outputs["Window"], sep.inputs[0])
        nt.links.new(sep.outputs["Y"], ramp.inputs["Fac"])
        ramp.color_ramp.elements[0].color = rgb(bottom)
        ramp.color_ramp.elements[1].color = rgb(top)
        bg = nt.nodes.new("ShaderNodeBackground")
        nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
        return bg

    def camera_sees(light_bg, cam_bg):
        path = nt.nodes.new("ShaderNodeLightPath")
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
        nt.links.new(light_bg.outputs["Background"], mix.inputs[1])
        nt.links.new(cam_bg.outputs["Background"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])

    def scale_color(hexcol, k):
        c = hexcol.lstrip("#")
        return "#" + "".join(f"{min(255, int(int(c[i:i + 2], 16) * k)):02x}" for i in (0, 2, 4))

    if env in ("studio", "dark", "white"):
        default = {"studio": "#283041", "dark": "#0b0d12", "white": "#e4e7ec"}[env]
        base = tint or default
        top, bottom = (scale_color(base, 1.25), scale_color(base, 0.55)) if env != "white" else \
            (scale_color(base, 1.03), scale_color(base, 0.86))
        camera_sees(hdri("studio", 0.8 if env == "white" else 0.6), backdrop(bottom, top))
        light("#fff4e8", at=(-5, -6, 7), energy=650, size=5)              # key light
        light("#dfe8ff", at=(6, -4, 2), energy=180, size=6)               # soft fill
        light(scale_color(base, 3.0) if env != "white" else "#ffffff", at=(2, 7, 6), energy=900, size=4)  # rim
        if floor is None:
            floor = env == "white"  # studio/dark: clean backdrop; call add_floor() when things rest on the ground
        if floor:
            add_floor(-2.5, scale_color(base, 0.45) if env != "white" else scale_color(base, 0.92))
    elif _is_place(env):
        nt.links.new(hdri(env, 1.0).outputs["Background"], out.inputs["Surface"])
        _state["place"] = {"name": env, "turn": hdri_turn}
        for attr, value in (("sun_threshold", 4.0), ("use_sun_shadow", True), ("sun_angle", math.radians(2))):
            try:  # Eevee turns the photo's sun into a real shadow-casting light
                setattr(world, attr, value)
            except (AttributeError, TypeError):
                pass
    elif env in VISIBLE_HDRI:
        nt.links.new(hdri(env, 1.0).outputs["Background"], out.inputs["Surface"])
        sun_data = bpy.data.lights.new("Sun", "SUN")
        sun_data.energy = {"sky": 3.0, "landscape": 3.0, "sunset": 1.5, "dramatic": 1.5, "night": 0.2}[env]
        sun_data.color = (1.0, 0.9, 0.78) if env in ("sunset", "dramatic") else (1.0, 0.96, 0.9)
        sun_data.angle = math.radians(1.5)
        sun = _link(bpy.data.objects.new("Sun", sun_data))
        sun.rotation_euler = (math.radians(50), 0, math.radians(200))
        if env in ("sky", "sunset"):
            shape("plane", size=8000, at=(0, 0, -80), color="#041726", preset="glossy").name = "Ocean"
    elif env == "space":
        coords = nt.nodes.new("ShaderNodeTexCoord")
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 700
        noise.inputs["Detail"].default_value = 0
        stars = nt.nodes.new("ShaderNodeValToRGB")
        stars.color_ramp.elements[0].position = 0.80
        stars.color_ramp.elements[1].position = 0.83
        nebula = nt.nodes.new("ShaderNodeTexNoise")
        nebula.inputs["Scale"].default_value = 1.5
        neb_ramp = nt.nodes.new("ShaderNodeValToRGB")
        neb_ramp.color_ramp.elements[0].color = rgb("#000000")
        neb_ramp.color_ramp.elements[1].color = rgb(tint or "#1a1440")
        add = nt.nodes.new("ShaderNodeMix")
        add.data_type, add.blend_type = "RGBA", "ADD"
        add.inputs[0].default_value = 1.0
        for src, dst in ((coords.outputs["Generated"], noise.inputs["Vector"]),
                         (noise.outputs["Fac"], stars.inputs["Fac"]),
                         (coords.outputs["Generated"], nebula.inputs["Vector"]),
                         (nebula.outputs["Fac"], neb_ramp.inputs["Fac"]),
                         (stars.outputs["Color"], add.inputs[6]), (neb_ramp.outputs["Color"], add.inputs[7])):
            nt.links.new(src, dst)
        bg = nt.nodes.new("ShaderNodeBackground")
        bg.inputs["Strength"].default_value = 1.3
        nt.links.new(add.outputs[2], bg.inputs["Color"])
        nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
        sun_data = bpy.data.lights.new("Sun", "SUN")
        sun_data.energy = 4.0
        sun = _link(bpy.data.objects.new("Sun", sun_data))
        sun.rotation_euler = (math.radians(60), 0, math.radians(140))
    else:
        raise ValueError(f"unknown env {env!r}")
    camera("static")
    return scene


def light(color="#ffffff", at=(0, -6, 6), energy: float = 500, size: float = 3.0, aim=(0, 0, 0)):
    """Soft area light aimed at `aim`. Colored rim lights behind a subject look great."""
    data = bpy.data.lights.new("Light", "AREA")
    data.energy = energy
    data.color = rgb(color)[:3]
    data.size = size
    obj = _link(bpy.data.objects.new("Light", data))
    obj.location = at
    obj.rotation_euler = (Vector(aim) - Vector(at)).to_track_quat("-Z", "Y").to_euler()
    obj.visible_glossy = False  # light the scene without showing up as rectangles in reflections
    return obj


def _sky_irradiance(img) -> tuple:
    """How brightly the place photo's sky lights an upward-facing white surface (per RGB channel), so
    the projected ground can be lit by the scene and still match the photo exactly where unshadowed."""
    import numpy as np
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)[::4, ::4, :3]  # rows go bottom (nadir) to top (zenith)
    rows, cols = px.shape[:2]
    elev = (np.arange(rows) + 0.5) / rows * math.pi - math.pi / 2
    up = elev > 0
    weight = (np.sin(elev) * np.cos(elev))[up][:, None, None]
    k = (px[up] * weight).sum(axis=(0, 1)) * (math.pi / rows) * (2 * math.pi / cols) / math.pi
    return tuple(max(float(v), 1e-4) for v in k) + (1.0,)


def _place_ground(z: float):
    """The place photo's own ground, projected onto a big disc at height z. It catches the model's
    shadows and fades into the photo's horizon."""
    place = _state["place"]
    img = bpy.data.images.load(_hdri_path(place["name"]), check_existing=True)
    radius = 160.0
    bpy.ops.mesh.primitive_circle_add(vertices=96, radius=radius, fill_type="NGON", location=(0, 0, z))
    ground = bpy.context.active_object
    ground.name = "Ground"
    mat = bpy.data.materials.new("Ground")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    n = nt.nodes.new
    geo = n("ShaderNodeNewGeometry")
    rel = n("ShaderNodeVectorMath")
    rel.operation = "SUBTRACT"
    rel.inputs[1].default_value = (0, 0, z + PLACE_EYE_HEIGHT)
    norm = n("ShaderNodeVectorMath")
    norm.operation = "NORMALIZE"
    mapping = n("ShaderNodeMapping")
    mapping.inputs["Rotation"].default_value = (0, 0, math.radians(place["turn"]))
    tex = n("ShaderNodeTexEnvironment")
    tex.image = img
    lit = n("ShaderNodeBsdfDiffuse")
    lit.inputs["Color"].default_value = (1, 1, 1, 1)
    to_rgb = n("ShaderNodeShaderToRGB")
    div = n("ShaderNodeMix")
    div.data_type, div.blend_type = "RGBA", "DIVIDE"
    div.inputs[0].default_value = 1.0
    div.inputs[7].default_value = _sky_irradiance(img)
    clamp = n("ShaderNodeMix")  # never brighter than the photo: only shadows darken it
    clamp.data_type, clamp.blend_type = "RGBA", "DARKEN"
    clamp.inputs[0].default_value = 1.0
    clamp.inputs[7].default_value = (1, 1, 1, 1)
    mul = n("ShaderNodeMix")
    mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
    mul.inputs[0].default_value = 1.0
    emit = n("ShaderNodeEmission")
    coords = n("ShaderNodeTexCoord")
    dist = n("ShaderNodeVectorMath")
    dist.operation = "LENGTH"
    fade = n("ShaderNodeMapRange")
    fade.inputs["From Min"].default_value = radius * 0.08
    fade.inputs["From Max"].default_value = radius * 0.4
    fade.inputs["To Min"].default_value = 1.0
    fade.inputs["To Max"].default_value = 0.0
    clear = n("ShaderNodeBsdfTransparent")
    mix = n("ShaderNodeMixShader")
    out = n("ShaderNodeOutputMaterial")
    link = nt.links.new
    link(geo.outputs["Position"], rel.inputs[0])
    link(rel.outputs["Vector"], norm.inputs[0])
    link(norm.outputs["Vector"], mapping.inputs["Vector"])
    link(mapping.outputs["Vector"], tex.inputs["Vector"])
    link(lit.outputs["BSDF"], to_rgb.inputs["Shader"])
    link(to_rgb.outputs["Color"], div.inputs[6])
    link(div.outputs[2], clamp.inputs[6])
    link(tex.outputs["Color"], mul.inputs[6])
    link(clamp.outputs[2], mul.inputs[7])
    link(mul.outputs[2], emit.inputs["Color"])
    link(coords.outputs["Object"], dist.inputs[0])
    link(dist.outputs["Value"], fade.inputs["Value"])
    link(fade.outputs["Result"], mix.inputs["Fac"])
    link(clear.outputs["BSDF"], mix.inputs[1])
    link(emit.outputs["Emission"], mix.inputs[2])
    link(mix.outputs["Shader"], out.inputs["Surface"])
    ground.data.materials.append(mat)
    ground.visible_shadow = False
    return ground


def _lowest_point(obj) -> float:
    bpy.context.view_layer.update()
    parts = [obj, *obj.children_recursive]
    zs = [(o.matrix_world @ Vector(c)).z for o in parts if o.type == "MESH" and not o.hide_render
          for c in o.bound_box]
    return min(zs) if zs else obj.matrix_world.translation.z


def add_floor(z: float = -2.5, color: str = "#1d2330", radius: float = 9.0, under=None):
    """The ground things rest on; under=obj puts it exactly at that object's base. In a real place
    env it is the place's own ground (grass, road, concrete...) with the model's shadow on it.
    Otherwise a soft reflective floor that fades into the backdrop (an 'infinite studio')."""
    if under is not None:
        z = _lowest_point(under)
    if _state.get("place"):
        return _place_ground(z)
    f = shape("plane", size=radius * 4, at=(0, 2, z), color=color, preset="glossy", bevel=False)
    mat = f.data.materials[0]
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.3
    coords = nt.nodes.new("ShaderNodeTexCoord")
    length = nt.nodes.new("ShaderNodeVectorMath")
    length.operation = "LENGTH"
    fade = nt.nodes.new("ShaderNodeMapRange")
    fade.inputs["From Min"].default_value = radius * 0.35
    fade.inputs["From Max"].default_value = radius
    fade.inputs["To Min"].default_value = 1.0
    fade.inputs["To Max"].default_value = 0.0
    nt.links.new(coords.outputs["Object"], length.inputs[0])
    nt.links.new(length.outputs["Value"], fade.inputs["Value"])
    nt.links.new(fade.outputs["Result"], bsdf.inputs["Alpha"])
    f.name = "Floor"
    return f


# ---------------------------------------------------------------- objects
def _merge(meshes: list) -> list:
    """Joins a model's many parts into one mesh object. Models from Sketchfab can have hundreds of
    parts, and copying such a model dozens of times makes Blender crawl. Rigged / shape-keyed models
    are left alone."""
    if len(meshes) < 2:
        return meshes
    if any(m.type == "ARMATURE" for o in meshes for m in o.modifiers) or any(o.data.shape_keys for o in meshes):
        return meshes
    try:
        for o in meshes:
            if o.data.users > 1:
                o.data = o.data.copy()
        with bpy.context.temp_override(active_object=meshes[0], object=meshes[0], selected_objects=meshes,
                                       selected_editable_objects=meshes):
            bpy.ops.object.join()
        bpy.context.view_layer.update()
        return [meshes[0]]
    except Exception as exc:  # joining is only an optimization
        print(f"[sb] could not merge model parts: {exc}")
        return meshes


def model(name: str, size: float = 5.0, at=(0, 0, 0), rot=(0, 0, 0)):
    """A downloaded 3D model (see the asset list), scaled so its largest side is `size` and centered
    on `at`. Use rot=(x, y, z) degrees to turn it, e.g. so it faces the camera (-Y)."""
    path = _state["assets"].get(name)
    if not path:
        raise KeyError(f"no 3D model called {name!r}; available: {sorted(_state['assets'])}")
    before = set(bpy.context.scene.objects)
    if path.lower().endswith((".glb", ".gltf")):
        bpy.ops.import_scene.gltf(filepath=path)
    elif path.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        bpy.ops.wm.obj_import(filepath=path)
    new = [o for o in bpy.context.scene.objects if o not in before]
    root = _link(bpy.data.objects.new(f"model_{name}", None))
    for o in new:
        if o.parent is None:
            o.parent = root
    bpy.context.view_layer.update()
    meshes = [o for o in new if o.type == "MESH"]
    # Many downloaded models include a flat "shadow plane" under them: hide paper-thin, wide parts.
    if len(meshes) > 1:
        dims = [o.dimensions * max(o.matrix_world.to_scale()) for o in meshes]
        biggest = max(max(d) for d in dims)
        for o, d in zip(meshes, dims):
            if min(d) < biggest * 0.004 and sorted(d)[1] > biggest * 0.3:
                o.hide_render = True
        meshes = [o for o in meshes if not o.hide_render]
    meshes = _merge(meshes)
    current = [o for o in bpy.context.scene.objects if o not in before and o is not root]
    if len(meshes) == 1 and not any(o.type == "ARMATURE" for o in current):
        # Merged: drop the file's empty node hierarchy (often hundreds of objects) so copies stay cheap.
        mesh = meshes[0]
        world = mesh.matrix_world.copy()
        mesh.parent = root
        mesh.matrix_world = world
        for o in current:
            if o is not mesh:
                bpy.data.objects.remove(o, do_unlink=True)
        bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    pivot = _link(bpy.data.objects.new(f"{name}", None))  # rotate around the model's center
    if pts:
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        k = size / max(max(hi - lo), 1e-6)
        root.parent = pivot
        root.scale = (k, k, k)
        root.location = -((lo + hi) / 2) * k
    else:
        root.parent = pivot
    pivot.location = at
    pivot.rotation_euler = [math.radians(a) for a in rot]
    return pivot


def shape(kind: str, size: float = 1.0, at=(0, 0, 0), rot=(0, 0, 0), color="#cccccc",
          preset: str = "plastic", glow: float = 0.0, alpha: float = 1.0, scale=None, bevel: bool = True,
          metal: float | None = None, rough: float | None = None):
    """Primitive: cube | sphere | cylinder | cone | torus | plane | ico | disc | capsule.
    Cubes/cylinders get softly beveled edges that catch the light."""
    ops = {"cube": bpy.ops.mesh.primitive_cube_add, "sphere": bpy.ops.mesh.primitive_uv_sphere_add,
           "cylinder": bpy.ops.mesh.primitive_cylinder_add, "cone": bpy.ops.mesh.primitive_cone_add,
           "torus": bpy.ops.mesh.primitive_torus_add, "plane": bpy.ops.mesh.primitive_plane_add,
           "ico": bpy.ops.mesh.primitive_ico_sphere_add, "disc": bpy.ops.mesh.primitive_circle_add,
           "capsule": bpy.ops.mesh.primitive_uv_sphere_add}
    if kind == "disc":
        ops[kind](radius=size / 2, fill_type="NGON", location=at, vertices=64)
    elif kind in ("cube", "plane"):
        ops[kind](size=size, location=at)
    elif kind == "torus":
        ops[kind](major_radius=size / 2, minor_radius=size / 45, location=at, major_segments=96)
    elif kind in ("sphere", "capsule"):
        ops[kind](radius=size / 2, location=at, segments=64, ring_count=32)
    elif kind == "cylinder":
        ops[kind](radius=size / 2, location=at, vertices=64)
    elif kind == "cone":
        ops[kind](radius1=size / 2, radius2=0, depth=size, location=at, vertices=64)
    else:
        ops[kind](radius=size / 2, location=at, subdivisions=3)
    obj = bpy.context.active_object
    if kind == "capsule":
        obj.scale = (1, 1, 2)
    if kind not in ("plane", "disc", "ico"):
        try:
            bpy.ops.object.shade_smooth_by_angle()
        except Exception:
            bpy.ops.object.shade_smooth()
    if bevel and kind in ("cube", "cylinder", "cone"):
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = size * 0.04
        mod.segments = 4
        mod.limit_method = "ANGLE"
    obj.rotation_euler = [math.radians(a) for a in rot]
    if scale is not None:
        obj.scale = scale if isinstance(scale, (tuple, list)) else (scale,) * 3
    _apply(obj, material(color, preset, glow, alpha, metal, rough))
    return obj


_FONT = {}


def _font(bold=True):
    key = "b" if bold else "r"
    if key not in _FONT:
        _FONT[key] = None
        for path in (("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf") if bold else
                     ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf")):
            try:
                _FONT[key] = bpy.data.fonts.load(path)
                break
            except Exception:
                continue
    return _FONT[key]


def text(content: str, at=(0, 0, 0), size: float = 1.0, color="#ffffff", glow: float = 0.0,
         preset: str = "glossy", depth: float = 0.12, rot=(90, 0, 0), bold: bool = True):
    """Solid, beveled 3D text facing the camera. glow > 0 for neon-style text."""
    curve = bpy.data.curves.new("text", "FONT")
    curve.body = content
    curve.align_x, curve.align_y = "CENTER", "CENTER"
    curve.size = size
    curve.extrude = depth * size
    curve.bevel_depth = 0.02 * size
    curve.bevel_resolution = 3
    if _font(bold):
        curve.font = _font(bold)
    obj = _link(bpy.data.objects.new("text", curve))
    obj.location = at
    obj.rotation_euler = [math.radians(a) for a in rot]
    obj.data.materials.append(material(color, preset, glow=glow))
    return obj


def card(words: str, at=(0, 0, 0), width: float = 4.0, height: float = 1.1, color="#1e2533",
         text_color="#ffffff", accent=None, size: float | None = None):
    """A rounded infographic card with text: great for labels, stats, list items, comparisons.
    accent: a glowing color strip on the left edge."""
    body = shape("cube", size=1, at=at, color=color, preset="glossy", bevel=False)
    body.scale = (width, 0.24, height)
    mod = body.modifiers.new("Round", "BEVEL")
    mod.width = min(width, height) * 0.25
    mod.segments = 8
    mod.affect = "EDGES"
    parts = [body]
    t = text(words, at=Vector(at) + Vector((0.15 if accent else 0, -0.16, 0)),
             size=size or height * 0.42, color=text_color, depth=0.04)
    parts.append(t)
    if accent:
        strip = shape("cube", size=1, at=Vector(at) + Vector((-width / 2 + 0.12, -0.14, 0)), color=accent,
                      glow=4, bevel=False)
        strip.scale = (0.06, 0.02, height * 0.32)
        parts.append(strip)
    root = group(*parts)
    return root


def line(points, color="#ffffff", width: float = 0.05, glow: float = 3.0):
    """A glowing tube through the given points (paths, graphs, connections)."""
    curve = bpy.data.curves.new("line", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = width
    curve.bevel_resolution = 4
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for p, xyz in zip(spline.points, points):
        p.co = (*_vec(xyz), 1)
    obj = _link(bpy.data.objects.new("line", curve))
    obj.data.materials.append(material(color, "plastic", glow=glow))
    return obj


def draw_on(line_obj, start: float = 0.0, end: float = 1.0):
    """Animates a line() being drawn from its first point to its last."""
    _set_interp("BEZIER")
    line_obj.data.bevel_factor_end = 0.0
    line_obj.data.keyframe_insert("bevel_factor_end", frame=_frame(start))
    line_obj.data.bevel_factor_end = 1.0
    line_obj.data.keyframe_insert("bevel_factor_end", frame=_frame(end))
    return line_obj


def arrow(start, end, color="#ffd400", width: float = 0.07, glow: float = 3.0):
    """Arrow from start to end (points or objects)."""
    a, b = _vec(start), _vec(end)
    d = b - a
    head = min(0.5, d.length * 0.3)
    shaft = line([a, b - d.normalized() * head], color, width, glow)
    cone = shape("cone", size=width * 6, at=b - d.normalized() * head / 2, color=color, glow=glow, bevel=False)
    cone.scale = (1, 1, head / (width * 6))
    cone.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    return group(shaft, cone)


def label(target, words: str, offset=(1.8, 0, 1.4), color="#ffffff", size: float = 0.42):
    """Documentary callout: glowing dot on the target, a thin leader line, and a text label."""
    p = _vec(target)
    q = p + Vector(offset)
    side = 1 if offset[0] >= 0 else -1
    ln = line([p, q, q + Vector((0.35 * side, 0, 0))], color, 0.018, 3.0)
    dot = shape("sphere", size=0.14, at=p, color=color, glow=5)
    t = text(words, at=q + Vector((side * (0.45 + len(words) * size * 0.3), 0, 0)), size=size, color=color,
             depth=0.03)
    return group(ln, dot, t)


def group(*objs):
    """Parents objects to one handle so they can be moved/animated together."""
    root = _link(bpy.data.objects.new("group", None))
    for o in objs:
        o.parent = root
    return root


def copy(obj, at=None):
    """A copy of an object (and its children)."""
    new = obj.copy()
    if obj.data is not None:
        new.data = obj.data
    _link(new)
    for child in obj.children:
        c = copy(child)
        c.parent = new
    if at is not None:
        new.location = at
    return new


def scatter(obj, count: int = 20, area=(10, 10, 4), center=(0, 0, 0), seed: int = 1, size_jitter=0.3):
    """Copies of `obj` spread randomly through a box (crowds, cells, coins...)."""
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        c = copy(obj, at=Vector(center) + Vector([rng.uniform(-a / 2, a / 2) for a in area]))
        k = 1 + rng.uniform(-size_jitter, size_jitter)
        c.scale = [s * k for s in obj.scale]
        c.rotation_euler = (0, 0, rng.uniform(0, math.tau))
        out.append(c)
    obj.hide_render = True
    return out


def dust(count: int = 70, color="#ffffff", area=(16, 14, 18), center=(0, 6, 1), size: float = 0.05,
         seed: int = 3):
    """Floating dust motes / bokeh sparkles drifting slowly: adds depth and atmosphere."""
    rng = random.Random(seed)
    mat = material(color, "plastic", glow=6)
    objs = []
    _set_interp("LINEAR")
    for _ in range(count):
        p = Vector(center) + Vector([rng.uniform(-a / 2, a / 2) for a in area])
        bpy.ops.mesh.primitive_ico_sphere_add(radius=size * rng.uniform(0.5, 1.5), location=p, subdivisions=1)
        o = bpy.context.active_object
        _apply(o, mat)
        drift = Vector((rng.uniform(-0.4, 0.4), rng.uniform(-0.2, 0.2), rng.uniform(0.2, 0.7)))
        o.keyframe_insert("location", frame=1)
        o.location = p + drift * _state["seconds"] / 4
        o.keyframe_insert("location", frame=_frame(_state["seconds"]))
        objs.append(o)
    _set_interp("BEZIER")
    return group(*objs)


def rings(at=(0, 0, 0), color="#00e5ff", count: int = 4, radius: float = 4.0, period: float = 1.6,
          facing: str = "up", start: float = 0.0, end: float | None = None):
    """Expanding, fading glowing rings: radar pings, sound waves, signals, ripples.
    facing='up' lies flat (seen from above/side), facing='camera' faces the viewer."""
    end = end if end is not None else _state["seconds"]
    objs = []
    for i in range(count):
        mat = material(color, "plastic", glow=5.0, name="ring")
        r = shape("torus", size=2.0, at=at, rot=(90, 0, 0) if facing == "camera" else (0, 0, 0))
        _apply(r, mat)
        alpha = mat.node_tree.nodes["Principled BSDF"].inputs["Alpha"]
        t = start + i * period / count
        _set_interp("LINEAR")
        while t < end:  # grow at full brightness, fade only over the last 40%
            for tt, k, a in ((t, 0.05, 1.0), (t + 0.6 * period, 0.05 + 0.6 * (radius - 0.05), 1.0),
                             (t + period, radius, 0.0)):
                r.scale = (k, k, k)
                r.keyframe_insert("scale", frame=_frame(tt))
                alpha.default_value = a
                alpha.keyframe_insert("default_value", frame=_frame(tt))
            t += period
        _set_interp("BEZIER")
        objs.append(r)
    return group(*objs)


# ---------------------------------------------------------------- animation
def key(obj, prop: str, keys, smooth: bool = True):
    """Keyframes in seconds. prop: location | rotation (degrees) | scale | hide.
    Example: key(car, "location", [(0, (-6, 0, 0)), (3, (6, 0, 0))])"""
    _set_interp("BEZIER" if smooth else "LINEAR")
    for t, value in keys:
        if prop == "rotation":
            obj.rotation_euler = [math.radians(a) for a in value]
            obj.keyframe_insert("rotation_euler", frame=_frame(t))
        elif prop == "scale":
            obj.scale = value if isinstance(value, (tuple, list)) else (value,) * 3
            obj.keyframe_insert("scale", frame=_frame(t))
        elif prop == "hide":
            obj.hide_render = bool(value)
            obj.keyframe_insert("hide_render", frame=_frame(t))
        else:
            setattr(obj, prop, value)
            obj.keyframe_insert(prop, frame=_frame(t))
    _set_interp("BEZIER")


def move(obj, to, start: float = 0.0, end: float | None = None):
    end = end if end is not None else _state["seconds"]
    key(obj, "location", [(start, tuple(obj.location)), (end, tuple(to))])


def appear(obj, at: float = 0.0, dur: float = 0.45):
    """Pops in: grows from nothing, overshoots slightly, settles."""
    full = Vector(obj.scale)
    key(obj, "scale", [(max(at - 0.01, 0), (0.001,) * 3), (at, (0.001,) * 3),
                       (at + dur * 0.7, tuple(full * 1.1)), (at + dur, tuple(full))])


def disappear(obj, at: float, dur: float = 0.4):
    full = Vector(obj.scale)
    key(obj, "scale", [(at, tuple(full)), (at + dur * 0.3, tuple(full * 1.08)), (at + dur, (0.001,) * 3)])


def slide_in(obj, from_offset=(0, 0, -3), at: float = 0.0, dur: float = 0.6):
    """Slides into its current position from an offset."""
    end = Vector(obj.location)
    key(obj, "location", [(at, tuple(end + Vector(from_offset))), (at + dur, tuple(end))])


def spin(obj, turns: float = 1.0, axis: str = "z", start: float = 0.0, end: float | None = None):
    end = end if end is not None else _state["seconds"]
    i = "xyz".index(axis)
    r0 = [math.degrees(a) for a in obj.rotation_euler]
    r1 = list(r0)
    r1[i] += 360 * turns
    key(obj, "rotation", [(start, r0), (end, r1)], smooth=False)


def bob(obj, amount: float = 0.15, period: float = 2.5):
    """Gentle floating/hovering motion for the whole shot."""
    base = Vector(obj.location)
    keys, t = [], 0.0
    while t <= _state["seconds"] + 0.01:
        keys.append((t, tuple(base + Vector((0, 0, amount * math.sin(2 * math.pi * t / period))))))
        t += period / 8
    key(obj, "location", keys)


def pulse(obj, amount: float = 0.12, period: float = 1.0):
    """Rhythmic grow/shrink (heartbeats, vibration, emphasis)."""
    base = Vector(obj.scale)
    keys, t = [], 0.0
    while t <= _state["seconds"] + 0.01:
        k = 1 + amount * math.sin(2 * math.pi * t / period)
        keys.append((t, tuple(base * k)))
        t += period / 8
    key(obj, "scale", keys)


def count_up(obj_at=(0, 0, 0), start_value: float = 0, end_value: float = 100, prefix: str = "",
             suffix: str = "", start: float = 0.0, end: float = 2.0, size: float = 1.6, color="#ffffff",
             decimals: int = 0, glow: float = 0.0):
    """A number that counts up (shown as swapped text objects)."""
    steps = max(int((end - start) * 12), 2)
    made = []
    for i in range(steps + 1):
        v = start_value + (end_value - start_value) * (i / steps) ** 0.6
        s = f"{prefix}{v:,.{decimals}f}{suffix}"
        t = text(s, at=obj_at, size=size, color=color, glow=glow)
        t0 = start + (end - start) * i / steps
        t1 = start + (end - start) * (i + 1) / steps if i < steps else _state["seconds"] + 1
        key(t, "hide", [(0, True), (t0, False), (t1, True)] if t0 > 0 else [(0, False), (t1, True)])
        made.append(t)
    return made


# ---------------------------------------------------------------- camera
def _ensure_camera():
    if _state["camera"] is None:
        data = bpy.data.cameras.new("Cam")
        data.sensor_fit = "HORIZONTAL"
        data.sensor_width = 36
        data.clip_end = 20000
        data.shift_y = -0.13  # the target sits in the upper half; the lower third is left for subtitles
        cam = _link(bpy.data.objects.new("Cam", data))
        target = _link(bpy.data.objects.new("Target", None))
        con = cam.constraints.new("TRACK_TO")
        con.target = target
        con.track_axis, con.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
        bpy.context.scene.camera = cam
        _state["camera"], _state["target"] = cam, target
    return _state["camera"], _state["target"]


def camera(kind: str = "static", target=(0, 0, 0), distance: float = 12.0, height: float = 1.5,
           angle: float = 0.0, sweep: float = 50.0, lens: float = 50.0, start: float = 0.0,
           end: float | None = None, follow=None, blur: float | None = 2.8, shake: float = 0.0):
    """Camera move. kind: static | orbit | push | pull | pan | crane | top | drift.
    angle: where the camera sits around the target (0 = front, 90 = right side, 180 = behind).
    follow: an object to keep aiming at while it moves. blur: depth-of-field f-stop (lower = blurrier
    background; None = everything sharp). shake: subtle handheld wobble (0.02-0.06)."""
    cam, tgt = _ensure_camera()
    cam.data.lens = lens
    end = end if end is not None else _state["seconds"]
    for fc in (cam, tgt):
        fc.animation_data_clear()
    con = cam.constraints[0]
    con.target = follow if follow is not None else tgt
    centre = _vec(target)
    tgt.location = centre
    cam.data.dof.use_dof = bool(blur)
    if blur:
        cam.data.dof.focus_object = follow if follow is not None else tgt
        cam.data.dof.aperture_fstop = blur
    rng = random.Random(7)
    keys, t = [], 0.0
    while t <= _state["seconds"] + 0.01:
        p = min(max((t - start) / max(end - start, 1e-6), 0.0), 1.0)
        p = p * p * (3 - 2 * p)
        a, d, h = angle, distance, height
        offset = Vector((0, 0, 0))
        if kind == "orbit":
            a = angle + sweep * (p - 0.5)
        elif kind == "push":
            d = distance * (1.35 - 0.35 * p)
        elif kind == "pull":
            d = distance * (1.0 + 0.35 * p)
        elif kind == "pan":
            offset = Vector((distance * 0.3 * (p - 0.5), 0, 0))
        elif kind == "crane":
            h = height + distance * 0.45 * (p - 0.3)
        elif kind == "drift":
            a = angle + 10 * (p - 0.5)
            d = distance * (1.05 - 0.08 * p)
        if kind == "top":
            r = math.radians(angle + sweep * 0.3 * (p - 0.5))
            pos = centre + Vector((0.01 * math.sin(r), -0.01 * math.cos(r) - 0.3, d))
        else:
            r = math.radians(a)
            pos = centre + offset + Vector((d * math.sin(r), -d * math.cos(r), h))
        if shake:
            pos += Vector([rng.uniform(-1, 1) * shake * d / 12 for _ in range(3)])
        keys.append((t, tuple(pos)))
        t += 0.2
    key(cam, "location", keys, smooth=not shake)
    return cam


# ---------------------------------------------------------------- render quality (called by the runner)
def finish(mode: str):
    """Render settings: Eevee with ray tracing, bloom, vignette, AgX color."""
    scene = bpy.context.scene
    r = scene.render
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            r.engine = engine
            break
        except TypeError:
            continue
    ee = scene.eevee
    ee.taa_render_samples = 8 if mode == "preview" else 32
    for attr, value in (("use_raytracing", True), ("use_shadows", True), ("use_fast_gi", True),
                        ("use_gtao", True), ("shadow_ray_count", 2), ("shadow_step_count", 8)):
        try:
            setattr(ee, attr, value)
        except Exception:
            pass
    try:
        ee.ray_tracing_method = "SCREEN"
    except Exception:
        pass
    try:
        r.use_motion_blur = mode == "final"
    except AttributeError:
        pass
    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass
    if _state["camera"] is None:
        camera("static")
    try:
        _compositor(scene)
    except Exception as exc:  # polish is optional: never fail a render over it
        print("[sb] compositor skipped:", exc)


def _compositor(scene):
    scene.use_nodes = True
    scene.render.use_compositing = True
    tree = scene.node_tree
    for n in list(tree.nodes):
        tree.nodes.remove(n)
    rl = tree.nodes.new("CompositorNodeRLayers")
    glare = tree.nodes.new("CompositorNodeGlare")
    for kind in ("BLOOM", "FOG_GLOW"):
        try:
            glare.glare_type = kind
            break
        except TypeError:
            continue
    _set(glare, "quality", "HIGH")
    _set(glare, "Threshold", 1.0)
    _set(glare, "Strength", 0.6)
    _set(glare, "Size", 7 if hasattr(glare, "size") else 0.6)
    _set(glare, "mix", -0.2)
    mask = tree.nodes.new("CompositorNodeEllipseMask")
    # The mask must cover nearly the whole frame (a soft vignette). Careful: node.width is the node's
    # UI width, not the mask's - setting that left a tiny default mask that dimmed everything else.
    for attr, value in (("mask_width", 1.15), ("mask_height", 1.05)):
        if hasattr(mask, attr):
            setattr(mask, attr, value)
    if "Size" in mask.inputs:
        try:
            mask.inputs["Size"].default_value = (1.15, 1.05)
        except (TypeError, ValueError):
            pass
    blur = tree.nodes.new("CompositorNodeBlur")
    _set(blur, "use_relative", True)
    _set(blur, "factor_x", 25.0)
    _set(blur, "factor_y", 25.0)
    _set(blur, "filter_type", "FAST_GAUSS")
    if "Size" in blur.inputs:  # Blender 4.5+ reads the blur size (pixels) from this input instead
        r = scene.render
        w = r.resolution_x * r.resolution_percentage / 100
        h = r.resolution_y * r.resolution_percentage / 100
        try:
            blur.inputs["Size"].default_value = (w * 0.25, h * 0.25)
        except (TypeError, ValueError):
            blur.inputs["Size"].default_value = max(w, h) * 0.25
    mix = tree.nodes.new("CompositorNodeMixRGB")
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 0.35
    comp = tree.nodes.new("CompositorNodeComposite")
    tree.links.new(rl.outputs["Image"], glare.inputs["Image"])
    tree.links.new(mask.outputs["Mask"], blur.inputs["Image"])
    tree.links.new(glare.outputs["Image"], mix.inputs[1])
    tree.links.new(blur.outputs["Image"], mix.inputs[2])
    tree.links.new(mix.outputs["Image"], comp.inputs["Image"])
