"""Closed solids for display and for checking.

All prisms of one material in one category on one storey are joined into one solid. The join removes
the faces between adjoining prisms, so what is left is the surface a person can see.
"""
from __future__ import annotations

from dataclasses import dataclass

import manifold3d as m3
import numpy as np
from shapely.geometry import Polygon

from .model import Element, Model, Piece

CATEGORY = {"wall": "walls", "slab": "floors", "roof": "roofs", "door": "doors", "window": "windows",
            "stair": "stairs", "column": "columns", "railing": "balconies"}


@dataclass
class Solid:
    level: str
    category: str       # what a viewer switches on and off
    material: str
    body: m3.Manifold
    prisms: int
    volume_in: float    # mm3, sum of the prisms; larger than the volume of `body` when prisms overlap


def category(e: Element, p: Piece) -> str:
    return "balconies" if p.material == "balcony" else CATEGORY[e.kind]


def prism(p: Piece) -> m3.Manifold:
    rings = [np.asarray(p.poly.exterior.coords)[:-1], *(np.asarray(r.coords)[:-1] for r in p.poly.interiors)]
    section = m3.CrossSection(rings, m3.FillRule.EvenOdd)
    return m3.Manifold.extrude(section, p.z1 - p.z0).translate((0.0, 0.0, p.z0))


def solids(model: Model) -> list[Solid]:
    groups: dict[tuple[str, str, str], list[Piece]] = {}
    for e in model.elements:
        for p in e.pieces:
            groups.setdefault((e.level, category(e, p), p.material), []).append(p)
    out = []
    for (level, cat, material), ps in groups.items():
        body = m3.Manifold.batch_boolean([prism(p) for p in ps], m3.OpType.Add)
        if body.status() != m3.Error.NoError:
            raise ValueError(f"storey {level}, {cat}, {material}: the solid is not closed ({body.status()})")
        out.append(Solid(level, cat, material, body, len(ps), sum(p.poly.area * (p.z1 - p.z0) for p in ps)))
    return out


def triangles(body: m3.Manifold) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (n, 3) in mm as float64 and triangles (m, 3) as vertex indices."""
    mesh = body.to_mesh64()
    return np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3], np.asarray(mesh.tri_verts, dtype=np.uint32)


def section(body: m3.Manifold, z: float) -> list[Polygon]:
    """What is solid where the horizontal plane at `z` cuts the solid: one polygon, with its holes, for each
    part of the cut that stands apart. A part that stands in a hole of another part is a polygon of its own."""
    out = []
    for part in body.slice(z).decompose():
        rings = sorted((Polygon(ring) for ring in part.to_polygons() if len(ring) >= 3), key=lambda p: -p.area)
        if rings:
            out.append(Polygon(rings[0].exterior, [hole.exterior for hole in rings[1:]]))
    return out


def feature_edges(body: m3.Manifold, degrees: float = 25.0, fine: float = 12.0) -> np.ndarray:
    """The edges where the surface turns by more than `degrees`, as rows of x1 y1 z1 x2 y2 z2 in mm.

    An edge beside a strip of surface narrower than `fine` is left out. Such a strip is a step of a few
    millimetres, for example where the outlines of two storeys do not agree exactly. A line there
    would show a feature that is smaller than the line itself.
    """
    v, t = triangles(body)
    if len(t) == 0:
        return np.zeros((0, 6))
    normal = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    size = np.linalg.norm(normal, axis=1)               # twice the area of each triangle
    normal /= np.maximum(size, 1e-12)[:, None]
    ends = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]).astype(np.int64)
    owner = np.tile(np.arange(len(t)), 3)
    ends.sort(axis=1)
    key = ends[:, 0] * (len(v) + 1) + ends[:, 1]
    order = np.argsort(key, kind="stable")
    key, ends, owner = key[order], ends[order], owner[order]
    pair = np.flatnonzero(key[1:] == key[:-1])          # a closed surface: each edge has two triangles
    a, b, e = owner[pair], owner[pair + 1], ends[pair]
    length = np.linalg.norm(v[e[:, 1]] - v[e[:, 0]], axis=1)
    across = np.minimum(size[a], size[b]) / np.maximum(length, 1e-9)    # the narrower triangle, measured from the edge
    sharp = (np.einsum("ij,ij->i", normal[a], normal[b]) < np.cos(np.radians(degrees))) & (across >= fine)
    e = e[sharp]
    return np.hstack([v[e[:, 0]], v[e[:, 1]]])


def joined(solids: list[Solid], category: str, material: str) -> m3.Manifold | None:
    """The solids of one kind on all storeys as one solid: no edge is left where two storeys meet flush."""
    bodies = [s.body for s in solids if s.category == category and s.material == material]
    return m3.Manifold.batch_boolean(bodies, m3.OpType.Add) if bodies else None
