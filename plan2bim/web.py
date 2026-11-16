"""One HTML file that shows the model in a browser.

The file holds the geometry, the viewer, its libraries (three.js, N8AO) and the IFC file of the model. It needs no server
and loads nothing from the network, so it can be opened from disk, mailed, or published as it is. A person
who has this file only can save the IFC file from it.
"""
from __future__ import annotations

import base64
import gzip
import html
import json
import re
from pathlib import Path

import numpy as np

from .level import Level
from .mesh import CREASE, Solid, contour_edges, feature_edges, joined, triangles
from .model import Model
from .project import Project

HERE = Path(__file__).parent
PLAN_CUT = 1200.0       # mm above the floor: where a plan cuts its storey
ORDER = ("walls", "floors", "roofs", "columns", "stairs", "balconies", "doors", "windows")
JOINED = (("walls", "wall"),)      # kinds whose solids continue from storey to storey


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


def _m(mm: float) -> str:
    return f"{mm / 1000.0:.2f}"


def assumptions(project: Project) -> list[str]:
    h = project.heights
    floors = ", ".join(f"{lv.name} {_m(lv.elevation)}" for lv in project.levels)
    return [
        "The drawings are plans. They hold no heights, sections or dimensions. Every height below is an assumption.",
        f"Floor levels in metres: {floors}. Top of the roof plate: {_m(h.roof)}.",
        f"Floor and roof plates are {h.plate:.0f} mm thick. Roofs are flat plates.",
        f"Doors, and openings without a door, are {h.door_head:.0f} mm high.",
        f"Windows stand from {h.window_sill:.0f} to {h.window_head:.0f} mm above the floor.",
        f"Balcony balustrades and stair guards are {h.balustrade:.0f} mm high.",
        "Stair treads are as drawn. The rise per step follows from the floor levels above.",
        "The ground around the building is not modelled.",
    ]


def payload(project: Project, levels: dict[str, Level], model: Model, solids: list[Solid]) -> dict:
    meshes = [(s, *triangles(s.body)) for s in solids]
    lo = np.min([v.min(axis=0) for _, v, _ in meshes], axis=0)
    hi = np.max([v.max(axis=0) for _, v, _ in meshes], axis=0)
    origin = np.array([round((lo[0] + hi[0]) / 2.0), round((lo[1] + hi[1]) / 2.0), 0.0])
    rank = {lv.key: i for i, lv in enumerate(project.levels)}
    meshes.sort(key=lambda m: (rank[m[0].level], ORDER.index(m[0].category), m[0].material))

    def lines(rows: np.ndarray) -> str:         # rows of points, three numbers each
        return _b64(((rows - np.tile(origin, rows.shape[1] // 3)) / 1000.0).astype("<f4"))

    # walls of all storeys as one solid: its edges show no line where two storeys meet flush
    whole = [{"category": cat, "e": lines(feature_edges(body))} for cat, material in JOINED
             if (body := joined(solids, cat, material)) is not None]
    kinds: dict[str, int] = {}
    for e in model.elements:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    counts = (f"{len(project.levels)} storeys \u00b7 {len(model.spaces)} rooms \u00b7 {kinds.get('door', 0)} doors \u00b7 "
              f"{kinds.get('window', 0)} windows \u00b7 {kinds.get('stair', 0)} stair flights")
    return {
        "name": project.name,
        "origin": origin.tolist(),
        "plan_cut": PLAN_CUT,
        "crease": CREASE,
        "counts": counts,
        "levels": [{"key": lv.key, "name": lv.name, "elevation": lv.elevation, "top": project.top_of(lv.key)}
                   for lv in project.levels],
        "solids": [{"level": s.level, "category": s.category, "material": s.material,
                    "v": _b64(((v - origin) / 1000.0).astype("<f4")), "i": _b64(t.astype("<u4")),
                    "e": lines(feature_edges(s.body)), "c": lines(contour_edges(s.body))}
                   for s, v, t in meshes],
        "whole": whole,
        "lines": [{"level": key, "p": _b64(((lv.segs - np.tile(origin[:2], 2)) / 1000.0).astype("<f4"))}
                  for key, lv in levels.items()],
        "rooms": [{"level": sp.level, "number": sp.number, "name": sp.name,
                   "x": round((pt.x - origin[0]) / 1000.0, 3), "y": round((pt.y - origin[1]) / 1000.0, 3)}
                  for sp in model.spaces for pt in [sp.poly.representative_point()]],
        "assumed": assumptions(project),
        "notes": [n.text for n in model.notes],
    }


def write(path: Path, project: Project, levels: dict[str, Level], model: Model, solids: list[Solid], ifc: Path) -> int:
    """Write the viewer and return its size in bytes. `ifc` is the IFC file of the model: the viewer holds it, packed."""
    page = (HERE / "viewer.html").read_text(encoding="utf-8")
    data = {**payload(project, levels, model, solids), "ifc": ifc.name}
    fill = {
        "__TITLE__": html.escape(project.name),
        "__THREE__": (HERE / "vendor" / "three.bundle.min.js").read_text(encoding="utf-8"),
        "__DATA__": json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/"),
        "__IFC__": base64.b64encode(gzip.compress(ifc.read_bytes(), mtime=0)).decode("ascii"),
    }
    parts = re.split(f"({'|'.join(fill)})", page)       # the marks of the page itself, not what looks like one in a filling
    for mark in fill:
        if parts.count(mark) != 1:
            raise ValueError(f"viewer.html must hold {mark} once")
    out = "".join(fill.get(part, part) for part in parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out, encoding="utf-8")
    return len(out.encode("utf-8"))
