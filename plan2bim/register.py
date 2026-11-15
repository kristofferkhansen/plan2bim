"""Put every building block of every storey into one frame.

Each storey is a separate drawing with its own origin. Worse, one drawing can
hold several blocks (separate gross outlines) that are not placed correctly
relative to each other. So the unit of registration is the block, and a block
is registered against a block of another storey that it stands on or under.

Evidence, strongest first:
1. large arcs with the same radius (a curved wall is unique in a building),
2. rooms with the same size (lift shafts and stairs run through all storeys),
   refined by exact coincidences of line end points, per axis,
3. exact coincidences of line end points alone.
A drawing that was traced over its neighbour storey shares thousands of end
points with it to within 0.001 mm, so a coincidence count is strong evidence.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Point, Polygon

from . import source
from .project import Project

CORE_NAMES = ("heis", "trapp", "sjakt")     # rooms that run through all storeys
ROOM_TOL = 60.0                             # same room on two storeys: size differs by less than this
FINE_WINDOW = 60.0                          # a coarse translation is trusted to this
MIN_EXACT = 8                               # coincident end points needed to accept a translation
CURVED_WALL_RADIUS = 1500.0                 # arcs larger than any door swing


@dataclass
class Block:
    level: str
    index: int
    outline: Polygon                        # gross outline, drawing coordinates
    t: tuple[float, float] = (0.0, 0.0)     # add to drawing coordinates to get model coordinates
    placed: bool = False
    evidence: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.level}/{self.index}"


def _ring(poly: source.Poly) -> list[tuple[float, float]]:
    return [(x, y) for x, y, _ in poly.pts]


def blocks_of(d: source.Drawing) -> list[Block]:
    polys = []
    for g in d.gross:
        ring = _ring(g)
        if len(ring) >= 3:
            p = Polygon(ring)
            polys.append(p if p.is_valid else p.buffer(0))
    polys.sort(key=lambda p: -p.area)
    return [Block(d.key, i, p) for i, p in enumerate(polys)]


def block_index(blocks: list[Block], x: float, y: float) -> int:
    """The block a drawing point belongs to: the one whose outline is nearest."""
    if len(blocks) == 1:
        return 0
    pt = Point(x, y)
    return min(range(len(blocks)), key=lambda i: blocks[i].outline.distance(pt))


@dataclass
class _Features:
    pts: np.ndarray                                             # unique line end points
    rooms: list[tuple[float, float, float, float, str]]         # bbox centre x, y, width, height, name
    arcs: list[tuple[float, float, float, float]]               # centre x, y, radius, |sweep| in degrees


def _features(d: source.Drawing, blocks: list[Block], index: int) -> _Features:
    pts, arcs, rooms = [], [], []
    for pl in d.lines:
        if block_index(blocks, pl.pts[0][0], pl.pts[0][1]) != index:
            continue
        pts.extend((x, y) for x, y, _ in pl.pts)
        for p, q, b in source.segments(pl):
            if abs(b) > 1e-9:
                a = source.bulge_arc(p, q, b)
                if a.r > CURVED_WALL_RADIUS:
                    arcs.append((a.cx, a.cy, a.r, abs(math.degrees(a.sweep))))
    names = _room_names(d)
    for pl in d.rooms:
        if not pl.closed or len(pl.pts) < 3 or block_index(blocks, pl.pts[0][0], pl.pts[0][1]) != index:
            continue
        a = np.array(_ring(pl))
        (x0, y0), (x1, y1) = a.min(0), a.max(0)
        rooms.append(((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, names.get(pl.handle, "")))
    p = np.unique(np.round(np.array(pts), 3), axis=0) if pts else np.zeros((0, 2))
    return _Features(p, rooms, arcs)


def _room_names(d: source.Drawing) -> dict[str, str]:
    """Room polyline handle -> room name, from the label that lies inside it."""
    polys = [(pl.handle, Polygon(_ring(pl))) for pl in d.rooms if pl.closed and len(pl.pts) >= 3]
    out: dict[str, str] = {}
    for lb in d.labels:
        pt = Point(lb.x, lb.y)
        for h, p in polys:
            if p.is_valid and p.contains(pt):
                out[h] = lb.name
                break
    return out


def _vote(d: np.ndarray, bin_: float = 0.1) -> list[tuple[float, int]]:
    """Histogram of 1D differences: [(value, count)] by count, descending. Two shifted grids, so no peak is split."""
    best: dict[float, int] = {}
    for shift in (0.0, bin_ / 2):
        k = np.floor((d + shift) / bin_).astype(np.int64)
        u, c = np.unique(k, return_counts=True)
        for ki, ci in zip(u.tolist(), c.tolist(), strict=True):
            v = round((ki + 0.5) * bin_ - shift, 3)
            best[v] = max(best.get(v, 0), ci)
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    out: list[tuple[float, int]] = []
    for v, c in ranked:                              # the same peak shows up in both grids: keep it once
        if all(abs(v - w) > bin_ for w, _ in out):
            out.append((v, c))
        if len(out) >= 6:
            break
    return out


def _exact_pairs(P: np.ndarray, Q: np.ndarray, t: tuple[float, float], tol: float = 0.02) -> np.ndarray:
    """Differences q - p for the end point pairs that the translation t brings into coincidence."""
    tree = cKDTree(Q)
    dist, j = tree.query(P + np.array(t), distance_upper_bound=tol)
    ok = np.isfinite(dist)
    return Q[j[ok]] - P[ok]


def _vote2d(P: np.ndarray, Q: np.ndarray, around: tuple[float, float] | None, window: float) -> list[tuple[tuple[float, float], int]]:
    """Translations ranked by the number of end point pairs they bring into coincidence."""
    keys: Counter[tuple[int, int]] = Counter()
    bin_ = 0.1
    if around is None:
        for i in range(0, len(P), 256):
            d = (Q[None, :, :] - P[i:i + 256, None, :]).reshape(-1, 2)
            k = np.floor(d / bin_ + 0.5).astype(np.int64)
            u, c = np.unique(k, axis=0, return_counts=True)
            m = c >= 2                                            # a true translation is hit many times per chunk
            keys.update({(int(a), int(b)): int(n) for (a, b), n in zip(u[m], c[m], strict=True)})
    else:
        tree = cKDTree(Q)
        for p, js in zip(P, tree.query_ball_point(P + np.array(around), window * math.sqrt(2)), strict=True):
            if js:
                k = np.floor((Q[js] - p) / bin_ + 0.5).astype(np.int64)
                keys.update(map(tuple, k.tolist()))
    out = []
    for (a, b), _ in keys.most_common(12):
        t0 = (a * bin_, b * bin_)
        pairs = _exact_pairs(P, Q, t0, tol=0.12)
        if len(pairs):
            t = tuple(np.median(pairs, axis=0))
            out.append(((float(t[0]), float(t[1])), len(_exact_pairs(P, Q, t))))
    out.sort(key=lambda x: -x[1])
    dedup: list[tuple[tuple[float, float], int]] = []
    for t, n in out:
        if all(math.hypot(t[0] - u[0], t[1] - u[1]) > 0.5 for u, _ in dedup):
            dedup.append((t, n))
    return dedup


def _refine_axis(p: np.ndarray, q: np.ndarray, coarse: float) -> tuple[float, list[tuple[float, int]]]:
    """Best translation along one axis near `coarse`, by coincidences of distinct coordinate values."""
    p, q = np.unique(np.round(p, 2)), np.unique(np.round(q, 2))
    lo = np.searchsorted(q, p + coarse - FINE_WINDOW)
    hi = np.searchsorted(q, p + coarse + FINE_WINDOW)
    d = np.concatenate([q[a:b] - v for v, a, b in zip(p, lo, hi, strict=True) if b > a]) if len(p) else np.zeros(0)
    if len(d) == 0:
        return coarse, []
    ranked = _vote(d)
    top = ranked[0][0]
    near = d[np.abs(d - top) < 0.06]
    return float(np.median(near)), ranked[:3]


def _coarse_from_rooms(a: _Features, b: _Features) -> tuple[tuple[float, float], float, int] | None:
    cand = []
    for xa, ya, wa, ha, na in a.rooms:
        for xb, yb, wb, hb, nb in b.rooms:
            if abs(wa - wb) <= ROOM_TOL and abs(ha - hb) <= ROOM_TOL:
                core = any(c in na.lower() for c in CORE_NAMES) and any(c in nb.lower() for c in CORE_NAMES)
                cand.append((xb - xa, yb - ya, 4.0 if core else 1.0))
    if not cand:
        return None
    c = np.array(cand)
    tree = cKDTree(c[:, :2])
    weight = np.array([c[j, 2].sum() for j in tree.query_ball_point(c[:, :2], ROOM_TOL)])
    i = int(weight.argmax())
    members = tree.query_ball_point(c[i, :2], ROOM_TOL)
    return (float(np.median(c[members, 0])), float(np.median(c[members, 1]))), float(weight[i]), len(members)


def _coarse_from_arcs(a: _Features, b: _Features) -> tuple[float, float] | None:
    cand = [(xb - xa, yb - ya) for xa, ya, ra, sa in a.arcs for xb, yb, rb, sb in b.arcs
            if abs(ra - rb) < 0.5 and abs(sa - sb) < 1.0]
    if len(cand) < 2:
        return None
    c = np.array(cand)
    tree = cKDTree(c)
    n = [len(j) for j in tree.query_ball_point(c, 0.05)]
    i = int(np.argmax(n))
    return (float(c[i, 0]), float(c[i, 1])) if n[i] >= 2 else None


def _match(a: _Features, b: _Features) -> tuple[tuple[float, float], dict] | None:
    """Translation that puts block `a` on block `b`, or None when the evidence is too thin."""
    if len(a.pts) == 0 or len(b.pts) == 0:
        return None
    t = _coarse_from_arcs(a, b)
    if t is not None:
        n = len(_exact_pairs(a.pts, b.pts, t))
        if n >= MIN_EXACT:
            return t, {"method": "arcs with equal radius", "exact_points": n}
    rooms = _coarse_from_rooms(a, b)
    if rooms is not None and rooms[1] >= 6.0:
        coarse, _, count = rooms
        tx, vx = _refine_axis(a.pts[:, 0], b.pts[:, 0], coarse[0])
        ty, vy = _refine_axis(a.pts[:, 1], b.pts[:, 1], coarse[1])
        ev = {"method": "rooms of equal size, refined per axis by coincident end points", "rooms_matched": count,
              "coarse": [round(coarse[0], 1), round(coarse[1], 1)], "x_votes": vx, "y_votes": vy,
              "exact_points": len(_exact_pairs(a.pts, b.pts, (tx, ty)))}
        if vx and vy and vx[0][1] >= 20 and vy[0][1] >= 20 and ev["exact_points"] >= MIN_EXACT:
            return (tx, ty), ev         # the two axes agree in a translation that makes end points coincide
    ranked = _vote2d(a.pts, b.pts, None, 0.0)
    if ranked and ranked[0][1] >= MIN_EXACT and (len(ranked) == 1 or ranked[0][1] >= 1.5 * ranked[1][1]):
        return ranked[0][0], {"method": "coincident end points", "exact_points": ranked[0][1],
                              "runner_up": ranked[1][1] if len(ranked) > 1 else 0}
    return None


def register(project: Project, drawings: dict[str, source.Drawing]) -> dict[str, list[Block]]:
    """Blocks of every level with their translation into the model frame."""
    blocks = {k: blocks_of(d) for k, d in drawings.items()}
    feats = {b.id: _features(drawings[k], bl, b.index) for k, bl in blocks.items() for b in bl}
    elev = {lv.key: lv.elevation for lv in project.levels}
    for bl in blocks.values():
        for b in bl:
            if b.id in project.registration:
                b.t, b.placed, b.evidence = project.registration[b.id], True, {"method": "pinned in the project file"}
    root = blocks[project.reference][0]
    if not root.placed:
        root.t, root.placed, root.evidence = (0.0, 0.0), True, {"method": "reference"}
    todo = sorted((b for bl in blocks.values() for b in bl if not b.placed), key=lambda b: -b.outline.area)
    progress = True
    while todo and progress:
        progress = False
        for b in list(todo):
            refs = sorted((r for bl in blocks.values() for r in bl if r.placed and r.level != b.level),
                          key=lambda r: (abs(elev[r.level] - elev[b.level]), -r.outline.area))
            for r in refs:
                m = _match(feats[b.id], feats[r.id])
                if m is None:
                    continue
                (tx, ty), ev = m
                b.t, b.placed = (tx + r.t[0], ty + r.t[1]), True
                b.evidence = {**ev, "against": r.id}
                todo.remove(b)
                progress = True
                break
    if todo:
        raise RuntimeError("no registration found for block(s) " + ", ".join(b.id for b in todo)
                           + '. Pin them in the project file: [registration] "level/block" = [tx, ty]')
    return blocks


def report(blocks: dict[str, list[Block]]) -> str:
    lines = ["# Registration", "",
             "Translation that is added to drawing coordinates to get model coordinates (mm).", "",
             "| Block | Outline area m2 | tx | ty | Against | Method | Coincident end points |", "|---|---|---|---|---|---|---|"]
    for bl in blocks.values():
        for b in bl:
            e = b.evidence
            lines.append(f"| {b.id} | {b.outline.area / 1e6:.1f} | {b.t[0]:.3f} | {b.t[1]:.3f} | {e.get('against', '')} "
                         f"| {e.get('method', '')} | {e.get('exact_points', '')} |")
    lines.append("")
    for bl in blocks.values():
        for b in bl:
            e = b.evidence
            if "x_votes" in e:
                lines.append(f"- {b.id}: {e['rooms_matched']} rooms matched, coarse {e['coarse']}; "
                             f"x votes {e['x_votes']}; y votes {e['y_votes']}")
            if "runner_up" in e:
                lines.append(f"- {b.id}: best translation has {e['exact_points']} coincident end points, the runner-up {e['runner_up']}")
    for k, bl in blocks.items():
        for b in bl[1:]:
            dx, dy = b.t[0] - bl[0].t[0], b.t[1] - bl[0].t[1]
            if math.hypot(dx, dy) > 0.5:
                lines.append(f"- Drawing {k}: block {b.index} is displaced by ({dx:.1f}, {dy:.1f}) mm relative to block 0 "
                             f"in the drawing. One translation per drawing would misplace it by {math.hypot(dx, dy):.0f} mm.")
    return "\n".join(lines) + "\n"
