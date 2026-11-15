"""Read one storey's drawing into plain geometry. No interpretation happens here.

Drawing convention this reader expects (FDV area drawings as produced for
Norwegian facility management):

- layer ``0``           all building linework, as polylines (arcs as bulges)
- layer ``Area_NTA``    one closed polyline per room, plus a block reference
                        with attributes ROOMNUMBER / ROOMNAME (/ WING)
- layer ``Area_BTA``    gross outline of each separate building block
- layer ``Area_BRA``    inner face of the exterior walls of each block
"""
from __future__ import annotations

import math
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
import numpy as np

LAYER_LINES = "0"
LAYER_ROOMS = "Area_NTA"
LAYER_GROSS = "Area_BTA"
LAYER_INNER = "Area_BRA"


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
    lines: list[Poly] = field(default_factory=list)     # layer 0
    rooms: list[Poly] = field(default_factory=list)     # Area_NTA polylines (rooms and separators)
    gross: list[Poly] = field(default_factory=list)     # Area_BTA
    inner: list[Poly] = field(default_factory=list)     # Area_BRA
    labels: list[Label] = field(default_factory=list)
    other: dict[str, int] = field(default_factory=dict)  # entities this reader does not use, by "TYPE@layer"


def dwg_to_dxf(dwg: Path, dxf: Path) -> Path:
    """Convert with LibreDWG's dwg2dxf. The result is cached next to the other outputs."""
    if dxf.exists() and dxf.stat().st_mtime >= dwg.stat().st_mtime:
        return dxf
    exe = shutil.which("dwg2dxf")
    if not exe:
        raise RuntimeError("dwg2dxf is not on PATH. It is part of LibreDWG: https://www.gnu.org/software/libredwg/")
    dxf.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "-y", "-o", str(dxf), str(dwg)], capture_output=True, text=True, check=False)
    if not dxf.exists() or dxf.stat().st_size == 0:
        raise RuntimeError(f"dwg2dxf failed for {dwg.name}: {r.stderr[-400:]}")
    return dxf


def read(key: str, dxf: Path) -> Drawing:
    doc = ezdxf.readfile(dxf)
    d = Drawing(key)
    for e in doc.modelspace():
        t, layer = e.dxftype(), e.dxf.layer
        if t == "LWPOLYLINE":
            p = Poly([(x, y, b) for x, y, b in e.get_points("xyb")], bool(e.closed), e.dxf.handle,
                     e.dxf.get("linetype", "BYLAYER"))
            target = {LAYER_LINES: d.lines, LAYER_ROOMS: d.rooms, LAYER_GROSS: d.gross, LAYER_INNER: d.inner}.get(layer)
            if target is not None:
                target.append(p)
                continue
        elif t == "LINE" and layer in (LAYER_LINES, LAYER_ROOMS):
            p = Poly([(e.dxf.start.x, e.dxf.start.y, 0.0), (e.dxf.end.x, e.dxf.end.y, 0.0)], False, e.dxf.handle,
                     e.dxf.get("linetype", "BYLAYER"))
            (d.lines if layer == LAYER_LINES else d.rooms).append(p)
            continue
        elif t == "INSERT" and layer == LAYER_ROOMS:
            a = {x.dxf.tag.upper(): (x.dxf.text or "").strip() for x in e.attribs}
            d.labels.append(Label(e.dxf.insert.x, e.dxf.insert.y, a.get("ROOMNUMBER", ""), a.get("ROOMNAME", ""),
                                  a.get("WING", ""), e.dxf.handle))
            continue
        k = f"{t}@{layer}"
        d.other[k] = d.other.get(k, 0) + 1
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
