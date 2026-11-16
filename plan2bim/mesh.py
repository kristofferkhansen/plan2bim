"""Closed solids for display and for checking.

All prisms of one material in one category on one storey are joined into one solid. The join removes
the faces between adjoining prisms, so what is left is the surface a person can see.
"""
from __future__ import annotations

from dataclasses import dataclass

import manifold3d as m3
import numpy as np
import shapely
from shapely.geometry import Polygon

from .model import Element, Model, Piece

CATEGORY = {"wall": "walls", "slab": "floors", "roof": "roofs", "door": "doors", "window": "windows",
            "stair": "stairs", "column": "columns", "railing": "balconies"}
CREASE = 25.0       # degrees: where the surface turns by more over an edge, the edge is drawn and the shading breaks


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
            cut = Polygon(rings[0].exterior, [hole.exterior for hole in rings[1:]])
            if cut.is_valid:
                out.append(cut)
            else:
                # a ring of the cut can touch itself, for example where two walls meet at one point:
                # GEOS takes no union or difference of such a polygon
                valid = shapely.make_valid(cut)
                out += [g for g in getattr(valid, "geoms", [valid]) if g.geom_type == "Polygon"]
    return out


def _edges(body: m3.Manifold):
    """The edges of the surface, each with its two triangles: the corners `v`, the triangles `t`, and for each edge
    its two corners `e`, its two triangles `a` and `b`, the cosine of the angle by which the surface turns there,
    and the width of the narrower of the two triangles measured from the edge."""
    v, t = triangles(body)
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
    across = np.minimum(size[a], size[b]) / np.maximum(length, 1e-9)
    return v, t, e, a, b, np.einsum("ij,ij->i", normal[a], normal[b]), across


def feature_edges(body: m3.Manifold, degrees: float = CREASE, fine: float = 12.0) -> np.ndarray:
    """The edges where the surface turns by more than `degrees`, as rows of x1 y1 z1 x2 y2 z2 in mm.

    An edge beside a strip of surface narrower than `fine` is left out. Such a strip is a step of a few
    millimetres, for example where the outlines of two storeys do not agree exactly. A line there
    would show a feature that is smaller than the line itself.
    """
    v, _t, e, _a, _b, turn, across = _edges(body)
    e = e[(turn < np.cos(np.radians(degrees))) & (across >= fine)]
    return np.hstack([v[e[:, 0]], v[e[:, 1]]])


def contour_edges(body: m3.Manifold, degrees: float = CREASE, fine: float = 12.0) -> np.ndarray:
    """The edges where the surface turns, but by no more than `degrees`: the facets of a round surface. As rows
    of x1 y1 z1 x2 y2 z2 in mm, and then the third corner of each of the two triangles at the edge.

    A viewer shades such a surface as round and draws no line on it. It draws one of these edges only where it is
    the outline of the surface as the eye sees it: where the two third corners are on the same side of the edge.
    """
    v, t, e, a, b, turn, across = _edges(body)
    keep = (turn >= np.cos(np.radians(degrees))) & (turn < np.cos(np.radians(0.5))) & (across >= fine)
    e, a, b = e[keep], a[keep], b[keep]
    third = [t[k].astype(np.int64).sum(axis=1) - e.sum(axis=1) for k in (a, b)]
    return np.hstack([v[e[:, 0]], v[e[:, 1]], v[third[0]], v[third[1]]])


def joined(solids: list[Solid], category: str, material: str) -> m3.Manifold | None:
    """The solids of one kind on all storeys as one solid: no edge is left where two storeys meet flush."""
    bodies = [s.body for s in solids if s.category == category and s.material == material]
    return m3.Manifold.batch_boolean(bodies, m3.OpType.Add) if bodies else None
