"""Plots for review: the drawing with every face coloured by what the pipeline decided it is."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MPath
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .classify import Classified

COLOURS = {
    "room": (0.87, 0.93, 1.0), "wall": (0.33, 0.33, 0.33), "implied": (0.85, 0.1, 0.1), "party": (0.55, 0.3, 0.1),
    "frame": (0.1, 0.45, 0.95), "niche": (0.72, 0.6, 0.9), "jamb": (0.0, 0.6, 0.3), "gap": (1.0, 0.65, 0.2),
    "space": (0.6, 0.9, 0.6), "swing": (1.0, 0.97, 0.75), "thin": (1.0, 0.2, 0.8),
    "stair": (0.45, 0.9, 0.9), "sliver": (1.0, 0.0, 1.0), "balcony": (0.93, 0.87, 0.75), "rail": (0.6, 0.45, 0.2),
    "panel": (0.8, 0.5, 0.1), "shaft": (0.75, 0.75, 0.95),
}
LEGEND = "wall grey | implied wall red | party wall brown | window joinery blue, niche violet | door frame green | " \
         "gap orange | opening yellow | unlabelled space light green | stair cyan | doubled line pink | " \
         "sliding door ochre | shaft lilac"


def patch(geom: BaseGeometry, **kw) -> PathPatch | None:
    verts, codes = [], []
    for p in getattr(geom, "geoms", [geom]):
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        for ring in (p.exterior, *p.interiors):
            c = list(ring.coords)
            verts += c
            codes += [MPath.MOVETO] + [MPath.LINETO] * (len(c) - 2) + [MPath.CLOSEPOLY]
    return PathPatch(MPath(verts, codes), **kw) if verts else None


def draw(ax, c: Classified, bounds: tuple[float, float, float, float], *, lines: float = 0.5, dots: bool = False,
         labels: bool = True, outlines: bool = True, ids: bool = False) -> None:
    level, arr = c.level, c.arr
    view = box(*bounds)
    for f in arr.near(view):
        col = COLOURS.get(f.kind)
        if col is None:
            continue
        geom = f.mass if f.solid is not None else f.poly
        pp = patch(geom, facecolor=col, edgecolor="none", zorder=1)
        if pp is not None:
            ax.add_patch(pp)
        if f.solid is not None:                                   # the part cut out for a window
            pp = patch(f.poly.difference(f.solid), facecolor=COLOURS["niche"], edgecolor="none", zorder=1)
            if pp is not None:
                ax.add_patch(pp)
        if ids and f.kind not in ("room",):
            p = f.poly.representative_point()
            if view.contains(p):
                ax.text(p.x, p.y, f"{f.id}", fontsize=5, ha="center", va="center", zorder=9, color="w" if f.kind == "wall" else "k")
    for col_ in c.columns:
        if col_.intersects(view):
            ax.add_patch(patch(col_, facecolor=(0.2, 0.2, 0.2), edgecolor="none", zorder=2))
    s = level.segs
    m = (np.maximum(s[:, 0], s[:, 2]) >= bounds[0]) & (np.minimum(s[:, 0], s[:, 2]) <= bounds[2]) & \
        (np.maximum(s[:, 1], s[:, 3]) >= bounds[1]) & (np.minimum(s[:, 1], s[:, 3]) <= bounds[3])
    for (x1, y1, x2, y2), dashed in zip(s[m], level.seg_dashed[m], strict=True):
        ax.plot([x1, x2], [y1, y2], "-", color="r" if dashed else "k", lw=lines, zorder=3)
        if dots:
            ax.plot([x1, x2], [y1, y2], "k.", ms=2.5, zorder=4)
    for d in c.doors:
        if d.rough.intersects(view):
            x, y = d.rough.exterior.xy
            ax.plot(x, y, "-", color=(0.9, 0.0, 0.9), lw=max(1.0, lines * 2), zorder=5)
    for o in c.openings:
        if o.intersects(view):
            x, y = o.exterior.xy
            ax.plot(x, y, "-", color=(0.95, 0.75, 0.0), lw=max(1.4, lines * 2.5), zorder=5)
    for w in c.windows:
        if w.bay.intersects(view):
            for p in getattr(w.bay, "geoms", [w.bay]):
                x, y = p.exterior.xy
                ax.plot(x, y, "-", color=(0.0, 0.3, 1.0), lw=max(0.8, lines * 1.5), zorder=5)
    if outlines:
        for g in level.gross:
            x, y = g.exterior.xy
            ax.plot(x, y, ":", color="red", lw=1.2, zorder=6)
        for g in level.inner:
            x, y = g.exterior.xy
            ax.plot(x, y, "-.", color="green", lw=0.8, zorder=6)
    if labels:
        for r in level.rooms:
            p = r.poly.representative_point()
            if view.contains(p):
                ax.text(p.x, p.y, r.number or "?", fontsize=6, color="darkred", ha="center", clip_on=True, zorder=7)
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_aspect("equal")
    ax.tick_params(labelsize=6)


def tile(c: Classified, bounds: tuple[float, float, float, float], out: str | Path, *, width: float = 15.6, dpi: int = 100,
         title: str = "", **kw) -> None:
    w, h = bounds[2] - bounds[0], bounds[3] - bounds[1]
    height = width * h / w
    if height > width:
        width, height = width * width / height, width
    fig, ax = plt.subplots(figsize=(width, height))
    draw(ax, c, bounds, **kw)
    if title:
        ax.set_title(title, fontsize=8)
    fig.tight_layout()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi)
    plt.close(fig)


def overview(c: Classified, out: str | Path, margin: float = 1500.0) -> None:
    b = c.level.gross_all.bounds
    tile(c, (b[0] - margin, b[1] - margin, b[2] + margin, b[3] + margin), out, width=30.0, dpi=110, lines=0.25,
         title=f"{c.level.key}: {LEGEND}; door rough openings magenta, window bays blue outline")
