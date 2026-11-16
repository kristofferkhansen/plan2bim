"""One storey in the model frame: linework as segments, rooms as polygons, outlines."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from . import source
from .errors import InputError
from .project import Project
from .register import Block, block_index

CHORD_SAG = 0.25        # largest distance between an arc and its polyline, mm


@dataclass
class Room:
    id: int
    poly: Polygon
    number: str
    name: str
    wing: str
    handles: list[str]
    block: int
    notes: list[str] = field(default_factory=list)


@dataclass
class ArcM:
    """A drawn arc in the model frame (door swings, curved walls)."""
    cx: float
    cy: float
    r: float
    a0: float
    sweep: float

    def point(self, a: float) -> tuple[float, float]:
        return self.cx + self.r * math.cos(a), self.cy + self.r * math.sin(a)


@dataclass
class Level:
    key: str
    segs: np.ndarray                    # (n, 4) drawn linework as straight pieces, model frame
    seg_arc: np.ndarray                 # (n,) index into arcs for arc pieces, else -1
    seg_dashed: np.ndarray              # (n,) bool
    arcs: list[ArcM]
    rooms: list[Room]
    separators: list[LineString]        # room-layer lines that close no room: they divide open-plan rooms
    gross: list[Polygon]                # one per block
    inner: list[Polygon]
    blocks: list[Block]
    notes: list[str] = field(default_factory=list)

    @property
    def gross_all(self):
        return unary_union(self.gross)

    @property
    def inner_all(self):
        return unary_union(self.inner)


class _Circles:
    """Arcs on the same circle get the same polyline vertices, so curved faces drawn twice still coincide."""

    def __init__(self) -> None:
        self.circles: list[tuple[float, float, float]] = []
        self.angles: list[list[float]] = []
        self.tree: cKDTree | None = None

    def add(self, a: ArcM) -> int:
        same = (i for i, (cx, cy, r) in enumerate(self.circles)
                if abs(cx - a.cx) < 0.05 and abs(cy - a.cy) < 0.05 and abs(r - a.r) < 0.05)
        i = next(same, len(self.circles))
        if i == len(self.circles):
            self.circles.append((a.cx, a.cy, a.r))
            self.angles.append([])
        self.angles[i] += [a.a0 % math.tau, (a.a0 + a.sweep) % math.tau]
        return i

    def points(self, i: int, a: ArcM) -> list[tuple[float, float]]:
        cx, cy, r = self.circles[i]
        n = max(24, math.ceil(math.pi / math.acos(max(-1.0, 1.0 - CHORD_SAG / r))))
        step = math.tau / n
        lo, hi = (a.a0, a.a0 + a.sweep) if a.sweep >= 0 else (a.a0 + a.sweep, a.a0)
        inner = {round(k * step, 9) for k in range(math.floor(lo / step) + 1, math.ceil(hi / step))}
        for ang in self.angles[i]:                       # end points of the other arcs on this circle
            for turn in (-math.tau, 0.0, math.tau):
                if lo + 1e-7 < ang + turn < hi - 1e-7:
                    inner.add(round(ang + turn, 9))
        inner = sorted(x for x in inner if lo + 1e-7 < x < hi - 1e-7)
        mid = [(cx + r * math.cos(x), cy + r * math.sin(x)) for x in inner]
        p0, p1 = a.point(a.a0), a.point(a.a0 + a.sweep)
        return [p0, *(mid if a.sweep >= 0 else mid[::-1]), p1]


def _arc_m(p, q, bulge) -> ArcM:
    a = source.bulge_arc(p, q, bulge)
    return ArcM(a.cx, a.cy, a.r, a.a0, a.sweep)


def _ring_points(poly: source.Poly, t, circles: _Circles, grid: float) -> list[tuple[float, float]]:
    """Model-frame vertices of a polyline, arcs as polylines."""
    out: list[tuple[float, float]] = []
    for p, q, b in source.segments(poly):
        p, q = (p[0] + t[0], p[1] + t[1]), (q[0] + t[0], q[1] + t[1])
        if not out:
            out.append(p)
        if abs(b) > 1e-9:
            a = _arc_m(p, q, b)
            out.extend(circles.points(circles.add(a), a)[1:])
        else:
            out.append(q)
    return [(round(x / grid) * grid, round(y / grid) * grid) for x, y in out]


def _polygons(g) -> list[Polygon]:
    """The polygons of a geometry, also those of a collection in it."""
    return [g] if g.geom_type == "Polygon" else [p for part in getattr(g, "geoms", ()) for p in _polygons(part)]


def _clean_polygon(ring: list[tuple[float, float]]) -> tuple[list[Polygon], str]:
    """The polygons that a ring encloses, the largest first, and why they are not the ring itself."""
    if len(ring) < 3:
        return [], "fewer than 3 vertices"
    p = Polygon(ring)
    if p.is_valid and p.area > 0:
        return [p], ""
    parts = sorted((g for g in _polygons(shapely.make_valid(p)) if g.area > 0), key=lambda g: -g.area)
    return parts, "outline touches or crosses itself" if parts else "no area"


def build(project: Project, d: source.Drawing, blocks: list[Block]) -> Level:
    tol = project.tol
    grid = tol.grid
    notes: list[str] = list(d.notes)
    notes += [f"block {b.id}: {b.evidence['doubt']}" for b in blocks if "doubt" in b.evidence]

    def t_of(x: float, y: float) -> tuple[tuple[float, float], int]:
        i = block_index(blocks, x, y)
        return blocks[i].t, i

    # ---- linework: register every arc first, so that arcs on one circle can share vertices
    circles = _Circles()
    arc_list: list[ArcM] = []
    pending: list[tuple] = []
    for pl in d.lines:
        t, _ = t_of(pl.pts[0][0], pl.pts[0][1])
        dashed = pl.linetype.upper() not in ("BYLAYER", "CONTINUOUS", "BYBLOCK")
        for p, q, b in source.segments(pl):
            p, q = (p[0] + t[0], p[1] + t[1]), (q[0] + t[0], q[1] + t[1])
            if abs(b) > 1e-9:
                a = _arc_m(p, q, b)
                arc_list.append(a)
                pending.append((circles.add(a), a, len(arc_list) - 1, dashed))
            else:
                pending.append((None, (p, q), -1, dashed))
    room_polys = [pl for pl in d.rooms]
    for pl in room_polys + d.gross + d.inner:               # arcs in area outlines join the same registry
        t, _ = t_of(pl.pts[0][0], pl.pts[0][1])
        for p, q, b in source.segments(pl):
            if abs(b) > 1e-9:
                circles.add(_arc_m((p[0] + t[0], p[1] + t[1]), (q[0] + t[0], q[1] + t[1]), b))
    segs, seg_arc, seg_dashed = [], [], []
    for ci, item, ai, dashed in pending:
        pts = circles.points(ci, item) if ci is not None else list(item)
        for (x1, y1), (x2, y2) in pairwise(pts):
            s = (round(x1 / grid) * grid, round(y1 / grid) * grid, round(x2 / grid) * grid, round(y2 / grid) * grid)
            if s[0] != s[2] or s[1] != s[3]:
                segs.append(s)
                seg_arc.append(ai)
                seg_dashed.append(dashed)

    # ---- rooms
    rooms: list[Room] = []
    separators: list[LineString] = []
    for pl in d.rooms:
        t, bi = t_of(pl.pts[0][0], pl.pts[0][1])
        ring = _ring_points(pl, t, circles, grid)
        closes = pl.closed or (len(ring) >= 4 and math.dist(ring[0], ring[-1]) < 1.0)
        if len(pl.pts) < 3 or not closes:
            if len(ring) >= 2:
                separators.append(LineString(ring))
                if len(pl.pts) >= 3:
                    notes.append(f"room-layer polyline {pl.handle} with {len(pl.pts)} vertices is open: used as a separator")
            continue
        parts, why = _clean_polygon(ring)
        if not parts:
            notes.append(f"room polyline {pl.handle} dropped: {why}")
            continue
        poly = parts[0]
        dup = next((r for r in rooms if r.poly.symmetric_difference(poly).area < 1e-4 * poly.area), None)
        if dup is not None:
            dup.handles.append(pl.handle)
            notes.append(f"room polyline {pl.handle} is a duplicate of {dup.handles[0]}")
            continue
        if why:
            why += f"; kept the largest part ({poly.area / 1e6:.2f} of {sum(g.area for g in parts) / 1e6:.2f} m2)"
        r = Room(len(rooms), poly, "", "", "", [pl.handle], bi)
        if why:
            r.notes.append(why)
            notes.append(f"room polyline {pl.handle}: {why}")
        rooms.append(r)
    tree = shapely.STRtree([r.poly for r in rooms])
    numbers: dict[int, list[str]] = {}
    names: dict[int, list[str]] = {}
    for lb in d.labels:                             # a label block has number and name; a plain text has one of them
        t, _ = t_of(lb.x, lb.y)
        pt = Point(lb.x + t[0], lb.y + t[1])
        hits = [int(i) for i in tree.query(pt, predicate="intersects")]
        if not hits:
            notes.append(f"label {' '.join(filter(None, (lb.number, lb.name)))} lies in no room polygon")
            continue
        r = rooms[min(hits, key=lambda i: rooms[i].poly.area)]
        for said, known in ((lb.number, numbers), (lb.name, names)):
            if said and said not in known.setdefault(r.id, []):
                known[r.id].append(said)
        r.wing = r.wing or lb.wing
    for r in rooms:
        r.number, r.name = "/".join(numbers.get(r.id, [])), " / ".join(names.get(r.id, []))
        if not r.number:
            notes.append(f"room polyline {r.handles[0]} has no label ({r.poly.area / 1e6:.2f} m2)")
        elif "/" in r.number:
            notes.append(f"room polygon {r.handles[0]} holds several labels: {r.number}")

    # ---- outlines, pulled onto the linework they were traced over
    S = np.array(segs)
    ends = np.unique(np.vstack([S[:, :2], S[:, 2:]]), axis=0)
    end_tree = cKDTree(ends)
    seg_tree = shapely.STRtree(shapely.linestrings(S.reshape(-1, 2, 2)))

    def snap(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
        out = []
        for x, y in ring:
            dist, j = end_tree.query((x, y))
            if dist <= tol.snap:
                out.append((float(ends[j, 0]), float(ends[j, 1])))
                continue
            pt = Point(x, y)
            near = seg_tree.query(pt, predicate="dwithin", distance=tol.snap)
            if len(near):
                line = min((seg_tree.geometries[k] for k in near), key=lambda g: g.distance(pt))
                q = line.interpolate(line.project(pt))
                out.append((round(q.x / grid) * grid, round(q.y / grid) * grid))
            else:
                out.append((x, y))
        return out

    def outlines(polys: list[source.Poly], what: str) -> list[Polygon]:
        out: list[tuple[int, Polygon]] = []
        for pl in polys:
            t, bi = t_of(pl.pts[0][0], pl.pts[0][1])
            parts, why = _clean_polygon(snap(_ring_points(pl, t, circles, grid)))
            if not parts:
                notes.append(f"{what} outline {pl.handle} dropped: {why}")
                continue
            if why:                                 # one polyline round two buildings: each part is an outline
                parts = [g for g in parts if g.area >= 1e6] or parts[:1]
                notes.append(f"{what} outline {pl.handle}: {why}; read as {len(parts)} "
                             f"outline{'' if len(parts) == 1 else 's'} of {', '.join(f'{g.area / 1e6:.2f}' for g in parts)} m2")
            out += [(bi, g) for g in parts]
        return [p for _, p in sorted(out, key=lambda x: x[0])]

    gross = outlines(d.gross, "gross")
    if not gross:
        raise InputError(f"storey {d.key}: no gross outline of the storey encloses an area. "
                         + " ".join(n for n in notes if n.startswith("gross outline")))
    return Level(d.key, S, np.array(seg_arc), np.array(seg_dashed, dtype=bool), arc_list, rooms, separators,
                 gross, outlines(d.inner, "inner"), blocks, notes)
