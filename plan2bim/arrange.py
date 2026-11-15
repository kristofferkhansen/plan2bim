"""Planar arrangement of one storey: every region that the drawn lines and the room outlines enclose.

A face of this arrangement is the unit the model is built from. A wall is a
face that is closed by drawn lines and is not a room. Nothing is offset,
joined or cleaned up. The one repair is `close_gaps`: a line that stops a few
millimetres short of the line it was drawn towards is joined to it, because
such a gap would let a wall leak into the room beside it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize

from .level import Level
from .project import Tolerances


@dataclass
class Face:
    id: int
    poly: Polygon
    area: float
    room: int = -1              # index of the room polygon that contains it, or -1
    inr: float = 0.0            # radius of the largest inscribed circle: half the width of a wall
    length: float = 0.0         # long side of the minimum rotated rectangle
    thick: float = 0.0          # short side
    axis: tuple[float, float] = (1.0, 0.0)   # unit vector along the long side
    perim: float = 0.0
    black: float = 0.0          # length of boundary that lies on drawn lines
    black_run: float = 0.0      # longest unbroken stretch of drawn boundary
    open_run: float = 0.0       # longest unbroken stretch of boundary that is not drawn
    in_gross: bool = False
    nbrs: dict[int, tuple[float, float]] = field(default_factory=dict)   # face id -> (shared length, of which drawn)
    free: float = 0.0           # boundary length with no face on the other side (the outside)
    kind: str = ""              # set by classify
    why: str = ""
    solid: BaseGeometry | None = None   # wall mass of this face when a window was cut out of it

    @property
    def cov(self) -> float:
        return self.black / self.perim if self.perim else 0.0

    @property
    def gap(self) -> float:
        return self.perim - self.black

    @property
    def mass(self) -> BaseGeometry:
        return self.poly if self.solid is None else self.solid


@dataclass
class Arrangement:
    faces: list[Face]
    tree: shapely.STRtree

    def at(self, x: float, y: float) -> Face | None:
        pt = shapely.Point(x, y)
        hits = [int(i) for i in self.tree.query(pt, predicate="intersects")]
        return self.faces[min(hits)] if hits else None

    def near(self, geom: BaseGeometry) -> list[Face]:
        return [self.faces[int(i)] for i in self.tree.query(geom, predicate="intersects")]


def obb(poly: BaseGeometry) -> tuple[float, float, tuple[float, float]]:
    """Length, thickness and long-axis direction of the minimum rotated rectangle."""
    c = np.array(poly.minimum_rotated_rectangle.exterior.coords)
    e1, e2 = c[1] - c[0], c[2] - c[1]
    l1, l2 = float(np.hypot(*e1)), float(np.hypot(*e2))
    if l1 >= l2:
        return l1, l2, (float(e1[0] / l1), float(e1[1] / l1)) if l1 else (1.0, 0.0)
    return l2, l1, (float(e2[0] / l2), float(e2[1] / l2))


def _longest_runs(flags: list[bool], lengths: list[float]) -> tuple[float, float]:
    """Longest cyclic run of True and of False, by summed length."""
    n = len(flags)
    if n == 0:
        return 0.0, 0.0
    if all(flags):
        return sum(lengths), 0.0
    if not any(flags):
        return 0.0, sum(lengths)
    start = next(i for i in range(n) if flags[i] != flags[i - 1])       # a run starts here
    best = {True: 0.0, False: 0.0}
    cur, acc = flags[start], 0.0
    for j in range(n):
        i = (start + j) % n
        if flags[i] != cur:
            best[cur] = max(best[cur], acc)
            cur, acc = flags[i], 0.0
        acc += lengths[i]
    best[cur] = max(best[cur], acc)
    return best[True], best[False]


def close_gaps(black: np.ndarray, tol: Tolerances) -> list[BaseGeometry]:
    """Joins for loose line ends: a drawn line that ends within `tol.close` of another line without touching it.

    A join runs from the loose end to the nearest point of the other line and a hair past it, so that the
    two are sure to cross. A stub that overshoots a junction is also a loose end; it is left alone because
    the line it would be joined to is the one it already crosses (gap about equal to its own length).
    """
    parts = shapely.get_parts(shapely.union_all(black, grid_size=tol.grid))
    n = len(parts)
    ends = np.concatenate([shapely.get_coordinates(shapely.get_point(parts, 0)),
                           shapely.get_coordinates(shapely.get_point(parts, -1))])
    _, inverse, counts = np.unique(np.round(ends / tol.grid).astype(np.int64), axis=0, return_inverse=True,
                                   return_counts=True)
    loose = np.flatnonzero(counts[inverse.ravel()] == 1)
    tree = shapely.STRtree(parts)
    lengths = shapely.length(parts)
    joins: list[BaseGeometry] = []
    for e in loose.tolist():
        own = e % n
        p = shapely.points(ends[e])
        best, best_d = -1, tol.close
        for j in tree.query(p, predicate="dwithin", distance=tol.close).tolist():
            if j != own:
                d = shapely.distance(parts[j], p)
                if d < best_d:
                    best, best_d = j, d
        if best < 0 or best_d >= 0.5 * lengths[own]:
            continue
        q = shapely.get_coordinates(shapely.shortest_line(p, parts[best]))[1]
        v = q - ends[e]
        norm = float(np.hypot(v[0], v[1]))
        if norm > 0:
            joins.append(shapely.linestrings([ends[e], q + v / norm * 5.0 * tol.grid]))
    return joins


def build(level: Level, tol: Tolerances) -> Arrangement:
    grid = tol.grid
    black = shapely.linestrings(level.segs.reshape(-1, 2, 2))
    joins = np.array(close_gaps(black, tol), dtype=object)
    others = [r.poly.boundary for r in level.rooms] + list(level.separators)
    noded = shapely.union_all(np.concatenate([black, joins, np.array(others, dtype=object)]), grid_size=grid)
    polys = list(polygonize(noded))
    black_tree = shapely.STRtree(np.concatenate([black[~level.seg_dashed], joins]))
    room_tree = shapely.STRtree([r.poly for r in level.rooms])
    gross = level.gross_all
    shapely.prepare(gross)

    faces: list[Face] = []
    rings: list[list[list[tuple]]] = []          # per face, per ring: edge keys in order
    edge_faces: dict[tuple, list[int]] = {}
    for p in polys:
        if p.area <= 0:
            continue
        f = Face(len(faces), p, p.area)
        hits = [int(i) for i in room_tree.query(p.representative_point(), predicate="intersects")]
        if hits:
            f.room = min(hits, key=lambda i: level.rooms[i].poly.area)
        f.inr = shapely.maximum_inscribed_circle(p, tolerance=0.25).length
        f.length, f.thick, f.axis = obb(p)
        f.in_gross = gross.intersection(p).area > 0.5 * p.area
        mine = []
        for ring in (p.exterior, *p.interiors):
            c = np.round(np.asarray(ring.coords) / grid).astype(np.int64).tolist()
            keys = [(a[0], a[1], b[0], b[1]) if (a[0], a[1]) <= (b[0], b[1]) else (b[0], b[1], a[0], a[1])
                    for a, b in pairwise(c)]
            for key in keys:
                edge_faces.setdefault(key, []).append(f.id)
            mine.append(keys)
        rings.append(mine)
        faces.append(f)

    # an edge is drawn when its midpoint lies on the drawn linework. A dashed line shows what is overhead:
    # it parts the faces it crosses, and closes none.
    keys = list(edge_faces)
    k = np.array(keys, dtype=float) * grid
    mids = shapely.points((k[:, 0] + k[:, 2]) / 2, (k[:, 1] + k[:, 3]) / 2)
    drawn = np.zeros(len(keys), dtype=bool)
    drawn[black_tree.query(mids, predicate="dwithin", distance=tol.on_line)[0]] = True
    length = np.hypot(k[:, 0] - k[:, 2], k[:, 1] - k[:, 3])
    info = {key: (bool(d), float(ln)) for key, d, ln in zip(keys, drawn.tolist(), length.tolist(), strict=True)}

    for f, mine in zip(faces, rings, strict=True):
        for ring_keys in mine:
            flags = [info[key][0] for key in ring_keys]
            lens = [info[key][1] for key in ring_keys]
            b, o = _longest_runs(flags, lens)
            f.black_run, f.open_run = max(f.black_run, b), max(f.open_run, o)
            for key, on, ln in zip(ring_keys, flags, lens, strict=True):
                f.perim += ln
                f.black += ln if on else 0.0
                ids = edge_faces[key]
                if len(ids) == 1:
                    f.free += ln
                else:
                    other = ids[0] if ids[1] == f.id else ids[1]
                    if other != f.id:
                        s, sb = f.nbrs.get(other, (0.0, 0.0))
                        f.nbrs[other] = (s + ln, sb + (ln if on else 0.0))
    return Arrangement(faces, shapely.STRtree([f.poly for f in faces]))
