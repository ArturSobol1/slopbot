"""Downloads a small set of free (CC0) HDRI environment maps from Poly Haven for 3D lighting."""
from pathlib import Path

import requests

from .config import ASSETS

HDRI_DIR = ASSETS / "hdri"
# env name used by the toolkit -> Poly Haven asset id
HDRIS = {
    "studio": "studio_small_09",
    "studio_warm": "brown_photostudio_02",
    "sky": "kloofendal_48d_partly_cloudy_puresky",
    "sunset": "kloppenheim_06_puresky",
    "dramatic": "the_sky_is_on_fire",
    "landscape": "lilienstein",
    "night": "satara_night",
}
# Real places to set 3D models in (ground-level photos; the toolkit projects their ground under the model).
PLACES = {
    "meadow": "meadow_2",
    "forest": "misty_pines",
    "mountains": "alps_field",
    "desert": "goegap",
    "snow": "snowy_park_01",
    "city": "wide_street_01",
    "city_night": "shanghai_bund",
    "warehouse": "empty_warehouse_01",
    "workshop": "machine_shop_02",
    "hangar": "rostock_laage_airport",
    "airfield": "derelict_airfield_01",       # a real runway (was a city-rooftop helipad)
    "airbase": "derelict_airfield_02",        # concrete apron in front of old hangars
    "helipad": "portland_landing_pad",        # rooftop helipad between city towers
    "harbor": "petit_port",                   # dock with cranes on the water
    "coast": "cape_hill",                     # rocky hilltop above the ocean
    "desert_road": "nowhere_road",            # straight empty highway through desert
    "rocky_desert": "rogland_sunset",         # arid rocky desert at sunset
    "ruins": "reinforced_concrete_01",        # bombed-out concrete ruins, rubble
    "castle": "monkstown_castle",             # medieval stone castle walls
    "muddy_forest": "muddy_autumn_forest",    # muddy autumn forest track
    "farmland": "ludwikowice_farmland",       # open rural fields, dry grass
    "dam": "dam_wall",                        # huge concrete dam wall and river
    "railway": "bloem_train_track_cloudy",    # railway track through fields
    "industrial": "freight_station",          # freight yard with cranes
    "valley": "neurathen_rock_castle",        # cliffs over a forested valley
    "lakeside": "lakeside",
    "sunset_hill": "spaichingen_hill",
    "night_field": "dikhololo_night",
    "interior": "lebombo",
}
HDRIS.update(PLACES)
HEADERS = {"User-Agent": "SlopBot/0.4 (personal video tool)"}


def ensure_hdris() -> Path:
    """Downloads any missing HDRIs (2k .hdr, ~6 MB each; places 4k, ~25 MB, so the ground under
    the models stays sharp). Returns the folder."""
    HDRI_DIR.mkdir(parents=True, exist_ok=True)
    for name, asset in HDRIS.items():
        out = HDRI_DIR / f"{name}.hdr"
        res = "4k" if name in PLACES else "2k"
        if out.exists() and (res == "2k" or out.stat().st_size > 12_000_000):
            continue
        print(f"[hdri] downloading {asset} {res} (Poly Haven, CC0)")
        files = requests.get(f"https://api.polyhaven.com/files/{asset}", headers=HEADERS, timeout=30).json()
        url = files["hdri"][res]["hdr"]["url"]
        out.write_bytes(requests.get(url, headers=HEADERS, timeout=300).content)
    return HDRI_DIR


# ---------------------------------------------------------------- per-video places
# Each video searches the whole Poly Haven HDRI library (CC0, ~1000 real places) for settings that fit
# its topic; Claude picks from thumbnails. The PLACES presets above are only a fallback.
CATALOG_FILE = HDRI_DIR / "catalog.json"
PICKED_DIR = HDRI_DIR / "picked"
SKIP_CATEGORIES = {"skies", "studio"}  # pure skies have no ground; studios are not places


def catalog() -> dict:
    """The Poly Haven HDRI list (name, tags, categories, downloads), cached for a week."""
    import json
    import time
    if CATALOG_FILE.exists() and time.time() - CATALOG_FILE.stat().st_mtime < 7 * 86400:
        return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    HDRI_DIR.mkdir(parents=True, exist_ok=True)
    data = requests.get("https://api.polyhaven.com/assets?t=hdris", headers=HEADERS, timeout=60).json()
    CATALOG_FILE.write_text(json.dumps(data), encoding="utf-8")
    return data


def search(words: str, limit: int = 8) -> list[dict]:
    """Best-matching places for a few keywords (tags count most), popular ones first on ties."""
    terms = [w for w in words.lower().replace(",", " ").split() if len(w) > 2]
    scored = []
    for asset_id, a in catalog().items():
        cats = set(a.get("categories", []))
        if cats & SKIP_CATEGORIES or "puresky" in asset_id:
            continue
        tags = [t.lower() for t in a.get("tags", [])]
        name = a.get("name", asset_id).lower()
        score = 0
        for term in terms:
            if term in tags:
                score += 3
            elif any(term in t for t in tags):
                score += 2
            if term in name:
                score += 2
            if any(term in c for c in cats):
                score += 1
        if score:
            scored.append((score, a.get("download_count", 0), asset_id, a))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return [{"id": asset_id, "name": a.get("name", asset_id), "tags": a.get("tags", []),
             "thumb": a.get("thumbnail_url", "").split("?")[0] + "?width=512&height=256&format=webp"}
            for _, _, asset_id, a in scored[:limit]]


def download_place(asset_id: str) -> Path:
    """Downloads one place at 4k (sharp ground under the models) into assets/hdri/picked/."""
    PICKED_DIR.mkdir(parents=True, exist_ok=True)
    out = PICKED_DIR / f"{asset_id}.hdr"
    if out.exists() and out.stat().st_size > 1_000_000:
        return out
    print(f"[places] downloading {asset_id} 4k (Poly Haven, CC0)")
    files = requests.get(f"https://api.polyhaven.com/files/{asset_id}", headers=HEADERS, timeout=30).json()
    tmp = out.with_suffix(".part")
    tmp.write_bytes(requests.get(files["hdri"]["4k"]["hdr"]["url"], headers=HEADERS, timeout=600).content)
    tmp.replace(out)
    return out
