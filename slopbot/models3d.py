"""Finds and downloads free 3D models from Sketchfab.

Searching is open; downloading needs a free Sketchfab account's API token (SKETCHFAB_API_TOKEN).
Only licenses that allow use in monetized videos are accepted (CC BY, CC BY-SA, CC0);
NonCommercial and NoDerivatives models are skipped.
"""
import io
import json
import zipfile
from pathlib import Path

import requests

from .config import ASSETS, require_key

API = "https://api.sketchfab.com/v3"
OK_LICENSES = {"CC Attribution", "CC Attribution-ShareAlike", "CC0 Public Domain"}
CACHE = ASSETS / "models"


MIN_FACES = 4_000    # below this a model is usually a low-poly toy
MAX_FACES = 600_000  # above this a scene gets too heavy to render in time


def search(query: str, limit: int = 6, pages: int = 2) -> list[dict]:
    """Usable models for a query, most liked first. Detailed models (MIN_FACES+) come before low-poly ones."""
    url, params, found = f"{API}/search", {"type": "models", "q": query, "downloadable": "true",
                                           "count": 24, "sort_by": "-likeCount"}, []
    for _ in range(pages):
        resp = requests.get(url, timeout=30, params=params)
        resp.raise_for_status()
        data = resp.json()
        for m in data.get("results", []):
            license_ = (m.get("license") or {}).get("label", "")
            faces = m.get("faceCount") or 0
            if license_ not in OK_LICENSES or faces > MAX_FACES:
                continue
            images = sorted(m.get("thumbnails", {}).get("images", []), key=lambda i: abs(i["width"] - 640))
            found.append({"uid": m["uid"], "name": m["name"], "license": license_,
                          "author": m["user"]["displayName"], "url": f"https://sketchfab.com/models/{m['uid']}",
                          "thumb": images[0]["url"] if images else "", "faces": faces})
        if not data.get("next"):
            break
        url, params = data["next"], None
    found.sort(key=lambda m: m["faces"] < MIN_FACES)  # stable: keeps like-order within each group
    return found[:limit]


def download(model: dict) -> Path:
    """Downloads a model (cached in assets/models/<uid>/) and returns the .glb/.gltf file."""
    folder = CACHE / model["uid"]
    existing = sorted(folder.glob("**/*.glb")) + sorted(folder.glob("**/*.gltf"))
    if existing:
        return existing[0]
    token = require_key("SKETCHFAB_API_TOKEN", "free: sign up at https://sketchfab.com, then "
                        "Settings > Password & API > API Token")
    resp = requests.get(f"{API}/models/{model['uid']}/download", timeout=30,
                        headers={"Authorization": f"Token {token}"})
    if resp.status_code == 401:
        raise SystemExit("Sketchfab rejected SKETCHFAB_API_TOKEN - check it in .env")
    resp.raise_for_status()
    links = resp.json()
    folder.mkdir(parents=True, exist_ok=True)
    if "glb" in links:
        out = folder / "model.glb"
        out.write_bytes(requests.get(links["glb"]["url"], timeout=600).content)
    else:
        archive = requests.get(links["gltf"]["url"], timeout=600).content
        zipfile.ZipFile(io.BytesIO(archive)).extractall(folder)
        out = (sorted(folder.glob("**/*.gltf")) or sorted(folder.glob("**/*.glb")))[0]
    (folder / "credit.json").write_text(json.dumps({
        "name": model["name"], "author": model["author"], "license": model["license"], "url": model["url"]},
        indent=2), encoding="utf-8")
    return out


def credit(model_file: Path) -> str:
    info_file = next((p / "credit.json" for p in [model_file.parent, *model_file.parents]
                      if (p / "credit.json").exists()), None)
    if not info_file:
        return ""
    info = json.loads(info_file.read_text(encoding="utf-8"))
    url = f"https://sketchfab.com/models/{info_file.parent.name}"  # the search API's viewerUrl has a bad slug
    return f"3D model \"{info['name']}\" by {info['author']} ({info['license']}) {url}"
