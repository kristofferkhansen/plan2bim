"""Say what every face of the arrangement is: wall mass, window, door, opening, space.

The rules, in the order they are applied:

1. A face inside a room polygon is room.
2. A face that the drawn lines close completely, and that is too narrow to be
   a space, is wall mass, exactly as drawn.
3. Faces that only a door symbol (leaf and swing arc) encloses are not solid.
4. A drawn circle of column size is a column.
5. A group of very thin closed faces with a long strip is window joinery. The
   closed faces between its two jamb lines are the window niche. Where the
   drafter left the jamb lines out, the bay is cut through the wall.
6. A face that drawn lines do not close is a gap: a door, an opening, a seam
   between two room outlines. Two special cases are wall after all: a wall
   whose end line was not drawn, and a wall of which only one face was drawn
   ("implied wall"; every one is reported for review).
7. A door is a swing arc with its leaf. Its rough opening runs from wall end
   to wall end and through the wall thickness, both measured on the drawing.
   A wall drawn unbroken across a door is cut through, and reported.
8. Two wall ends of about the same thickness that face each other with no
   door between them are an opening.
9. A cell with two equal diagonals drawn across it is a shaft: no wall
   stands in it and the floor is open there.
10. Thin closed strips drawn inside a room, with the small posts between
    them, are sliding doors or screens. A gap between two wall ends that
    such panels close is an opening of any width up to SPAN.
11. A closed area outside the building at a door, with a thin closed strip
    round it, is a balcony and the strip its balustrade.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from .arrange import Arrangement, Face
from .level import Level
from .model import Note
from .project import Project
from .stairs import area as stair_area

SLIVER = 0.6            # inscribed radius below this: two lines that should coincide, no face
MAX_WALL = 600.0        # no wall is thicker than this
SPACE_INR = MAX_WALL / 2.0      # inscribed radius: a closed face wider than a wall is a space
JOINERY = 60.0          # frames, sashes and glass are drawn thinner than this; walls are not
STRIP = 250.0           # a joinery face at least this long is a glass strip
LINE_PAIR = 25.0        # a closed face thinner than this, outside a window, is a doubled line
SHAFT_DRAWN = 0.9       # share of the outline of a crossed cell that must be drawn for it to be a shaft
SEAM = 0.5              # a gap thinner than twice this between a window bay and its cut through the wall is closed
NICHE_MARGIN = 80.0     # a window niche may reach this far past the joinery at each jamb
JAMB_LEN, JAMB_THICK = 260.0, 75.0      # largest door frame section as drawn
OPENING = (500.0, 2100.0)               # clear width of a gap in a wall that counts as an opening
SPAN = 8000.0           # widest gap between two wall ends that a row of sliding doors closes
PANEL = (25.0, 70.0)    # thickness of a sliding door or a screen drawn on its own
SHAFT_DIAGONAL = 600.0  # shortest diagonal of a cross that marks a shaft
BALUSTRADE = 260.0      # a closed strip around a balcony up to this thick is its balustrade
BALCONY_MAX = 40e6      # mm2; a larger closed area outside the building is not a balcony

SOLID = ("wall", "implied", "party")    # kinds that stand the full storey height


@dataclass
class Leaf:
    hinge: tuple[float, float]
    r: float
    u: tuple[float, float]          # unit vector from the hinge to the tip of the closed leaf
    open_dir: tuple[float, float]   # unit vector from the hinge to the tip as drawn (open)
    swing: int                      # +1 opens to the left of u, -1 to the right
    thick: float                    # leaf thickness as drawn
    body: int                       # side of the leaf body relative to its own line (+1 left, -1 right)
    arc: int


@dataclass
class Frame:
    """Local axes of an opening: origin, u along the wall, n = left normal of u."""
    origin: tuple[float, float]
    u: tuple[float, float]

    @property
    def n(self) -> tuple[float, float]:
        return -self.u[1], self.u[0]

    def point(self, s: float, n: float) -> tuple[float, float]:
        return (self.origin[0] + s * self.u[0] - n * self.u[1], self.origin[1] + s * self.u[1] + n * self.u[0])

    def rect(self, s0: float, s1: float, n0: float, n1: float) -> Polygon:
        return Polygon([self.point(s0, n0), self.point(s1, n0), self.point(s1, n1), self.point(s0, n1)])

    def extent(self, geom: BaseGeometry) -> tuple[float, float, float, float]:
        c = shapely.get_coordinates(geom) - np.array(self.origin)
        s, n = c @ np.array(self.u), c @ np.array(self.n)
        return float(s.min()), float(s.max()), float(n.min()), float(n.max())


@dataclass
class Window:
    id: int
    frame: Frame
    faces: list[int]                # joinery faces
    strips: list[int]               # the joinery faces that are glass strips
    s: tuple[float, float]          # joinery extent along the wall
    n: tuple[float, float]          # joinery extent across the wall
    niche: list[int] = field(default_factory=list)       # closed faces between the jambs
    bay: BaseGeometry = field(default_factory=Polygon)   # the hole through the wall, in plan
    carved: float = 0.0                                  # wall thickness cut through where no jamb lines are drawn
    exterior: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Door:
    id: int
    frame: Frame                    # origin at the first hinge
    leaves: list[Leaf]
    s: tuple[float, float]          # clear opening along the wall (hinge to hinge, or hinge to leaf tip)
    rough: Polygon = field(default_factory=Polygon)      # rough opening in plan: wall end to wall end, through the wall
    jambs: list[int] = field(default_factory=list)       # frame faces
    exterior: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Balcony:
    floor: Polygon                  # the slab in plan, under the balustrade as well
    rail: BaseGeometry              # the balustrade in plan
    door: int


@dataclass
class Classified:
    level: Level
    arr: Arrangement
    leaves: list[Leaf]
    windows: list[Window]
    doors: list[Door]
    columns: list[Polygon]
    openings: list[Polygon]         # gaps in walls with no door drawn, wall end to wall end
    balconies: list[Balcony]
    swing: list[BaseGeometry]       # region each door symbol covers (leaf as drawn + swept sector)
    panels: list[Polygon] = field(default_factory=list)     # sliding doors and screens drawn on their own
    shafts: list[Polygon] = field(default_factory=list)     # cells marked with a cross: open to the storey below
    notes: list[Note] = field(default_factory=list)

    def solids(self) -> list[tuple[Face, BaseGeometry]]:
        return [(f, f.mass) for f in self.arr.faces if f.kind in SOLID and not f.mass.is_empty]

    def blocked(self) -> BaseGeometry:
        """Plan of the wall mass and of the door and window openings: no stair tread enters it."""
        return unary_union([g for _, g in self.solids()] + [d.rough for d in self.doors] + [w.bay for w in self.windows])


# ------------------------------------------------------------------ probing along a line

class Probe:
    """Where a straight line runs through wall mass."""

    def __init__(self, arr: Arrangement, kinds: tuple[str, ...] = SOLID) -> None:
        self.arr, self.kinds = arr, kinds

    def intervals(self, p: tuple[float, float], d: tuple[float, float], length: float,
                  skip: frozenset[int] = frozenset()) -> list[tuple[float, float]]:
        """Distances from p along d at which the line is inside wall mass, merged and sorted."""
        seg = LineString([p, (p[0] + d[0] * length, p[1] + d[1] * length)])
        out: list[tuple[float, float]] = []
        for f in self.arr.near(seg):
            if f.kind not in self.kinds or f.id in skip:
                continue
            part = f.mass.intersection(seg)
            for g in getattr(part, "geoms", [part]):
                if g.geom_type == "LineString" and g.length > 0:
                    c = np.asarray(g.coords) - np.array(p)
                    t = c @ np.array(d)
                    out.append((float(t.min()), float(t.max())))
        out.sort()
        merged: list[tuple[float, float]] = []
        for a, b in out:
            if merged and a <= merged[-1][1] + 0.05:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        return merged

    def first_hit(self, p, d, length, skip: frozenset[int] = frozenset()) -> float | None:
        iv = self.intervals(p, d, length, skip)
        return iv[0][0] if iv else None

    def depth(self, p, d, length) -> float:
        """Thickness of the wall mass that starts right at p, 0 if p is not at a wall."""
        iv = self.intervals(p, d, length)
        return iv[0][1] if iv and iv[0][0] <= 1.0 else 0.0


# ------------------------------------------------------------------ door symbols

def find_leaves(level: Level) -> list[Leaf]:
    """A door leaf is an arc of 20-100 degrees with a drawn line from one of its ends to (or through) its centre."""
    straight = level.segs[level.seg_arc < 0]
    p0, p1 = straight[:, :2], straight[:, 2:]
    t0, t1 = cKDTree(p0), cKDTree(p1)
    d = p1 - p0
    ln = np.hypot(d[:, 0], d[:, 1])

    def leaf_lines(e: tuple[float, float], h: tuple[float, float], r: float) -> int:
        count = 0
        for j in set(t0.query_ball_point(e, 3.0)) | set(t1.query_ball_point(e, 3.0)):
            if ln[j] < r - 3.0:
                continue
            t = ((h[0] - p0[j, 0]) * d[j, 0] + (h[1] - p0[j, 1]) * d[j, 1]) / ln[j] ** 2
            t = min(1.0, max(0.0, t))
            if math.dist(h, (p0[j, 0] + t * d[j, 0], p0[j, 1] + t * d[j, 1])) <= 3.0:
                count += 1
        return count

    out: list[Leaf] = []
    for ai, a in enumerate(level.arcs):
        if not (200.0 <= a.r <= 1500.0 and 20.0 <= abs(math.degrees(a.sweep)) <= 100.0):
            continue
        h = (a.cx, a.cy)
        ends = [a.point(a.a0), a.point(a.a0 + a.sweep)]
        drawn = [leaf_lines(e, h, a.r) for e in ends]
        if (drawn[0] > 0) == (drawn[1] > 0):
            continue
        o, c = (ends[0], ends[1]) if drawn[0] else (ends[1], ends[0])
        od = ((o[0] - h[0]) / a.r, (o[1] - h[1]) / a.r)
        u = ((c[0] - h[0]) / a.r, (c[1] - h[1]) / a.r)
        swing = 1 if u[0] * od[1] - u[1] * od[0] > 0 else -1
        thick, body = 40.0, swing
        for j in set(t0.query_ball_point(h, 70.0)) | set(t1.query_ball_point(h, 70.0)):
            # the leaf's other long side: parallel to the drawn line, 25-60 mm beside it
            if ln[j] == 0 or abs(ln[j] - a.r) > 0.06 * a.r or abs(d[j, 0] * od[1] - d[j, 1] * od[0]) / ln[j] > 0.02:
                continue
            off = (p0[j, 0] - h[0]) * -od[1] + (p0[j, 1] - h[1]) * od[0]
            if 25.0 <= abs(off) <= 60.0:
                thick, body = abs(off), (1 if off > 0 else -1)
                break
        out.append(Leaf(h, a.r, u, od, swing, thick, body, ai))
    return out


def swing_region(level: Level, leaf: Leaf) -> BaseGeometry:
    """What the door symbol covers: the sector between the open and the closed leaf, and the leaf as drawn."""
    a = level.arcs[leaf.arc]
    k = max(2, math.ceil(abs(math.degrees(a.sweep)) / 3.0))
    sector = Polygon([leaf.hinge, *(a.point(a.a0 + a.sweep * i / k) for i in range(k + 1))])
    od, h = leaf.open_dir, leaf.hinge
    px, py = -od[1] * leaf.body * leaf.thick, od[0] * leaf.body * leaf.thick
    tip = (h[0] + od[0] * leaf.r, h[1] + od[1] * leaf.r)
    body = Polygon([h, tip, (tip[0] + px, tip[1] + py), (h[0] + px, h[1] + py)])
    return unary_union([sector.buffer(0), body.buffer(0)])


def pair_leaves(leaves: list[Leaf]) -> list[Door]:
    """One door per leaf, or per two leaves that close against each other."""
    doors: list[Door] = []
    used: set[int] = set()
    tips = [(lf.hinge[0] + lf.r * lf.u[0], lf.hinge[1] + lf.r * lf.u[1]) for lf in leaves]
    for i, a in enumerate(leaves):
        if i in used:
            continue
        mate = None
        for j in range(i + 1, len(leaves)):
            b = leaves[j]
            if j not in used and math.dist(tips[i], tips[j]) < 100.0 and a.u[0] * b.u[0] + a.u[1] * b.u[1] < -0.95:
                mate = j
                break
        used.add(i)
        if mate is None:
            doors.append(Door(len(doors), Frame(a.hinge, a.u), [a], (0.0, a.r)))
        else:
            used.add(mate)
            b = leaves[mate]
            span = (b.hinge[0] - a.hinge[0]) * a.u[0] + (b.hinge[1] - a.hinge[1]) * a.u[1]
            doors.append(Door(len(doors), Frame(a.hinge, a.u), [a, b], (0.0, span)))
    return doors


# ------------------------------------------------------------------ columns

def find_columns(level: Level) -> list[Polygon]:
    """Drawn circles of column size, each as the outline its own arcs give.

    Arcs of one radius about nearly the same centre that together cover most of a circle count as the
    circle: a column that stands in a wall is drawn as the two arcs the wall leaves of it.
    """
    groups: list[tuple[list[float], list[int]]] = []    # (sum of cx, cy weighted by sweep; radius; sweep), arcs
    for i, a in enumerate(level.arcs):
        if not 80.0 <= a.r <= 500.0:
            continue
        deg = abs(math.degrees(a.sweep))
        for g, members in groups:
            if abs(g[2] - a.r) <= 2.0 and math.hypot(g[0] / g[3] - a.cx, g[1] / g[3] - a.cy) <= 0.25 * a.r:
                g[0] += a.cx * deg
                g[1] += a.cy * deg
                g[3] += deg
                members.append(i)
                break
        else:
            groups.append(([a.cx * deg, a.cy * deg, a.r, deg], [i]))
    out = []
    for g, members in groups:
        if g[3] >= 200.0:
            hull = MultiPoint(level.segs[np.isin(level.seg_arc, members)].reshape(-1, 2)).convex_hull
            if hull.geom_type == "Polygon":
                out.append(hull)
    return out


def find_shafts(level: Level) -> list[Polygon]:
    """Cells marked with a cross: two diagonals of equal length that cut each other in the middle of a drawn cell."""
    s = level.segs[level.seg_arc < 0]
    d = s[:, 2:] - s[:, :2]
    length = np.hypot(d[:, 0], d[:, 1])
    slanted = (length >= SHAFT_DIAGONAL) & (np.abs(d[:, 0]) > 0.15 * length) & (np.abs(d[:, 1]) > 0.15 * length)
    s, d, length = s[slanted], d[slanted], length[slanted]
    if len(s) < 2:
        return []
    mid = (s[:, :2] + s[:, 2:]) / 2.0
    tree = cKDTree(mid)
    lines = shapely.linestrings(level.segs[~level.seg_dashed].reshape(-1, 2, 2))
    drawn = shapely.STRtree(lines)
    out = []
    for i, j in sorted(tree.query_pairs(0.03 * float(length.max()))):
        if abs(length[i] - length[j]) > 0.03 * length[i] or math.hypot(*(mid[i] - mid[j])) > 0.03 * length[i]:
            continue
        if abs(d[i, 0] * d[j, 1] - d[i, 1] * d[j, 0]) < 0.2 * length[i] * length[j]:
            continue                                 # parallel: one line drawn twice
        cell = MultiPoint([s[i, :2], s[i, 2:], s[j, :2], s[j, 2:]]).convex_hull
        if cell.geom_type != "Polygon":
            continue
        edge = cell.boundary                         # a cross with no cell drawn round it marks something else
        sides = shapely.union_all(lines[drawn.query(edge, predicate="dwithin", distance=1.0)]).buffer(1.0)
        if edge.intersection(sides).length >= SHAFT_DRAWN * edge.length:
            out.append(cell)
    return out


def find_panels(level: Level, arr: Arrangement, door_symbol: Callable[[Face], bool], stairs: BaseGeometry) -> list[int]:
    """Thin closed strips drawn inside a room, with the small posts between them: sliding doors and screens.

    A door leaf of a swing door is a door symbol, not a panel. Dashed lines show what is overhead.
    """
    dashed = level.segs[level.seg_dashed]
    overhead = shapely.union_all(shapely.linestrings(dashed.reshape(-1, 2, 2))).buffer(1.0) if len(dashed) else None
    out: list[int] = []
    for f in arr.faces:
        if f.kind != "room" or not PANEL[0] <= f.thick <= PANEL[1] or f.length < 300.0 or not _is_closed(f):
            continue
        if door_symbol(f) or stairs.intersects(f.poly):
            continue
        if overhead is not None and f.poly.exterior.intersection(overhead).length > 0.3 * f.perim:
            continue
        out.append(f.id)
    strips = set(out)
    for f in arr.faces:                              # the posts between them
        if f.kind == "room" and f.id not in strips and SLIVER <= f.inr and f.length <= 250.0 and _is_closed(f) \
                and any(nb in strips and shared >= 20.0 for nb, (shared, _) in f.nbrs.items()):
            out.append(f.id)
    return out


# ------------------------------------------------------------------ windows

def _is_closed(f: Face) -> bool:
    return f.gap <= max(1.0, 0.005 * f.perim)


def find_windows(arr: Arrangement, walls: set[int]) -> list[Window]:
    faces = arr.faces
    thin = {i for i in walls if faces[i].thick <= JOINERY}
    groups: list[tuple[list[int], list[int]]] = []
    seen: set[int] = set()
    for i in sorted(thin):
        if i in seen:
            continue
        comp, stack = [], [i]
        seen.add(i)
        while stack:
            j = stack.pop()
            comp.append(j)
            for nb, (shared, _) in faces[j].nbrs.items():
                if nb in thin and nb not in seen and shared >= 5.0:
                    seen.add(nb)
                    stack.append(nb)
        strips = [j for j in comp if faces[j].length >= STRIP]
        if not strips:
            continue
        side_by_side = any(faces[a].nbrs.get(b, (0.0, 0.0))[0] >= 0.8 * min(faces[a].length, faces[b].length)
                           for a in strips for b in strips if a < b)
        if len(comp) >= 3 or side_by_side:
            groups.append((sorted(comp), strips))
            continue
        for j in strips:
            # A lone strip is glass when it lies in the face of a wall: both ends run into wall, one long
            # side is wall and the other is air. With air on both long sides it is a thin partition, and
            # with air at its ends it is a post that crosses the wall (a mullion beside a door).
            f = faces[j]
            if not (f.thick >= 20.0 and f.length <= 4000.0):
                continue
            fr = Frame(tuple(np.asarray(f.poly.exterior.coords[:-1]).mean(axis=0)), f.axis)
            s0, s1, n0, n1 = fr.extent(f.poly)

            def is_wall(s: float, n: float, fr: Frame = fr) -> bool:
                g = arr.at(*fr.point(s, n))
                return g is not None and g.id in walls

            sides = [sum(is_wall(s0 + (s1 - s0) * t, n) for t in (0.25, 0.5, 0.75)) for n in (n1 + 2.0, n0 - 2.0)]
            ends = is_wall(s0 - 2.0, (n0 + n1) / 2.0) and is_wall(s1 + 2.0, (n0 + n1) / 2.0)
            if ends and max(sides) >= 2 and min(sides) <= 1:
                groups.append(([j], [j]))

    out: list[Window] = []
    for comp, strips in groups:
        lead = faces[max(strips, key=lambda j: faces[j].length)]
        fr = Frame(tuple(np.asarray(lead.poly.exterior.coords).mean(axis=0)), lead.axis)
        ext = np.array([fr.extent(faces[j].poly) for j in comp])
        s0, s1, n0, n1 = float(ext[:, 0].min()), float(ext[:, 1].max()), float(ext[:, 2].min()), float(ext[:, 3].max())
        if s1 - s0 < 300.0:
            continue
        w = Window(len(out), fr, comp, strips, (s0, s1), (n0, n1))
        # niche: closed faces reached from the joinery that lie between the two jamb lines. A room polygon
        # drawn into the niche does not change that: the face is still closed by drawn lines.
        reach, stack = set(comp), list(comp)
        while stack:
            j = stack.pop()
            for nb, (shared, _) in faces[j].nbrs.items():
                g = faces[nb]
                if nb in reach or nb in thin or shared < 5.0:
                    continue
                if not (nb in walls or (g.room >= 0 and _is_closed(g) and g.inr <= SPACE_INR)):
                    continue
                a0, a1, c0, c1 = fr.extent(g.poly)
                if (a0 >= s0 - NICHE_MARGIN and a1 <= s1 + NICHE_MARGIN and a1 - a0 >= 0.6 * (s1 - s0)
                        and c0 >= n1 - MAX_WALL and c1 <= n0 + MAX_WALL):
                    reach.add(nb)
                    stack.append(nb)
        w.niche = sorted(reach - set(comp))
        w.bay = unary_union([faces[j].poly for j in comp + w.niche])
        out.append(w)
    return out


def cut_through(arr: Arrangement, w: Window, walls: set[int]) -> None:
    """Cut the bay through wall mass that the drawing shows unbroken behind or in front of the window."""
    probe = Probe(arr, ("wall",))
    fr = w.frame
    s0, s1, b0, b1 = fr.extent(w.bay)
    for side, start, sign in ((1, b1, 1.0), (-1, b0, -1.0)):
        d = (fr.n[0] * sign, fr.n[1] * sign)
        depths = [probe.depth(fr.point(s0 + (s1 - s0) * t, start + sign * 0.5), d, MAX_WALL) for t in (0.15, 0.35, 0.5, 0.65, 0.85)]
        hit = sorted(x for x in depths if x > 5.0)
        if len(hit) < 4 or hit[-1] - hit[0] > 3.0 or hit[0] >= MAX_WALL - 1.0:
            continue
        depth = hit[len(hit) // 2] + 0.5
        lo, hi = (start, start + depth) if side > 0 else (start - depth, start)
        cut = fr.rect(s0, s1, lo, hi)
        for f in arr.near(cut):
            if f.id in walls and f.kind == "wall":
                rest = f.mass.difference(cut)
                if rest.area < 0.02 * f.area:
                    f.kind, f.why, f.solid = "niche", f"window {w.id}", None
                    w.niche.append(f.id)
                elif rest.area < f.mass.area - 1.0:
                    f.solid, f.why = _areal(rest), f"cut for window {w.id}"
        # the cut starts at the extent of the bay, which is not its edge to the last fraction of a millimetre
        w.bay = unary_union([w.bay, cut]).buffer(SEAM, join_style="mitre").buffer(-SEAM, join_style="mitre")
        w.carved += depth
        if side > 0:
            b1 += depth
        else:
            b0 -= depth


# ------------------------------------------------------------------ doors

def rough_opening(arr: Arrangement, door: Door) -> None:
    """Wall end to wall end along the door, and the wall thickness across it, measured with probes."""
    faces = arr.faces
    fr = door.frame
    lf = door.leaves[0]
    n_mid = lf.body * lf.thick / 2.0                 # the closed leaf lies beside the hinge line
    s0, s1 = door.s
    zone = fr.rect(s0 - 140.0, s1 + 140.0, n_mid - 160.0, n_mid + 160.0)
    for f in arr.near(zone):
        if f.kind not in ("wall", "thin") or f.length > JAMB_LEN or f.thick > JAMB_THICK:
            continue
        a0, a1, c0, c1 = fr.extent(f.poly)
        at_left, at_right = a1 <= s0 + 15.0 and a0 >= s0 - 140.0, a0 >= s1 - 15.0 and a1 <= s1 + 140.0
        if (at_left or at_right) and c0 >= n_mid - 160.0 and c1 <= n_mid + 160.0:
            door.jambs.append(f.id)
    skip = frozenset(door.jambs)
    probe = Probe(arr, (*SOLID, "niche", "frame"))   # a window bay beside the door is part of the wall
    ends = []
    for sign, s_end in ((-1.0, s0), (1.0, s1)):
        d = (fr.u[0] * sign, fr.u[1] * sign)
        jamb_reach = [(fr.extent(faces[j].poly)[0] if sign < 0 else fr.extent(faces[j].poly)[1]) for j in door.jambs
                      if (fr.extent(faces[j].poly)[1] <= s0 + 15.0) == (sign < 0)]
        hit = probe.first_hit(fr.point(s_end - sign * 1.0, n_mid), d, 141.0, skip)
        wall = s_end - sign * 1.0 + sign * hit if hit is not None else None
        if jamb_reach:
            end = min(jamb_reach) if sign < 0 else max(jamb_reach)
            if wall is not None and abs(wall - end) > 25.0:
                door.notes.append(f"frame and wall end differ by {abs(wall - end):.0f} mm")
        else:
            end = wall if wall is not None else s_end
        ends.append(end)
    sl, sr = ends

    def span_at(s: float) -> tuple[float, float] | None:
        """Extent across the wall of the wall mass at s along the door, if there is any near the leaf line."""
        p = fr.point(s, n_mid)
        up = probe.intervals(p, fr.n, MAX_WALL, skip)
        down = probe.intervals(p, (-fr.n[0], -fr.n[1]), MAX_WALL, skip)
        if up and up[0][0] <= 0.5 or down and down[0][0] <= 0.5:          # the leaf line runs into wall mass here
            hi = up[0][1] if up and up[0][0] <= 0.5 else 0.0
            lo = -down[0][1] if down and down[0][0] <= 0.5 else 0.0
        else:                                                               # the wall end is beside the leaf line
            near = [(a, b, 1.0) for a, b in up if a <= 150.0] + [(a, b, -1.0) for a, b in down if a <= 150.0]
            if not near:
                return None
            a, b, sg = min(near)
            lo, hi = min(sg * a, sg * b), max(sg * a, sg * b)
        return (n_mid + lo, n_mid + hi) if hi - lo < MAX_WALL - 1.0 else None

    # The wall thickness on each side of the door: measured a little way into the wall, where frames and
    # reveals no longer blur it, and right at the wall end. Of these, the two sides' values that agree best.
    cand = [[sp for off in (100.0, 3.0) if (sp := span_at(s_end + sign * off)) is not None]
            for sign, s_end in ((-1.0, sl), (1.0, sr))]
    if cand[0] and cand[1]:
        real = list(min(((a, b) for a in cand[0] for b in cand[1]),
                        key=lambda ab: round(abs(ab[0][0] - ab[1][0]) + abs(ab[0][1] - ab[1][1]))))
    else:
        real = (cand[0] or cand[1])[:1]
    if real:
        lo, hi = max(a for a, _ in real), min(b for _, b in real)
        if hi - lo < 30.0:                           # the wall ends do not overlap across: take the thinner one
            lo, hi = min(real, key=lambda ab: ab[1] - ab[0])
    elif door.jambs:
        ext = np.array([fr.extent(faces[j].poly) for j in door.jambs])
        lo, hi = float(ext[:, 2].min()), float(ext[:, 3].max())
    else:
        lo, hi = min(0.0, lf.body * lf.thick), max(0.0, lf.body * lf.thick)
        door.notes.append("no wall end and no frame found: opening as deep as the leaf")
    door.rough = fr.rect(sl, sr, lo, hi)
    for j in door.jambs:
        faces[j].kind, faces[j].why = "jamb", f"door {door.id}"


def _areal(geom: BaseGeometry) -> BaseGeometry:
    """The part of a geometry that has an area. A cut along an edge of a face leaves a line or a point of it,
    and GEOS cannot overlay a geometry of lines and areas."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    return unary_union([g for g in getattr(geom, "geoms", ()) if g.geom_type in ("Polygon", "MultiPolygon")])


# ------------------------------------------------------------------ openings without a door

def find_openings(arr: Arrangement, taken: BaseGeometry, panels: BaseGeometry | None = None) -> list[Polygon]:
    """Gaps in a wall where no door is drawn: two wall ends of about the same thickness that face each other.

    `taken` is what doors and windows already account for. The result is the plan of each gap, wall end to
    wall end. Thinner walls are handled first, so a gap between unequal walls takes the thinner one's width.
    A gap wider than a door counts only when `panels`, the sliding doors drawn on their own, stand in it.
    """
    solids = [f.mass for f in arr.faces if f.kind in SOLID and not f.mass.is_empty]
    if not solids:
        return []
    mass = unary_union(solids).simplify(0.05)        # drops the vertices neighbouring faces leave on a straight edge
    ends: list[tuple[float, np.ndarray, np.ndarray]] = []
    for poly in getattr(mass, "geoms", [mass]):
        poly = orient(poly, 1.0)                     # wall mass on the left of every edge
        for ring in (poly.exterior, *poly.interiors):
            c = np.asarray(ring.coords)[:-1]
            m = len(c)
            for i in range(m):
                a, b = c[i], c[(i + 1) % m]
                e, before, after = b - a, a - c[i - 1], c[(i + 2) % m] - b
                t, l0, l1 = float(np.hypot(*e)), float(np.hypot(*before)), float(np.hypot(*after))
                if not (JOINERY <= t <= MAX_WALL and l0 >= 50.0 and l1 >= 50.0):
                    continue
                if before[0] * e[1] - before[1] * e[0] <= 0 or e[0] * after[1] - e[1] * after[0] <= 0:
                    continue                         # not a convex end
                if abs(before @ e) > 0.2 * l0 * t or abs(after @ e) > 0.2 * l1 * t:
                    continue                         # not square to the wall faces
                ends.append((t, a, b))
    ends.sort(key=lambda x: x[0])
    probe = Probe(arr)
    out: list[Polygon] = []
    reach = SPAN if panels is not None else OPENING[1]
    for t, a, b in ends:
        u = (b - a) / t
        n = np.array([u[1], -u[0]])                  # out of the wall
        probes = [probe.first_hit(tuple(a + (b - a) * f + n * 0.5), tuple(n), reach + 5.0) for f in (0.1, 0.5, 0.9)]
        hits = [h for h in probes if h is not None]
        if len(hits) < len(probes) or max(hits) - min(hits) > 2.0:
            continue
        w = hits[1] + 0.5
        if w < OPENING[0]:
            continue
        if w > OPENING[1]:                           # too wide for a plain opening: only with panels standing in it
            span = Polygon([tuple(a), tuple(b), tuple(b + n * w), tuple(a + n * w)])
            if panels is None or panels.intersection(span).area < 0.3 * w * PANEL[0]:
                continue
        mid = (a + b) / 2.0
        beside = [probe.first_hit(tuple(mid + u * side * (t / 2.0 + 70.0) + n * 0.5), tuple(n), w + 5.0) for side in (-1.0, 1.0)]
        if any(h is not None and h >= w - 6.0 for h in beside):
            continue                                 # the far side is the face of a wall, not the end of one
        rect = Polygon([tuple(a), tuple(b), tuple(b + n * w), tuple(a + n * w)])
        if taken.intersection(rect).area > 0.02 * rect.area or any(o.contains(rect.centroid) for o in out):
            continue
        if sum(f.mass.intersection(rect).area for f in arr.near(rect) if f.kind in SOLID) > 100.0:
            continue
        out.append(rect)
    return out


# ------------------------------------------------------------------ balconies

def is_exterior(gross: BaseGeometry, fr: Frame, hole: BaseGeometry) -> bool:
    """True when one side of a door or window lies outside the gross outline."""
    s0, s1, n0, n1 = fr.extent(hole)
    mid = (s0 + s1) / 2.0
    return gross.contains(Point(fr.point(mid, n1 + 150.0))) != gross.contains(Point(fr.point(mid, n0 - 150.0)))


def find_balconies(arr: Arrangement, doors: list[Door], door_symbol: Callable[[Face], bool], gross: BaseGeometry,
                   notes: list[Note]) -> list[Balcony]:
    """A space outside the gross outline that an exterior door opens onto and a balustrade surrounds.

    The balustrade is the closed strips around the space, each up to `BALUSTRADE` wide, with open air
    beyond them. A closed outside area without one is paving; one with rooms all round, or larger than
    `BALCONY_MAX`, belongs to another building. Neither is modelled; each is reported.
    """
    faces = arr.faces
    out: list[Balcony] = []
    seen: set[int] = set()
    for d in doors:
        if not d.exterior:
            continue
        floors = [f for f in arr.near(d.rough.buffer(60.0))
                  if f.kind == "outside" and _is_closed(f) and f.inr > 300.0 and not door_symbol(f)]
        if not floors:
            continue
        main = max(floors, key=lambda f: f.area)
        if main.id in seen:
            continue
        seen.add(main.id)
        c = d.rough.centroid
        where = f"door near ({c.x:.0f}, {c.y:.0f}) opens onto a closed outside area of {main.area / 1e6:.0f} m2"
        if main.area > BALCONY_MAX:
            notes.append(Note(f"{where}: too large for a balcony, not modelled", at=(c.x, c.y)))
            continue
        near = main.poly.buffer(300.0)
        rail: list[int] = []
        swing: list[int] = []
        visited, stack = {main.id}, [main.id]
        while stack:
            j = stack.pop()
            for nb, (shared, _) in faces[j].nbrs.items():
                g = faces[nb]
                if nb in visited or shared < 5.0 or g.kind != "outside":
                    continue
                if door_symbol(g):                  # the leaf as drawn and the area it sweeps
                    swing.append(nb)
                elif near.contains(g.poly) and _is_closed(g) and 2.0 * g.inr <= BALUSTRADE:
                    rail.append(nb)
                else:
                    continue
                visited.add(nb)
                stack.append(nb)
        if not rail or sum(faces[j].free for j in [main.id, *rail]) < 0.25 * main.perim:
            notes.append(Note(f"{where} with no balustrade round it: paving or another building, not modelled", at=(c.x, c.y)))
            continue
        floor = unary_union([faces[j].poly for j in [main.id, *swing, *rail]]).buffer(1.0).buffer(-1.0).difference(gross)
        floor = max(getattr(floor, "geoms", [floor]), key=lambda g: g.area)
        main.kind, main.why = "balcony", f"outside, closed by drawn lines, door {d.id} opens onto it"
        for j in swing:
            faces[j].kind, faces[j].why = "swing", "door symbol"
        for j in rail:
            faces[j].kind, faces[j].why = "rail", "closed strip around a balcony"
        out.append(Balcony(Polygon(floor.exterior), unary_union([faces[j].poly for j in rail]) if rail else Polygon(), d.id))
    return out


# ------------------------------------------------------------------ the classification

def classify(project: Project, level: Level, arr: Arrangement, stair_areas: list[BaseGeometry] | None = None) -> Classified:
    faces = arr.faces
    notes: list[Note] = []
    leaves = find_leaves(level)
    swings = [swing_region(level, lf) for lf in leaves]
    swing_tree = shapely.STRtree(swings) if swings else None
    stairs = unary_union(stair_areas) if stair_areas else None

    def in_swing(f: Face) -> bool:
        if swing_tree is None:
            return False
        return any(swings[int(j)].buffer(1.5).intersection(f.poly).area >= 0.6 * f.area
                   for j in swing_tree.query(f.poly, predicate="intersects"))

    def swung_through(f: Face) -> bool:
        """A door cannot swing through a wall: a face that holds most of a swing is the opening, drawn open to it."""
        if swing_tree is None:
            return False
        return any(swings[int(j)].intersection(f.poly).area >= 0.5 * swings[int(j)].area
                   for j in swing_tree.query(f.poly, predicate="intersects"))

    def has_leaf(f: Face) -> bool:
        return any(f.poly.distance(Point(lf.hinge[0] + lf.u[0] * lf.r / 2, lf.hinge[1] + lf.u[1] * lf.r / 2)) <= 60.0 for lf in leaves)

    walls: set[int] = set()
    for f in faces:
        width = 2.0 * f.inr
        if f.room >= 0:
            f.kind = "room"
        elif f.inr < SLIVER:
            f.kind = "sliver"
        elif not f.in_gross:
            # walls only exist inside the gross outline, except a wall that bounds one of our rooms
            beside_room = sum(shared for nb, (shared, _) in f.nbrs.items() if faces[nb].room >= 0)
            if _is_closed(f) and f.inr <= SPACE_INR and beside_room >= 300.0:
                f.kind, f.why = "party", f"outside the gross outline, but {beside_room:.0f} mm of it bounds a room"
            else:
                f.kind = "outside"
        elif stairs is not None and stairs.intersection(f.poly).area >= 0.8 * f.area:
            f.kind, f.why = "stair", "inside a stair"
        elif in_swing(f):
            f.kind, f.why = "swing", "door symbol"
        elif f.inr <= SPACE_INR and swung_through(f):
            f.kind, f.why = "gap", "a door swings through it"
        elif _is_closed(f):
            if f.inr > SPACE_INR:
                f.kind, f.why = "space", "closed by drawn lines, no room polygon"
            else:
                f.kind = "wall"
                walls.add(f.id)
        elif f.inr <= SPACE_INR and f.cov >= 0.8 and f.gap <= max(1.2 * width, 60.0):
            f.kind, f.why = "wall", f"end not drawn ({f.gap:.0f} mm of outline missing)"
            walls.add(f.id)
        else:
            f.kind = "gap"

    windows = find_windows(arr, walls)
    for w in windows:
        for j in w.faces:
            faces[j].kind, faces[j].why = "frame", f"window {w.id}"
        for j in w.niche:
            if faces[j].room < 0:
                faces[j].kind, faces[j].why = "niche", f"window {w.id}"
    for w in windows:
        cut_through(arr, w, walls)
    for i in walls:
        f = faces[i]
        if f.kind == "wall" and f.thick < LINE_PAIR:
            f.kind, f.why = "thin", f"{f.thick:.0f} mm thick: a doubled line, not a wall"

    # gaps that are wall after all
    for f in faces:
        width = 2.0 * f.inr
        if f.kind != "gap" or width < JOINERY or f.inr > SPACE_INR or has_leaf(f):
            continue
        beside = {faces[nb].room for nb, (shared, sb) in f.nbrs.items() if faces[nb].room >= 0 and shared - sb >= 3.0 * width}
        if not beside:
            continue
        if f.black_run >= 3.0 * width and f.black_run >= 0.6 * f.open_run and f.open_run >= 3.0 * width:
            f.kind, f.why = "implied", "one face drawn, the other given only by the room outline"
        elif len(beside) >= 2 and f.length > 2500.0 and f.length >= 3.0 * f.thick:
            f.kind, f.why = "implied", f"{f.length:.0f} mm strip between two rooms, no door, no face drawn"
        if f.kind == "implied":
            c = f.poly.representative_point()
            notes.append(Note(f"implied wall near ({c.x:.0f}, {c.y:.0f}), about {f.perim / 2:.0f} x {width:.0f} mm: {f.why}",
                              at=(c.x, c.y)))

    for key, x, y in project.solid_points:
        f = arr.at(x, y) if key == level.key else None
        if f is not None:
            f.kind, f.why = "wall", "set solid in the project file"
    for key, x, y in project.open_points:
        f = arr.at(x, y) if key == level.key else None
        if f is not None:
            f.kind, f.why, f.solid = "gap", "set open in the project file", None
    shafts = find_shafts(level)
    for cell in shafts:                              # nothing stands in a cell marked with a cross
        for f in arr.near(cell):
            part = f.poly.intersection(cell).area
            if f.kind != "sliver" and part >= 0.8 * f.area:
                f.kind, f.why, f.solid = "shaft", "cell marked with a cross", None
            elif f.kind in SOLID and part > 1.0:
                f.solid, f.why = _areal(f.mass.difference(cell)), "cut for a shaft"

    doors = pair_leaves(leaves)
    gross = level.gross_all
    for d in doors:
        rough_opening(arr, d)
        d.exterior = is_exterior(gross, d.frame, d.rough)
        cut = 0.0
        for f in arr.near(d.rough):                  # a wall drawn unbroken across the door is cut through
            if f.kind in SOLID:
                rest = f.mass.difference(d.rough)
                cut += f.mass.area - rest.area
                if rest.area < 0.02 * f.area:
                    f.kind, f.why, f.solid = "gap", f"door {d.id}", None
                elif rest.area < f.mass.area - 1.0:
                    f.solid, f.why = _areal(rest), f"cut for door {d.id}"
        if cut > 0.05 * d.rough.area:
            d.notes.append(f"the wall is drawn across this door: {cut / 1e6:.2f} m2 of plan cut out of it")
    for w in windows:
        w.exterior = is_exterior(gross, w.frame, w.bay)

    taken = unary_union([d.rough for d in doors] + [w.bay for w in windows])

    def door_symbol(f: Face) -> bool:
        return in_swing(f) or swung_through(f)

    # thin strips where a stair stands are its strings and guards, not panels
    panels = find_panels(level, arr, door_symbol, unary_union([stair_area(s) for s in project.stairs]))
    for j in panels:
        faces[j].kind, faces[j].why = "panel", "thin closed strip inside a room: a sliding door or a screen"
    openings = find_openings(arr, taken, unary_union([faces[j].poly for j in panels]) if panels else None)
    balconies = find_balconies(arr, doors, door_symbol, gross, notes)
    for d in doors:
        notes.extend(Note(f"door near ({d.frame.origin[0]:.0f}, {d.frame.origin[1]:.0f}): {n}", at=d.frame.origin)
                     for n in d.notes)
    return Classified(level, arr, leaves, windows, doors, find_columns(level), openings, balconies, swings,
                      [faces[j].poly for j in panels], shafts, notes)


def summary(c: Classified) -> dict[str, tuple[int, float]]:
    out: dict[str, list[float]] = {}
    for f in c.arr.faces:
        n = out.setdefault(f.kind, [0, 0.0])
        n[0] += 1
        n[1] += f.area / 1e6
    return {k: (int(v[0]), round(v[1], 2)) for k, v in sorted(out.items())}
