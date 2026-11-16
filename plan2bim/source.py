"""Read the drawings of one storey into plain geometry.

The project file says where the reader finds what (`project.Input`). The defaults take two conventions
of Norwegian drawings for facility management:

- layer ``Area_NTA`` or ``862 - Rom NTA``       one closed polyline per room. A room has its number and its
                                                name in a block reference on that layer, with the attributes
                                                ROOMNUMBER / ROOMNAME (/ WING), or in a plain text on the
                                                layers ``F29-T1-Nr`` and ``F29-T2-Navn``.
- layer ``Area_BTA`` or ``861 - Etasje BTA``    gross outline of each separate building block
- layer ``Area_BRA``                            inner face of the exterior walls of each block
- every other layer                             building linework

A storey can have several drawings that share their coordinates, for example an area drawing with the
outlines and the plan drawing that it references. They are read as one drawing.

Building lines are read from LWPOLYLINE, POLYLINE (2D), LINE, ARC and CIRCLE. All of them become
polylines with bulges. What else lies on a layer that the reader uses is counted and reported, apart from
texts and dimensions among the building lines. The only interpretation here: a building line far outside
every gross outline, or straight through a gross outline from outside to outside, is not read
(`_building_lines`).
"""
from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
import numpy as np
import shapely
from ezdxf.lldxf.encoding import decode_dxf_unicode, has_dxf_unicode
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .errors import InputError
from .project import UNITS, Input, content_of

INSUNITS = {4: "mm", 5: "cm", 6: "m"}       # $INSUNITS of the DXF header, for the units this reader takes
ANNOTATION = frozenset({"TEXT", "MTEXT", "ATTDEF", "DIMENSION", "LEADER", "MULTILEADER", "TOLERANCE"})
FAR = 10_000.0          # mm; a line this far outside every gross outline is a frame, a legend or the site
THROUGH = 1_000.0       # mm; a straight line that ends this far outside on both sides runs through the building
AXES = 3                # lines through the building that make a layer of which they are most a layer of axes
ELSEWHERE = 0.05        # share of the lines of a drawing near the gross outline, under which it has other coordinates
LISTED = 30             # layers that a message names


@dataclass
class Arc:
    cx: float
    cy: float
    r: float
    a0: float       # start angle, radians
    sweep: float    # signed, radians (positive = counter-clockwise)

    @property
    def p0(self) -> tuple[float, float]:
        return self.cx + self.r * math.cos(self.a0), self.cy + self.r * math.sin(self.a0)

    @property
    def p1(self) -> tuple[float, float]:
        a = self.a0 + self.sweep
        return self.cx + self.r * math.cos(a), self.cy + self.r * math.sin(a)


@dataclass
class Poly:
    """A polyline as drawn: vertices with bulge, open or closed."""
    pts: list[tuple[float, float, float]]
    closed: bool
    handle: str
    linetype: str = "BYLAYER"
    layer: str = ""


@dataclass
class Label:
    """What a room is called: a label block with attributes, or a plain text with the number or the name."""
    x: float
    y: float
    number: str
    name: str
    wing: str
    handle: str


@dataclass
class Drawing:
    key: str
    lines: list[Poly] = field(default_factory=list)     # building lines
    rooms: list[Poly] = field(default_factory=list)     # room outlines and separators
    gross: list[Poly] = field(default_factory=list)     # gross outline of each block
    inner: list[Poly] = field(default_factory=list)     # inner face of the exterior walls
    labels: list[Label] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # what a person must know about this drawing


def to_dxf(drawing: Path, cache: Path) -> Path:
    """The drawing as DXF. A DXF is used as it is. A DWG is converted with LibreDWG's dwg2dxf to `cache`.

    The conversion is kept: `cache` is used again as long as the file beside it holds the hash of this DWG.
    """
    if drawing.suffix.lower() == ".dxf":
        return drawing
    stamp = cache.with_suffix(".sha256")
    with drawing.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if cache.exists() and stamp.exists() and stamp.read_text(encoding="ascii") == digest:
        return cache
    exe = shutil.which("dwg2dxf")
    if not exe:
        raise InputError(f"{drawing.name} is a DWG, and dwg2dxf is not on the PATH. Install LibreDWG "
                         "(https://www.gnu.org/software/libredwg/), or convert the drawing to DXF with another "
                         "tool and name the DXF in the project file.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    stamp.unlink(missing_ok=True)
    cache.unlink(missing_ok=True)       # what is left of another drawing is not the conversion of this one
    r = subprocess.run([exe, "-y", "-o", str(cache), str(drawing)], capture_output=True, text=True, check=False)
    if not cache.exists() or cache.stat().st_size == 0:
        raise InputError(f"dwg2dxf cannot convert {drawing.name}: {r.stderr.strip()[-400:] or 'no output'}. "
                         "Convert the drawing to DXF with another tool and name the DXF in the project file.")
    stamp.write_text(digest, encoding="ascii")
    return cache


def document(dxf: Path, name: str = ""):
    """The DXF document, with a message for a person when it cannot be read."""
    try:
        return ezdxf.readfile(dxf)
    except (OSError, ezdxf.DXFError) as e:
        raise InputError(f"{name or dxf.name} cannot be read as DXF: {e}") from e


def plain(text: str) -> str:
    """A name or a text of a DXF, with each `\\U+XXXX` of an older DXF as the character it stands for."""
    return decode_dxf_unicode(text) if has_dxf_unicode(text) else text


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f'{", ".join(names[:-1])} and {names[-1]}'


def read(key: str, files: dict[str, Path], inp: Input) -> Drawing:
    """The drawings of one storey as one drawing. `files` gives the DXF of each drawing by the name that
    a message uses for it."""
    k = UNITS[inp.unit]
    d = Drawing(key)
    several = len(files) > 1
    buckets = {"lines": d.lines, "rooms": d.rooms, "gross": d.gross, "inner": d.inner}
    number, name, wing = inp.room_number.upper(), inp.room_name.upper(), inp.room_wing.upper()
    unread: Counter[tuple[str, str]] = Counter()     # on a layer that the reader uses, of a kind it does not read
    layers: Counter[str] = Counter()
    references: list[str] = []                       # drawings that these drawings reference
    lines_of: dict[str, range] = {}                  # the building lines of each drawing, as indexes of d.lines
    for file, dxf in files.items():
        doc = document(dxf, file)
        said = INSUNITS.get(doc.header.get("$INSUNITS", 0))
        if said and said != inp.unit:
            d.notes.append(f"{file if several else 'the drawing'} says that its unit is {said}; it is read in {inp.unit}, "
                           "as the project file says")
        references += [plain(block.name) for block in doc.blocks if block.block is not None and block.block.is_xref]
        content: dict[str, str] = {}
        first = len(d.lines)
        for e in doc.modelspace():
            t, layer = e.dxftype(), plain(e.dxf.layer)
            layers[layer] += 1
            if layer not in content:
                content[layer] = content_of(inp, layer)
            what = content[layer]
            if not what:
                continue
            handle = f"{file}:{e.dxf.handle}" if several else e.dxf.handle
            if what in ("room_number_text", "room_name_text"):
                if t in ("TEXT", "MTEXT"):
                    at = e.get_placement()[1] if t == "TEXT" else e.dxf.insert
                    text = " ".join(plain(e.plain_text()).split())
                    is_number = what == "room_number_text"
                    d.labels.append(Label(at.x * k, at.y * k, text if is_number else "", "" if is_number else text, "", handle))
                else:
                    unread[(t, layer)] += 1
                continue
            bucket = buckets[what]
            linetype = e.dxf.get("linetype", "BYLAYER")
            if t == "LWPOLYLINE":
                bucket.append(Poly([(x * k, y * k, b) for x, y, b in e.get_points("xyb")], bool(e.closed), handle, linetype,
                                   layer))
            elif t == "POLYLINE" and e.is_2d_polyline:
                pts = [(v.dxf.location.x * k, v.dxf.location.y * k, v.dxf.get("bulge", 0.0)) for v in e.vertices]
                bucket.append(Poly(pts, bool(e.is_closed), handle, linetype, layer))
            elif t == "LINE" and (bucket is d.lines or bucket is d.rooms):
                bucket.append(Poly([(e.dxf.start.x * k, e.dxf.start.y * k, 0.0), (e.dxf.end.x * k, e.dxf.end.y * k, 0.0)],
                                   False, handle, linetype, layer))
            elif t == "ARC" and bucket is d.lines:
                sweep = math.radians(e.dxf.end_angle - e.dxf.start_angle) % math.tau or math.tau
                bulge = math.tan(sweep / 4.0) * (-1.0 if e.dxf.extrusion.z < 0 else 1.0)    # a mirrored arc turns the other way
                p, q = e.start_point, e.end_point
                bucket.append(Poly([(p.x * k, p.y * k, bulge), (q.x * k, q.y * k, 0.0)], False, handle, linetype, layer))
            elif t == "CIRCLE" and bucket is d.lines:
                c, r = e.ocs().to_wcs(e.dxf.center), e.dxf.radius
                bucket.append(Poly([((c.x - r) * k, c.y * k, 1.0), ((c.x + r) * k, c.y * k, 1.0)], True, handle, linetype,
                                   layer))
            elif t == "INSERT" and bucket is d.rooms:
                a = {x.dxf.tag.upper(): plain(x.dxf.text or "").strip() for x in e.attribs}
                d.labels.append(Label(e.dxf.insert.x * k, e.dxf.insert.y * k, a.get(number, ""), a.get(name, ""),
                                      a.get(wing, ""), handle))
            elif not (bucket is d.lines and t in ANNOTATION):
                unread[(t, layer)] += 1
        lines_of[file] = range(first, len(d.lines))

    names = _and(list(files))
    found = ", ".join(f"{layer} ({n})" for layer, n in layers.most_common(LISTED)) or "none"
    if len(layers) > LISTED:
        found += f", and {len(layers) - LISTED} more"
    where = (f"{'Their' if several else 'Its'} layers, with the number of entities on each: {found}. Name the layers "
             'under "Layers and unit of the drawings" on the web page, or in [input] of the project file.')
    if not d.gross:
        raise InputError(f'storey {key}: {names} {"hold" if several else "holds"} no gross outline on layer '
                         f'{" or ".join(inp.gross)}. {where} If the outlines are in a drawing of their own, give the '
                         "storey that drawing too.")
    drawn = len(d.lines)
    _building_lines(key, d, lines_of)
    if not d.lines:
        if drawn:
            raise InputError(f"storey {key}: all {drawn} building lines of {names} lie more than {FAR / 1000:.0f} m from "
                             "the gross outline. The building lines and the gross outline must have the same coordinates.")
        others = (f" {names} {'reference' if several else 'references'} {_and(sorted(set(references)))}."
                  if references else "")
        raise InputError(f'storey {key}: {names} {"hold" if several else "holds"} no building lines on layer '
                         f'{" or ".join(inp.lines)}. {where} If the walls are in a drawing of their own, give the storey '
                         f"that drawing too.{others}")
    if unread:
        kinds = ", ".join(f"{n} {t} on layer {layer}" for (t, layer), n in unread.most_common())
        d.notes.append(f"not read, because the reader does not take this kind of entity on that layer: {kinds}")
    return d


def _building_lines(key: str, d: Drawing, lines_of: dict[str, range]) -> None:
    """Take out of `d.lines` what is not a line of the building, and note it.

    A frame, a legend and the site lie far outside every gross outline. An axis line or a section line is
    straight and runs through a gross outline from outside to outside. Where most straight lines of a layer
    do so, the layer holds the axis grid, and its other lines and its circles belong to that grid too.
    Each of them is the same in the drawing of every storey, so it would also be false evidence for the
    registration.
    A drawing of which almost no line is near the gross outline has other coordinates: it stops the run.
    """
    rings = [Polygon([(x, y) for x, y, _ in g.pts]) for g in d.gross if len(g.pts) >= 3]
    gross = unary_union([p if p.is_valid else p.buffer(0) for p in rings])
    if gross.is_empty or not d.lines:
        return
    paths = np.array([LineString([(x, y) for x, y, _ in (*pl.pts, *(pl.pts[:1] if pl.closed else ()))])
                      if len(pl.pts) > 1 else LineString() for pl in d.lines])
    far = ~shapely.intersects(gross.buffer(FAR), paths)
    straight = np.array([len(pl.pts) == 2 and not pl.closed and abs(pl.pts[0][2]) < 1e-9 for pl in d.lines])
    beside = gross.buffer(THROUGH)
    ends = shapely.points([[pl.pts[0][:2], pl.pts[-1][:2]] if pl.pts else [(0.0, 0.0)] * 2 for pl in d.lines])
    through = (straight & ~far & ~shapely.intersects(beside, ends[:, 0]) & ~shapely.intersects(beside, ends[:, 1])
               & shapely.intersects(gross, paths))
    layer = np.array([pl.layer for pl in d.lines])
    for name in set(layer[through]):
        on = (layer == name) & ~far
        if through[on].sum() >= AXES and 2 * through[on].sum() > (straight & on).sum():
            through |= on
    for file, own in lines_of.items():
        if len(lines_of) > 1 and len(own) >= 20 and (~far[own.start:own.stop]).mean() < ELSEWHERE:
            raise InputError(f"storey {key}: {file} does not lie on the gross outline of the storey: "
                             f"{far[own.start:own.stop].sum()} of its {len(own)} lines are more than {FAR / 1000:.0f} m "
                             "from it. The drawings of one storey must have the same coordinates. Take the drawing out "
                             "of the storey.")
    axes = "they run straight through the building as axis lines do, or lie on a layer of such lines"
    for which, why in ((far, f"they lie more than {FAR / 1000:.0f} m outside the gross outline"), (through, axes)):
        if which.any():
            layers = Counter(pl.layer for pl, out in zip(d.lines, which, strict=True) if out)
            d.notes.append(f"{which.sum()} lines not read, because {why}: "
                           + ", ".join(f"{n} on layer {layer}" for layer, n in layers.most_common()))
    d.lines[:] = [pl for pl, out in zip(d.lines, far | through, strict=True) if not out]


# ---------------------------------------------------------------- geometry helpers

def bulge_arc(p: tuple[float, float], q: tuple[float, float], bulge: float) -> Arc:
    """The arc of a polyline segment p->q with the given bulge (tan of a quarter of the included angle)."""
    sweep = 4.0 * math.atan(bulge)
    chord = math.hypot(q[0] - p[0], q[1] - p[1])
    r = chord / (2.0 * math.sin(abs(sweep) / 2.0))
    d = r * math.cos(abs(sweep) / 2.0)
    ux, uy = (q[0] - p[0]) / chord, (q[1] - p[1]) / chord
    s = 1.0 if bulge > 0 else -1.0
    cx, cy = (p[0] + q[0]) / 2.0 - s * uy * d, (p[1] + q[1]) / 2.0 + s * ux * d
    return Arc(cx, cy, r, math.atan2(p[1] - cy, p[0] - cx), sweep)


def arc_points(a: Arc, step_deg: float) -> list[tuple[float, float]]:
    """Polyline for an arc, end points included.

    Inner vertices sit at absolute multiples of the step, measured from angle 0 of
    the circle. Two arcs on the same circle therefore share their vertices, and
    walls drawn against each other on a curve stay edge to edge.
    """
    step = math.radians(step_deg)
    lo, hi = (a.a0, a.a0 + a.sweep) if a.sweep >= 0 else (a.a0 + a.sweep, a.a0)
    k0, k1 = math.floor(lo / step + 1e-9) + 1, math.ceil(hi / step - 1e-9) - 1
    inner = [(a.cx + a.r * math.cos(k * step), a.cy + a.r * math.sin(k * step)) for k in range(k0, k1 + 1)]
    pts = [a.p0, *(inner if a.sweep >= 0 else inner[::-1]), a.p1]
    return pts


def segments(poly: Poly) -> list[tuple[tuple[float, float], tuple[float, float], float]]:
    """(p, q, bulge) for every segment of a polyline."""
    n = len(poly.pts)
    out = []
    for i in range(n if poly.closed else n - 1):
        p, q = poly.pts[i], poly.pts[(i + 1) % n]
        if math.hypot(q[0] - p[0], q[1] - p[1]) > 1e-9:
            out.append(((p[0], p[1]), (q[0], q[1]), p[2]))
    return out


def bbox(polys: list[Poly]) -> tuple[float, float, float, float]:
    a = np.array([(x, y) for p in polys for x, y, _ in p.pts])
    return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())
