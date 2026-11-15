"""Checks of the finished model against the drawings and against itself.

1. Cut each storey like a plan and compare the cut with the drawn lines, both ways: every face of the
   model must lie on a drawn line, and every drawn line must be the outline of a part of the model.
2. Balance the areas of each storey: the gross outline against the room outlines, the walls and what
   is left. Both outlines are the drafter's own, drawn for the area schedule.
3. Look for solids that overlap.
4. Look up and down: every room has a floor under it and a plate over it, every hole in a floor is
   named with the rooms it takes floor from, and each stair, as the model holds it, climbs in equal
   steps from its own floor to the next.
5. Read the IFC file back with another geometry kernel.

Nothing here changes the model. What it finds is for a person to read.

What these checks cannot see: a height, because the drawings hold none; and a cell that is given the
wrong meaning where both meanings have the same outline in plan.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LinearRing, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from .assemble import stair_name
from .classify import Classified
from .level import Level
from .mesh import Solid, section
from .model import Model
from .project import Project
from .stairs import Stair
from .stairs import area as stair_area

CUT = 1200.0            # mm above the floor, as in web.PLAN_CUT
STEP = 25.0             # mm between the points tested along a line
NEAR = 2.0              # mm: a point this close to a line is on it
CELL = 600.0            # mm: points that are off and this close to each other are one finding
STRUCTURE = ("walls", "columns")
SWING = (200.0, 1500.0, 100.0)     # an arc of this radius range in mm and at most this many degrees is a door symbol
PATCH = 0.05e6          # mm2: a smaller patch of plan is not named in the balance of areas
MOST = 6                # patches named for each term of the balance
OUTSIDE = 0.05          # share of a door or window that may lie outside the opening it fills


@dataclass
class Finding:
    level: str
    x: float
    y: float
    length: float       # mm of line
    worst: float        # mm, largest distance in it
    bounds: tuple[float, float, float, float]
    where: str = ""     # the stair that stands there, if one does


@dataclass
class Storey:
    level: str
    cut_length: float               # mm of model outline in the cut
    cut_on_line: float              # share of it that lies on a drawn line
    cut_off: list[Finding]          # model faces with no drawn line under them
    drawn_length: float             # mm of drawn line inside the building, the arcs of door symbols left out
    drawn_explained: float          # share of it that is the outline of a part of the model
    drawn_symbol: float             # mm of it in a door symbol or in the cross of a shaft: drawn, not built
    drawn_buried: float             # mm of it inside a wall and on no face of one
    drawn_off: list[Finding]        # drawn lines that nothing in the model explains
    buried: list[Finding]           # the longest runs of line inside walls
    areas: dict[str, float] = field(default_factory=dict)       # mm2 of plan, see `areas`
    patches: list[str] = field(default_factory=list)            # where the balance of areas leaves something over


@dataclass
class Report:
    storeys: list[Storey] = field(default_factory=list)
    overlaps: list[str] = field(default_factory=list)
    vertical: list[str] = field(default_factory=list)
    ifc: list[str] = field(default_factory=list)

    def text(self) -> str:
        out = ["Plan cut against the drawing", ""]
        for s in self.storeys:
            off = sum(f.length for f in s.drawn_off)
            in_stairs = sum(f.length for f in s.drawn_off if f.where)
            out.append(f"storey {s.level}: {s.cut_length / 1000:.0f} m of model outline in the cut, "
                       f"{100 * s.cut_on_line:.2f} % on a drawn line")
            out.append(f"storey {s.level}: {s.drawn_length / 1000:.0f} m of drawn line, {100 * s.drawn_explained:.2f} % "
                       f"on the outline of a part of the model; {s.drawn_symbol / 1000:.1f} m in door symbols and shaft "
                       f"crosses, which are drawn and not built; {s.drawn_buried / 1000:.1f} m inside a wall, where it "
                       f"parts nothing; not in the model: {in_stairs / 1000:.1f} m where a stair stands (walking lines, "
                       f"strings, guards, a stair as another storey draws it) and {(off - in_stairs) / 1000:.1f} m elsewhere")
            for name, items in (("model face with no drawn line", s.cut_off), ("drawn line not in the model", s.drawn_off),
                                ("drawn line inside a wall", s.buried)):
                for f in items:
                    out.append(f"    {name}: {f.length / 1000:.2f} m near ({f.x:.0f}, {f.y:.0f}), up to {f.worst:.0f} mm off"
                               + (f", in {f.where}" if f.where else ""))
        out += ["", "Balance of areas, in m2", ""]
        for s in self.storeys:
            a = {k: v / 1e6 for k, v in s.areas.items()}
            out.append(f"storey {s.level}: gross outline {a['gross']:.2f} = rooms {a['rooms']:.2f} + walls and columns "
                       f"{a['walls']:.2f} + openings in walls, with their doors and windows {a['openings']:.2f} + floor with "
                       f"no room outline {a['floor']:.2f} + open to the storey below {a['open']:.2f} + nothing "
                       f"{a['nothing']:.2f}. Inside room outlines: {a['wall_in_room']:.2f} of wall and "
                       f"{a['opening_in_room']:.2f} of openings, which a room outline runs through")
            out += [f"    {line}" for line in s.patches]
        out += ["", "Solids that overlap", "", *(self.overlaps or ["none"])]
        out += ["", "Up and down", "", *(self.vertical or ["nothing found"])]
        if self.ifc:
            out += ["", "The IFC file, read back", "", *self.ifc]
        return "\n".join(out) + "\n"


def _points(lines: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Points every `STEP` along segments (n, 4), and the length of line each point stands for."""
    d = lines[:, 2:] - lines[:, :2]
    length = np.hypot(d[:, 0], d[:, 1])
    count = np.maximum(1, np.ceil(length / STEP).astype(int))
    which = np.repeat(np.arange(len(lines)), count)
    first = np.cumsum(count) - count
    t = (np.arange(len(which)) - first[which] + 0.5) / count[which]
    return lines[which, :2] + d[which] * t[:, None], (length / count)[which]


def _segments(geoms: Sequence[BaseGeometry]) -> np.ndarray:
    """All boundary segments of polygons, or the segments of lines, as rows x1 y1 x2 y2."""
    rows = []
    for g in geoms:
        for part in getattr(g, "geoms", [g]):
            rings = [part.exterior, *part.interiors] if part.geom_type == "Polygon" else [part]
            for ring in rings:
                c = np.asarray(ring.coords)[:, :2]
                if len(c) >= 2:
                    rows.append(np.hstack([c[:-1], c[1:]]))
    return np.vstack(rows) if rows else np.zeros((0, 4))


def _distance(points: np.ndarray, lines: np.ndarray) -> np.ndarray:
    if len(lines) == 0:
        return np.full(len(points), np.inf)
    tree = shapely.STRtree(shapely.linestrings(lines.reshape(-1, 2, 2)))
    _, dist = tree.query_nearest(shapely.points(points), return_distance=True, all_matches=False)
    return dist


def _findings(level: str, points: np.ndarray, weight: np.ndarray, dist: np.ndarray, least: float) -> list[Finding]:
    """Group the points that are off a line into findings of at least `least` mm of line."""
    off = dist > NEAR
    if not off.any():
        return []
    p, w, d = points[off], weight[off], dist[off]
    cells: dict[tuple[int, int], list[int]] = {}
    for i, (cx, cy) in enumerate(np.floor(p / CELL).astype(int).tolist()):
        cells.setdefault((cx, cy), []).append(i)
    seen: set[tuple[int, int]] = set()
    out = []
    for start in cells:
        if start in seen:
            continue
        seen.add(start)
        stack, members = [start], []
        while stack:
            cx, cy = stack.pop()
            members += cells[(cx, cy)]
            for nb in ((cx + i, cy + j) for i in (-1, 0, 1) for j in (-1, 0, 1)):
                if nb in cells and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        idx = np.asarray(members)
        length = float(w[idx].sum())
        if length >= least:
            q = p[idx]
            out.append(Finding(level, float(q[:, 0].mean()), float(q[:, 1].mean()), length, float(d[idx].max()),
                               (float(q[:, 0].min()), float(q[:, 1].min()), float(q[:, 0].max()), float(q[:, 1].max()))))
    return sorted(out, key=lambda f: -f.length)


def cut_area(solids: list[Solid], key: str, z: float, categories: tuple[str, ...] = STRUCTURE) -> BaseGeometry:
    """Plan of what is solid where the horizontal plane at `z` cuts the storey's solids of the given categories."""
    outer, holes = [], []
    for s in solids:
        if s.level == key and s.category in categories:
            for ring in section(s.body, z):
                if len(ring) >= 3:
                    (outer if LinearRing(ring).is_ccw else holes).append(Polygon(ring))
    return unary_union(outer).difference(unary_union(holes))


def _patches(name: str, plan: BaseGeometry) -> list[str]:
    parts = sorted((g for g in getattr(plan, "geoms", [plan]) if g.geom_type == "Polygon" and g.area >= PATCH),
                   key=lambda g: -g.area)
    out = [f"{name}: {g.area / 1e6:.2f} m2 near ({g.representative_point().x:.0f}, {g.representative_point().y:.0f})"
           for g in parts[:MOST]]
    if len(parts) > MOST:
        out.append(f"{name}: {len(parts) - MOST} more patches, {sum(g.area for g in parts[MOST:]) / 1e6:.2f} m2 together")
    return out


def areas(level: Level, model: Model) -> tuple[dict[str, float], list[str]]:
    """Where the plan area inside the gross outline of a storey goes, and the patches that fit nowhere.

    The room outlines and the gross outline are the drafter's own, drawn for the area schedule. Wall that
    stands inside a room outline, or plan that is neither room nor wall nor floor, contradicts them. A room
    outline runs through the door openings of its room, so openings that lie in one say nothing.
    """
    key, gross = level.key, level.gross_all
    own = [e for e in model.elements if e.level == key]
    rooms = unary_union([r.poly for r in level.rooms]).intersection(gross)
    # what stands from floor to ceiling, and the openings in it with their doors and windows
    solid = unary_union([q.poly for e in own if e.kind in ("wall", "column") and e.void is None for q in e.pieces])
    solid = solid.intersection(gross)
    openings = unary_union([e.void.poly for e in own if e.void is not None]
                           + [q.poly for e in own if e.kind in ("door", "window") for q in e.pieces])
    openings = openings.intersection(gross).difference(solid)
    plate = unary_union([q.poly for e in own if e.kind == "slab" for q in e.pieces if q.material == "plate"])
    floor = plate.intersection(gross).difference(unary_union([rooms, solid, openings]))
    void = model.open_floor(key).intersection(gross).difference(unary_union([rooms, solid, openings, floor]))
    nothing = gross.difference(unary_union([rooms, solid, openings, floor, void]))
    out = {"gross": gross.area, "rooms": rooms.area, "walls": solid.difference(rooms).area,
           "openings": openings.difference(rooms).area, "floor": floor.area, "open": void.area, "nothing": nothing.area,
           "wall_in_room": solid.intersection(rooms).area, "opening_in_room": openings.intersection(rooms).area}
    return out, (_patches("floor with no room outline", floor) + _patches("neither room, wall nor floor", nothing)
                 + _patches("wall inside a room outline", solid.intersection(rooms)))


def storey(project: Project, level: Level, c: Classified, model: Model, solids: list[Solid], least: float = 150.0) -> Storey:
    key = level.key
    z = project.level(key).elevation + CUT
    drawn = level.segs[:, :4]

    # the model, cut: every face must lie on a drawn line
    cut = _segments([cut_area(solids, key, z)])
    p, w = _points(cut)
    d = _distance(p, drawn)
    cut_off = _findings(key, p, w, d, least)

    # The drawing: every line inside the building must be the outline of a part of the model: of what stands
    # on the storey, of the holes in its walls and in its floor, or of the stair that comes up from below.
    lower = project.below(key)
    below = lower.key if lower else None
    own = [e for e in model.elements if (e.level == key and e.kind != "roof") or (e.kind == "stair" and e.level == below)]
    shapes: list[BaseGeometry] = [q.poly for e in own for q in e.pieces]
    shapes += [e.void.poly for e in own if e.void is not None]
    shapes += [plan for _, plan in model.holes.get(key, [])]
    outline = _segments(shapes)
    swing = np.array([SWING[0] <= a.r <= SWING[1] and abs(np.degrees(a.sweep)) <= SWING[2] for a in level.arcs] + [False])
    drawn = drawn[~swing[level.seg_arc]]
    inside = level.gross_all.buffer(50.0)
    q, v = _points(drawn)
    keep = shapely.contains_xy(inside, q[:, 0], q[:, 1])
    q, v = q[keep], v[keep]
    e = _distance(q, outline)
    off = e > NEAR
    # a door symbol and the cross in a shaft are drawn and not built
    symbols = unary_union([g.buffer(NEAR) for g in (*c.swing, *c.shafts)])
    symbol = off & shapely.contains_xy(symbols, q[:, 0], q[:, 1])
    # a line inside a wall, on no face of it, parts nothing that the model keeps apart
    walls = unary_union([piece.poly for el in own if el.kind == "wall" for piece in el.pieces])
    buried = off & ~symbol & shapely.contains_xy(walls, q[:, 0], q[:, 1])
    left = np.where(symbol | buried, 0.0, e)
    drawn_off = _findings(key, q, v, left, least)
    served = [(s.name, stair_area(s)) for s in project.stairs if key in s.levels or below in s.levels]
    for f in drawn_off:
        f.where = next((f"stair {name}" for name, plan in served if plan.contains(Point(f.x, f.y))), "")
    balance, patches = areas(level, model)
    return Storey(key, float(w.sum()), float(w[d <= NEAR].sum() / max(w.sum(), 1.0)), cut_off,
                  float(v.sum()), float(v[~off].sum() / max(v.sum(), 1.0)), float(v[symbol].sum()), float(v[buried].sum()),
                  drawn_off, _findings(key, q, v, np.where(buried, e, 0.0), 4.0 * least)[:8], balance, patches)


def overlaps(solids: list[Solid], least: float = 1e6) -> list[str]:
    """Pairs of solids that share more than `least` mm3. Solids of one kind on one storey are one solid already."""
    out = []
    for s in solids:
        if abs(s.volume_in - s.body.volume()) > least:
            out.append(f"storey {s.level}, {s.category}/{s.material}: prisms overlap by "
                       f"{(s.volume_in - s.body.volume()) / 1e9:.3f} m3")
    boxes = [s.body.bounding_box() for s in solids]
    for i, a in enumerate(solids):
        for j in range(i + 1, len(solids)):
            b = solids[j]
            if any(boxes[i][k] >= boxes[j][k + 3] or boxes[j][k] >= boxes[i][k + 3] for k in range(3)):
                continue
            shared = (a.body ^ b.body).volume()
            if shared > least:
                out.append(f"{a.category}/{a.material} of storey {a.level} and {b.category}/{b.material} of storey {b.level} "
                           f"share {shared / 1e9:.3f} m3")
    return out


def _stair(project: Project, model: Model, st: Stair, plates: dict[str, BaseGeometry]) -> str:
    """One stair as the model holds it: equal steps, each reached from the one under it, from floor to floor."""
    e0, e1 = project.level(st.level).elevation, project.top_of(st.level)
    rise = (e1 - e0) / st.risers
    line = f"stair {st.name} from {st.level}: {st.risers} risers of {rise:.0f} mm"
    name = stair_name(project, st)
    found = next((e for e in model.elements if e.kind == "stair" and e.level == st.level and e.name == name), None)
    if found is None:
        return f"{line}; NOT IN THE MODEL"
    walked = [p for p in found.pieces if p.material == "stair"]
    # the top of a piece is a step unless another piece of the stair stands on it
    steps: dict[float, list[Polygon]] = {}
    for p in walked:
        if not any(abs(o.z0 - p.z1) < 0.5 and o.poly.intersection(p.poly).area > 1.0 for o in walked):
            steps.setdefault(round(p.z1, 1), []).append(p.poly)
    tops = sorted(steps)
    faults = []
    if len(tops) != st.risers - 1 or any(abs(t - (e0 + i * rise)) > 1.0 for i, t in enumerate(tops, start=1)):
        faults.append(f"it has steps at {len(tops)} heights where {st.risers - 1} equal rises are wanted")
    plans = [unary_union(steps[t]) for t in tops]
    apart = [i for i, (a, b) in enumerate(pairwise(plans), start=2) if a.distance(b) > NEAR]
    if apart:
        faults.append(f"step {apart[0]} does not touch the step under it" + (f", nor do {len(apart) - 1} more" if apart[1:] else ""))
    if plans and plans[0].distance(plates[st.level]) > NEAR:
        faults.append("its first step does not stand at the floor it leaves")
    upper = project.above(st.level)
    if upper and plans:
        floor = plates[upper.key]
        if plans[-1].distance(floor) > NEAR:
            faults.append(f"its top step does not meet the floor of {upper.name}")
        elif plans[-1].difference(floor).area < 0.5 * plans[-1].area:
            faults.append(f"its top step lies under the floor of {upper.name}")
    have = sum(p.poly.area * (p.z1 - p.z0) for p in found.pieces)
    want = sum(p.poly.area * (p.z1 - p.z0) for p in st.prisms)
    if want - have > 1e-3 * want:
        faults.append(f"{100 * (want - have) / want:.1f} % of it is cut away where it passes the floor above")
    if faults:
        return f"{line}; FAULT: " + "; ".join(faults)
    reach = f", the top one meets the floor of {upper.name}" if upper else ""
    return f"{line}; the model holds {len(tops)} steps, each touches the one under it{reach}"


def vertical(project: Project, levels: dict[str, Level], model: Model, stairs: list[Stair]) -> list[str]:
    out = []
    plates: dict[str, BaseGeometry] = {}
    covers: dict[str, BaseGeometry] = {}
    for spec in project.levels:
        key = spec.key
        own = [e for e in model.elements if e.level == key]
        plates[key] = unary_union([p.poly for e in own if e.kind == "slab" for p in e.pieces])
        # what closes the storey below: all that starts at the underside of this storey's floor plate
        under = spec.elevation - project.heights.plate
        covers[key] = unary_union([p.poly for e in own for p in e.pieces if p.z0 <= under + 1.0])
    for spec in project.levels:
        key = spec.key
        above = project.above(key)
        roof = unary_union([p.poly for e in model.elements if e.level == key and e.kind == "roof" for p in e.pieces])
        cover = unary_union([roof, covers[above.key], model.open_floor(above.key)]) if above else roof
        for r in levels[key].rooms:
            bare = r.poly.difference(plates[key]).difference(model.open_floor(key)).area
            if bare > 0.02e6 and bare > 0.01 * r.poly.area:
                out.append(f"storey {key}, room {r.number} {r.name}: {bare / 1e6:.2f} m2 of it has no floor")
            open_sky = r.poly.difference(cover).area
            if open_sky > 0.02e6 and open_sky > 0.01 * r.poly.area:
                out.append(f"storey {key}, room {r.number} {r.name}: {open_sky / 1e6:.2f} m2 of it has nothing over it")
    # every hole in a floor, with what the drawing gives as its reason and the rooms it takes floor from
    for spec in project.levels:
        for why, plan in model.holes.get(spec.key, []):
            taken = [f"{a / 1e6:.2f} m2 of " + (f"room {r.number} {r.name}".rstrip() if r.number or r.name
                                                else "a room outline with no label")
                     for r in levels[spec.key].rooms if (a := r.poly.intersection(plan).area) > 0.02e6]
            out.append(f"storey {spec.key}: the floor is open over {plan.area / 1e6:.2f} m2 for {why}"
                       + (f"; that is {', '.join(taken)}" if taken else "; no room outline lies there"))
    out += [_stair(project, model, st, plates) for st in stairs]
    return out


def _volume(v: np.ndarray, t: np.ndarray) -> float:
    """Volume inside a closed triangle mesh whose triangles face outwards."""
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def _slice(v: np.ndarray, t: np.ndarray, z: float) -> list[Polygon]:
    """Plan of what is solid where the horizontal plane at `z` cuts a closed triangle mesh."""
    if np.any(np.abs(v[:, 2] - z) < 1e-6):
        z += 0.137                                   # no vertex in the plane
    lines = []
    for tri in t[(v[t][:, :, 2].min(axis=1) < z) & (v[t][:, :, 2].max(axis=1) > z)]:
        ends = []
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            if (v[a, 2] - z) * (v[b, 2] - z) < 0.0:
                lo, hi = (a, b) if a < b else (b, a)                 # both triangles on an edge get the same point
                s = (z - v[lo, 2]) / (v[hi, 2] - v[lo, 2])
                ends.append(v[lo, :2] + s * (v[hi, :2] - v[lo, :2]))
        if len(ends) == 2 and not np.array_equal(ends[0], ends[1]):
            lines.append(np.concatenate(ends))
    if not lines:
        return []
    seg = np.asarray(lines)
    out = []
    for face in polygonize(shapely.union_all(shapely.linestrings(seg.reshape(-1, 2, 2)))):
        p = face.representative_point()
        y1, y2 = seg[:, 1], seg[:, 3]
        spans = (y1 > p.y) != (y2 > p.y)
        x = seg[spans, 0] + (p.y - y1[spans]) * (seg[spans, 2] - seg[spans, 0]) / (y2[spans] - y1[spans])
        if np.count_nonzero(x > p.x) % 2 == 1:                       # inside: an odd number of faces to cross
            out.append(face)
    return out


def _profiles(f) -> tuple[dict[int, BaseGeometry], list[str]]:
    """The plan of every product in the file, from the outlines it extrudes, and the products with an outline
    that is not the outline of an area. No placement in the file turns or moves a plan."""
    plans: dict[int, BaseGeometry] = {}
    bad = []
    for product in f.by_type("IfcProduct"):
        if product.Representation is None:
            continue
        parts = []
        for rep in product.Representation.Representations:
            for item in rep.Items:
                area = item.SweptArea
                rings = [[pt.Coordinates for pt in ring.Points]
                         for ring in (area.OuterCurve, *(getattr(area, "InnerCurves", None) or []))]
                poly = Polygon(rings[0], rings[1:])
                if not poly.is_valid:
                    bad.append(product.Name or f"#{product.id()}")
                    poly = poly.buffer(0)
                parts.append(poly)
        plans[product.id()] = unary_union(parts)
    return plans, sorted(set(bad))


def ifc(path: Path, project: Project, levels: dict[str, Level], model: Model, solids: list[Solid]) -> list[str]:
    """Read the IFC file back with another geometry kernel and check it as the model was checked."""
    import ifcopenshell
    import ifcopenshell.geom

    from .ifc import CLASS

    f = ifcopenshell.open(str(path))
    key_of = {lv.name: lv.key for lv in project.levels}
    storey_of = {el.id(): key_of[rel.RelatingStructure.Name]
                 for rel in f.by_type("IfcRelContainedInSpatialStructure") for el in rel.RelatedElements}
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("convert-back-units", True)
    settings.set("weld-vertices", True)
    volume: dict[tuple[str, str], float] = {}
    count: dict[tuple[str, str], int] = {}
    plans: dict[str, list[BaseGeometry]] = {lv.key: [] for lv in project.levels}
    it = ifcopenshell.geom.iterator(settings, f, os.cpu_count() or 1, exclude=["IfcOpeningElement", "IfcSpace"])
    if it.initialize():
        while True:
            shape = it.get()
            el = f.by_id(shape.id)
            key = storey_of[el.id()]
            v = np.asarray(shape.geometry.verts, dtype=np.float64).reshape(-1, 3)
            t = np.asarray(shape.geometry.faces, dtype=np.int64).reshape(-1, 3)
            volume[(key, el.is_a())] = volume.get((key, el.is_a()), 0.0) + _volume(v, t)
            count[(key, el.is_a())] = count.get((key, el.is_a()), 0) + 1
            if el.is_a() in ("IfcWall", "IfcColumn"):
                plans[key] += _slice(v, t, project.level(key).elevation + CUT)
            if not it.next():
                break
    want: dict[tuple[str, str], float] = {}
    have: dict[tuple[str, str], int] = {}
    for e in model.elements:
        k = (e.level, CLASS[e.kind][0])
        want[k] = want.get(k, 0.0) + sum(p.poly.area * (p.z1 - p.z0) for p in e.pieces)
        have[k] = have.get(k, 0) + (1 if e.pieces else 0)
    out = []
    worst = 0.0
    for k in sorted(set(want) | set(volume)):
        a, b = want.get(k, 0.0), volume.get(k, 0.0)
        worst = max(worst, abs(a - b) / max(a, 1.0))
        if abs(a - b) > 1e-4 * max(a, 1e9) or have.get(k, 0) != count.get(k, 0):
            out.append(f"storey {k[0]}, {k[1]}: the file holds {count.get(k, 0)} with {b / 1e9:.3f} m3, "
                       f"the model {have.get(k, 0)} with {a / 1e9:.3f} m3")
    out.insert(0, f"{sum(count.values())} elements read back; volume per storey and kind differs from the model "
                  f"by at most {100 * worst:.4f} %")
    for spec in project.levels:
        joined = unary_union([g.buffer(0.02, join_style="mitre") for g in plans[spec.key]]).buffer(-0.02, join_style="mitre")
        own = cut_area(solids, spec.key, spec.elevation + CUT)
        p, w = _points(_segments([joined]))
        d = _distance(p, levels[spec.key].segs[:, :4])
        out.append(f"storey {spec.key}: the cut of the file is {joined.area / 1e6:.3f} m2 of wall and column and differs "
                   f"from the cut of the model by {joined.symmetric_difference(own).area / 1e6:.4f} m2; "
                   f"{100 * w[d <= NEAR].sum() / max(w.sum(), 1.0):.2f} % of its outline is on a drawn line")

    # how the file relates its parts: outlines, holes in walls, and the doors and windows that fill them
    plan, bad = _profiles(f)
    out.append("every extruded outline in the file is the outline of an area" if not bad else
               f"{len(bad)} elements extrude an outline that crosses itself: {', '.join(bad[:6])}")
    voids = {rel.RelatedOpeningElement.id() for rel in f.by_type("IfcRelVoidsElement")
             if rel.RelatingBuildingElement.is_a("IfcWall")}
    loose = [o.Name for o in f.by_type("IfcOpeningElement") if o.id() not in voids]
    filled = {}
    for rel in f.by_type("IfcRelFillsElement"):
        filled[rel.RelatedBuildingElement.id()] = rel.RelatingOpeningElement
    astray, unhosted = [], []
    fillings = [*f.by_type("IfcDoor"), *f.by_type("IfcWindow")]
    for el in fillings:
        hole = filled.get(el.id())
        if hole is None:
            unhosted.append(el.Name)
        elif plan[el.id()].difference(plan[hole.id()].buffer(NEAR)).area > OUTSIDE * plan[el.id()].area:
            astray.append(el.Name)
    out.append(f"{len(voids)} openings, each cuts a wall" + (f"; {len(loose)} cut nothing: {', '.join(loose[:6])}" if loose else ""))
    out.append(f"{len(fillings)} doors and windows: {len(fillings) - len(unhosted) - len(astray)} fill an opening and lie in it"
               + (f"; {len(unhosted)} fill no opening: {', '.join(unhosted[:6])}" if unhosted else "")
               + (f"; {len(astray)} lie outside the opening they fill: {', '.join(astray[:6])}" if astray else ""))
    return out


def run(project: Project, levels: dict[str, Level], cls: dict[str, Classified], model: Model, solids: list[Solid],
        stairs: list[Stair], ifc_file: Path | None = None) -> Report:
    report = Report()
    for spec in project.levels:
        report.storeys.append(storey(project, levels[spec.key], cls[spec.key], model, solids))
    report.overlaps = overlaps(solids)
    report.vertical = vertical(project, levels, model, stairs)
    if ifc_file is not None:
        report.ifc = ifc(ifc_file, project, levels, model, solids)
    return report
