"""Read one storey's drawing into plain geometry. No interpretation happens here.

The project file says where the reader finds what (`project.Input`). The defaults are those of area
drawings for Norwegian facility management:

- layer ``0``           all building linework
- layer ``Area_NTA``    one closed polyline per room, plus a block reference
                        with attributes ROOMNUMBER / ROOMNAME (/ WING)
- layer ``Area_BTA``    gross outline of each separate building block
- layer ``Area_BRA``    inner face of the exterior walls of each block

Building lines are read from LWPOLYLINE, POLYLINE (2D), LINE, ARC and CIRCLE. All of them become
polylines with bulges. What else lies on a layer that the reader uses is counted and reported.
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

from .errors import InputError
from .project import UNITS, Input

INSUNITS = {4: "mm", 5: "cm", 6: "m"}       # $INSUNITS of the DXF header, for the units this reader takes


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


@dataclass
class Label:
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


def document(dxf: Path):
    """The DXF document, with a message for a person when it cannot be read."""
    try:
        return ezdxf.readfile(dxf)
    except (OSError, ezdxf.DXFError) as e:
        raise InputError(f"{dxf.name} cannot be read as DXF: {e}") from e


def read(key: str, dxf: Path, inp: Input) -> Drawing:
    doc = document(dxf)
    k = UNITS[inp.unit]
    d = Drawing(key)
    said = INSUNITS.get(doc.header.get("$INSUNITS", 0))
    if said and said != inp.unit:
        d.notes.append(f"the drawing says that its unit is {said}; it is read in {inp.unit}, as the project file says")
    buckets = {layer.casefold(): bucket
               for bucket, layers in ((d.lines, inp.lines), (d.rooms, inp.rooms), (d.gross, inp.gross), (d.inner, inp.inner))
               for layer in layers}
    number, name, wing = inp.room_number.upper(), inp.room_name.upper(), inp.room_wing.upper()
    unread: Counter[tuple[str, str]] = Counter()     # on a layer that the reader uses, of a kind it does not read
    layers: Counter[str] = Counter()
    for e in doc.modelspace():
        t, layer = e.dxftype(), e.dxf.layer
        layers[layer] += 1
        bucket = buckets.get(layer.casefold())
        if bucket is None:
            continue
        linetype = e.dxf.get("linetype", "BYLAYER")
        if t == "LWPOLYLINE":
            bucket.append(Poly([(x * k, y * k, b) for x, y, b in e.get_points("xyb")], bool(e.closed), e.dxf.handle, linetype))
        elif t == "POLYLINE" and e.is_2d_polyline:
            pts = [(v.dxf.location.x * k, v.dxf.location.y * k, v.dxf.get("bulge", 0.0)) for v in e.vertices]
            bucket.append(Poly(pts, bool(e.is_closed), e.dxf.handle, linetype))
        elif t == "LINE" and (bucket is d.lines or bucket is d.rooms):
            bucket.append(Poly([(e.dxf.start.x * k, e.dxf.start.y * k, 0.0), (e.dxf.end.x * k, e.dxf.end.y * k, 0.0)],
                               False, e.dxf.handle, linetype))
        elif t == "ARC" and bucket is d.lines:
            sweep = math.radians(e.dxf.end_angle - e.dxf.start_angle) % math.tau or math.tau
            bulge = math.tan(sweep / 4.0) * (-1.0 if e.dxf.extrusion.z < 0 else 1.0)    # a mirrored arc turns the other way
            p, q = e.start_point, e.end_point
            bucket.append(Poly([(p.x * k, p.y * k, bulge), (q.x * k, q.y * k, 0.0)], False, e.dxf.handle, linetype))
        elif t == "CIRCLE" and bucket is d.lines:
            c, r = e.ocs().to_wcs(e.dxf.center), e.dxf.radius
            bucket.append(Poly([((c.x - r) * k, c.y * k, 1.0), ((c.x + r) * k, c.y * k, 1.0)], True, e.dxf.handle, linetype))
        elif t == "INSERT" and bucket is d.rooms:
            a = {x.dxf.tag.upper(): (x.dxf.text or "").strip() for x in e.attribs}
            d.labels.append(Label(e.dxf.insert.x * k, e.dxf.insert.y * k, a.get(number, ""), a.get(name, ""),
                                  a.get(wing, ""), e.dxf.handle))
        else:
            unread[(t, layer)] += 1
    for what, bucket, wanted in (("building lines", d.lines, inp.lines), ("gross outline", d.gross, inp.gross)):
        if not bucket:
            found = ", ".join(f"{layer} ({n})" for layer, n in layers.most_common(12)) or "none"
            raise InputError(f'storey {key}: {dxf.name} holds no {what} on layer {" or ".join(wanted)}. Its layers, with '
                             f"the number of entities on each: {found}. Name the layers in [input] of the project file.")
    if unread:
        kinds = ", ".join(f"{n} {t} on layer {layer}" for (t, layer), n in unread.most_common())
        d.notes.append(f"not read, because the reader does not take this kind of entity on that layer: {kinds}")
    return d


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
