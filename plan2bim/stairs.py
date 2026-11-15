"""Stairs: treads from the drawing, direction of climb from the project file.

A plan shows where the risers are. It does not show which way a stair climbs,
so the project file says that for every stair. Every tread becomes a vertical
prism: the stair is a stack of prisms with a stepped underside.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry import box as rectangle
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .level import Level
from .model import Note
from .project import Project, StairSpec

RISER_MIN = 600.0           # a riser line is at least this long
GOING = (150.0, 450.0)      # riser lines of one flight are this far apart
STRING = 200.0              # a strip up to this wide between a flight and the shaft wall is the string
SPIRAL_TREAD = 60.0         # spiral stair: plate treads, open risers
SPIRAL_REACH = 1500.0       # no spiral stair is wider than twice this
ARC_STEP = math.radians(3.0)

Bounds = tuple[float, float, float, float]


class StairError(Exception):
    """The drawing does not show a stair as the project file says. `build` turns it into a note."""


@dataclass
class Prism:
    poly: BaseGeometry
    z0: float
    z1: float
    part: str               # "tread" | "landing" | "post" | "rail"


@dataclass
class Stair:
    name: str
    level: str              # the storey it climbs from
    risers: int
    prisms: list[Prism]
    hole: BaseGeometry      # what must stay open in the plate of the storey above
    notes: list[str] = field(default_factory=list)


Flight = tuple[tuple[float, float], list[float]]     # extent across the stair, riser positions along it


def riser_lines(level: Level, bounds: Bounds, travel: int) -> list[Flight]:
    """The flights drawn inside `bounds`. `travel` is 0 for a stair that runs along x, 1 along y."""
    s = level.segs[level.seg_arc < 0]
    across = 1 - travel
    pos = s[:, travel]
    lo = np.clip(np.minimum(s[:, across], s[:, across + 2]), bounds[across], bounds[across + 2])
    hi = np.clip(np.maximum(s[:, across], s[:, across + 2]), bounds[across], bounds[across + 2])
    m = (np.abs(pos - s[:, travel + 2]) < 1.0) & (pos > bounds[travel] + 5.0) & (pos < bounds[travel + 2] - 5.0) & (hi - lo > 50.0)
    spans: dict[int, list[tuple[float, float, float]]] = {}
    for p, a, b in zip(pos[m].tolist(), lo[m].tolist(), hi[m].tolist(), strict=True):
        spans.setdefault(round(p), []).append((a, b, p))
    lines: list[tuple[float, float, float]] = []            # position, from, to: collinear pieces joined
    for pieces in spans.values():
        pieces.sort()
        a, b, p = pieces[0]
        for a2, b2, _ in pieces[1:]:
            if a2 <= b + 5.0:
                b = max(b, b2)
            else:
                lines.append((p, a, b))
                a, b = a2, b2
        lines.append((p, a, b))
    lines = [ln for ln in lines if ln[2] - ln[1] >= RISER_MIN]
    groups: list[tuple[list[float], list[float]]] = []
    for p, a, b in lines:
        for ext, ps in groups:
            if abs(ext[0] - a) < 30.0 and abs(ext[1] - b) < 30.0:
                ps.append(p)
                break
        else:
            groups.append(([a, b], [p]))
    flights: list[Flight] = []
    for ext, ps in groups:
        ps.sort()
        # the smallest spacing is the going: a storey above may hide some of the lines behind a cut line
        if len(ps) >= 3 and GOING[0] <= float(np.min(np.diff(ps))) <= GOING[1]:
            flights.append(((ext[0], ext[1]), ps))
    for ext, ps in flights:                                  # a line right across the shaft is a riser of every
        going = float(np.min(np.diff(ps)))                   # flight whose rhythm it continues
        for p, a, b in lines:
            if a <= ext[0] + 30.0 and b >= ext[1] - 30.0 and b - a > ext[1] - ext[0] + 60.0:
                if 0.6 * going <= ps[0] - p <= 1.4 * going:
                    ps.insert(0, p)
                elif 0.6 * going <= p - ps[-1] <= 1.4 * going:
                    ps.append(p)
    return sorted(flights)


def _rect(travel: int, across: tuple[float, float], p: float, q: float) -> Polygon:
    p, q = min(p, q), max(p, q)
    return rectangle(p, across[0], q, across[1]) if travel == 0 else rectangle(across[0], p, across[1], q)


def _travel(spec: StairSpec) -> int:
    side = spec.up if spec.kind == "straight" else spec.entry
    return 1 if side in ("N", "S") else 0


def _to_walls(ext: tuple[float, float], bounds: Bounds, across: int) -> tuple[float, float]:
    lo = bounds[across] if ext[0] - bounds[across] <= STRING else ext[0]
    hi = bounds[across + 2] if bounds[across + 2] - ext[1] <= STRING else ext[1]
    return lo, hi


def _flights(spec: StairSpec, levels: dict[str, Level], key: str) -> tuple[list[Flight], list[Flight]]:
    """Flights on the drawing that shows the whole stair, and on the drawing of the storey it starts on."""
    travel = _travel(spec)
    shown = riser_lines(levels[spec.draw_level or key], spec.box, travel)
    own = riser_lines(levels[key], spec.box, travel) if spec.draw_level else shown
    want = 2 if spec.kind == "dogleg" else 1
    if len(shown) < want:
        raise StairError(f"{len(shown)} flights found in the drawing, {want} needed")
    shown = sorted(sorted(shown, key=lambda f: -len(f[1]))[:want])
    return shown, own


def _across_here(shown: Flight, own: list[Flight]) -> tuple[float, float]:
    """Width of a flight on the storey it starts on, where that drawing shows it; else as on the other drawing."""
    near = [f for f in own if f[0][0] < shown[0][1] and f[0][1] > shown[0][0]]
    return max(near, key=lambda f: len(f[1]))[0] if near else shown[0]


def footprints(project: Project, levels: dict[str, Level]) -> dict[str, list[BaseGeometry]]:
    """Per storey, the plan area of the flights that start on it. Faces inside are treads, not walls.

    A stair that the drawing does not show has no area here. `build` reports it.
    """
    out: dict[str, list[BaseGeometry]] = {lv.key: [] for lv in project.levels}
    for spec in project.stairs:
        if spec.kind == "spiral":
            continue
        travel = _travel(spec)
        for key in spec.levels:
            try:
                shown, own = _flights(spec, levels, key)
            except StairError:
                continue
            for f in shown:
                out[key].append(_rect(travel, _across_here(f, own), f[1][0], f[1][-1]))
    return out


def area(spec: StairSpec) -> Polygon:
    """Where a stair stands in plan: its box, or the circle a spiral stair fills."""
    if spec.kind == "spiral":
        return Polygon([(spec.at[0] + SPIRAL_REACH * math.cos(math.tau * i / 48), spec.at[1] + SPIRAL_REACH * math.sin(math.tau * i / 48))
                        for i in range(48)])
    return rectangle(*spec.box)


def _dogleg(spec: StairSpec, project: Project, key: str, levels: dict[str, Level]) -> Stair:
    travel = _travel(spec)
    across = 1 - travel
    shown, own = _flights(spec, levels, key)
    first, second = (shown[0], shown[1]) if spec.up in ("W", "S") else (shown[1], shown[0])
    away = spec.entry in ("S", "W")                 # the first flight runs towards larger coordinates
    e0 = project.level(key).elevation
    rise = project.top_of(key) - e0
    n1, n2 = len(first[1]), len(second[1])
    h = rise / (n1 + n2)
    waist = project.heights.landing
    notes = [] if n1 == n2 else [f"the two flights have {n1} and {n2} risers"]
    prisms: list[Prism] = []
    ups = sorted(first[1], reverse=not away)        # riser positions in walking order
    wide = _to_walls(_across_here(first, own), spec.box, across)
    for i, (p, q) in enumerate(pairwise(ups), start=1):
        top = e0 + i * h
        prisms.append(Prism(_rect(travel, wide, p, q), max(e0, top - h - waist), top, "tread"))
    mid = e0 + n1 * h
    far = spec.box[travel + 2] if away else spec.box[travel]
    full = (spec.box[across], spec.box[across + 2])
    downs = sorted(second[1], reverse=away)         # from the mid landing back towards the entry
    wide2 = _to_walls(_across_here(second, own), spec.box, across)
    for j, (p, q) in enumerate(pairwise(downs), start=1):
        top = mid + j * h
        prisms.append(Prism(_rect(travel, wide2, p, q), max(mid - waist, top - h - waist), top, "tread"))
    # the landing takes what the two flights leave of the far end, also where they end a little apart
    near = min(ups[-1], downs[0]) if away else max(ups[-1], downs[0])
    landing = _rect(travel, full, near, far).difference(unary_union([p.poly for p in prisms]))
    prisms.append(Prism(landing, mid - waist, mid, "landing"))
    edge = max(ups[0], downs[-1]) if away else min(ups[0], downs[-1])   # past here both flights are open
    hole = unary_union([_rect(travel, full, edge, far),
                        _rect(travel, (full[0], first[0][1]) if spec.up in ("W", "S") else (first[0][0], full[1]), ups[0], far),
                        _rect(travel, (second[0][0], full[1]) if spec.up in ("W", "S") else (full[0], second[0][1]), downs[-1], far)])
    return Stair(spec.name, key, n1 + n2, prisms, hole, notes)


def _straight(spec: StairSpec, project: Project, key: str, levels: dict[str, Level]) -> Stair:
    travel = _travel(spec)
    shown, own = _flights(spec, levels, key)
    flight = shown[0]
    ups = sorted(flight[1], reverse=spec.up in ("W", "S"))
    e0 = project.level(key).elevation
    rise = project.top_of(key) - e0
    h = rise / len(ups)
    waist = project.heights.landing
    wide = _across_here(flight, own)
    prisms = [Prism(_rect(travel, wide, p, q), max(e0, e0 + i * h - h - waist), e0 + i * h, "tread")
              for i, (p, q) in enumerate(pairwise(ups), start=1)]
    return Stair(spec.name, key, len(ups), prisms, _rect(travel, flight[0], ups[0], ups[-1]), [])


def _wedge(c: tuple[float, float], r0: float, r1: float, a0: float, a1: float) -> Polygon:
    k = max(1, math.ceil(abs(a1 - a0) / ARC_STEP))
    angles = [a0 + (a1 - a0) * i / k for i in range(k + 1)]
    outer = [(c[0] + r1 * math.cos(a), c[1] + r1 * math.sin(a)) for a in angles]
    inner = [(c[0] + r0 * math.cos(a), c[1] + r0 * math.sin(a)) for a in reversed(angles)]
    return Polygon(outer + inner)


def _circles(level: Level, at: tuple[float, float]) -> tuple[tuple[float, float], list[float]] | None:
    arcs = [a for a in level.arcs if math.hypot(a.cx - at[0], a.cy - at[1]) <= 200.0]
    if not arcs:
        return None
    centre = (float(np.mean([a.cx for a in arcs])), float(np.mean([a.cy for a in arcs])))
    return centre, sorted({round(a.r, 1) for a in arcs})


def _spiral(spec: StairSpec, project: Project, key: str, levels: dict[str, Level]) -> Stair:
    found = _circles(levels[key], spec.at)
    if found is None or len(found[1]) < 3:
        raise StairError(f"no post and two outer circles drawn near {spec.at}")
    centre, radii = found
    post, r_in, r_out = radii[0], radii[-2], radii[-1]
    radial = _spokes(levels[key], centre, post, r_in)
    step = float(np.median(np.diff(radial))) if len(radial) >= 3 else 30.0
    treads = round(abs(spec.sweep_deg) / step)
    turn = math.copysign(math.radians(step), spec.sweep_deg)
    e0 = project.level(key).elevation
    rise = project.top_of(key) - e0
    h = rise / (treads + 1)
    a = math.radians(spec.start_deg)
    prisms: list[Prism] = []
    for i in range(1, treads + 1):
        top = e0 + i * h
        prisms.append(Prism(_wedge(centre, post, r_in, a, a + turn), top - SPIRAL_TREAD, top, "tread"))
        prisms.append(Prism(_wedge(centre, r_in, r_out, a, a + turn), top - SPIRAL_TREAD, top + project.heights.balustrade, "rail"))
        a += turn
    k = max(16, math.ceil(math.tau / ARC_STEP / 4))
    disc = Polygon([(centre[0] + post * math.cos(math.tau * i / k), centre[1] + post * math.sin(math.tau * i / k)) for i in range(k)])
    prisms.append(Prism(disc, e0, e0 + rise + project.heights.balustrade, "post"))
    # The storey above keeps a landing of a quarter turn past the last riser; the rest of the circle is open.
    # The hole holds the stair as this storey draws it and the circle as the storey above draws it, and the
    # post goes through the landing.
    upper = project.above(key)
    above = _circles(levels[upper.key], spec.at) if upper else None
    quarter = math.copysign(math.pi / 2.0, spec.sweep_deg)
    full = a + math.copysign(math.tau, spec.sweep_deg)
    parts = [_wedge(centre, 0.0, r_out, a + quarter, full), disc]
    notes = []
    if above:
        parts.append(_wedge(above[0], 0.0, above[1][-1], a + quarter, full))
        apart = math.hypot(above[0][0] - centre[0], above[0][1] - centre[1])
        if apart > 5.0:
            notes.append(f"the storey above draws this stair {apart:.0f} mm from where this storey draws it; "
                         f"the hole in its floor holds both")
    return Stair(spec.name, key, treads + 1, prisms, unary_union(parts), notes)


def _spokes(level: Level, centre: tuple[float, float], post: float, r_in: float) -> list[float]:
    """Directions, in degrees, of the straight lines drawn from the post to the inner circle."""
    out = []
    for x1, y1, x2, y2 in level.segs[level.seg_arc < 0].tolist():
        d1, d2 = math.hypot(x1 - centre[0], y1 - centre[1]), math.hypot(x2 - centre[0], y2 - centre[1])
        far = (x2, y2) if d2 > d1 else (x1, y1)
        if min(d1, d2) <= post + 30.0 and abs(max(d1, d2) - r_in) <= 60.0:
            out.append(math.degrees(math.atan2(far[1] - centre[1], far[0] - centre[0])) % 360.0)
    return sorted(out)


def build(project: Project, levels: dict[str, Level], blocked: dict[str, BaseGeometry], notes: list[Note]) -> list[Stair]:
    """All stairs, storey by storey.

    `blocked` is, per storey, the plan of its wall mass and of its door and window openings. No tread or
    landing enters it. A stair that the drawing does not show as the project file says is left out, and a
    note that fails the build goes to `notes`: the rest of the model is still there to look at.
    """
    make = {"dogleg": _dogleg, "straight": _straight, "spiral": _spiral}
    out: list[Stair] = []
    for spec in project.stairs:
        for key in spec.levels:
            try:
                stair = make[spec.kind](spec, project, key, levels)
            except StairError as e:
                c = area(spec).centroid
                notes.append(Note(f"stair {spec.name} from {key} is not in the model: {e}", key, (c.x, c.y), "fail", "stair"))
                continue
            room = levels[key].inner_all.difference(blocked[key])
            for p in stair.prisms:
                if p.part in ("tread", "landing"):
                    p.poly = p.poly.intersection(room)
            stair.prisms = [p for p in stair.prisms if not p.poly.is_empty]
            out.append(stair)
    return out
