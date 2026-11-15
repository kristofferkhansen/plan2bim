"""Draw the example building: two storeys, one DXF each, as the reader of plan2bim expects them.

    uv run python example/draw.py

The building is 14 x 8 m. It has two rooms and a lift on each storey, a straight stair, a column, an
entrance on storey 1, and windows on all sides. The drawing of storey 2 has another origin than that of
storey 1, as the drawings of real storeys have.

Every entity kind that the reader takes is in the drawings: LWPOLYLINE, POLYLINE, LINE, ARC, CIRCLE,
and INSERT with attributes.
"""
from __future__ import annotations

from pathlib import Path

import ezdxf
from shapely.geometry import box
from shapely.ops import unary_union

HERE = Path(__file__).parent
W, D = 14000.0, 8000.0          # outside of the building
EXT, INT = 300.0, 100.0         # thickness of the exterior walls and of the partitions
LINES, ROOMS, GROSS, INNER = "0", "Area_NTA", "Area_BTA", "Area_BRA"

WALLS = [
    box(0, 0, W, D).difference(box(EXT, EXT, W - EXT, D - EXT)),
    box(7000, EXT, 7000 + INT, D - EXT),                # partition between the two rooms
    box(11800, 5800, W - EXT, 5800 + INT),              # lift, south wall
    box(11800, 5800 + INT, 11800 + INT, D - EXT),       # lift, west wall
]
DOOR = box(7000, 3000, 7000 + INT, 3900)                # door between the two rooms
LIFT = box(11800, 6300, 11800 + INT, 7200)              # opening of the lift, no door drawn
ENTRANCE = box(2000, 0, 3000, EXT)                      # storey 1 only; storey 2 has a window there
WINDOWS = [box(4000, 0, 5500, EXT), box(9000, 0, 10500, EXT), box(9000, D - EXT, 10200, D),
           box(W - EXT, 2000, W, 3500), box(0, 3000, EXT, 4200)]
STAIR = (1500.0, 6500.0, 5700.0, D - EXT)               # the flight: 16 risers, going 280, against the north wall
GOING = 280.0
COLUMN = (10000.0, 4500.0, 120.0)
ROOM_OUTLINES = [
    ("01", "Office", [(EXT, EXT), (7000, EXT), (7000, D - EXT), (EXT, D - EXT)], (3000, 3500)),
    ("02", "Meeting room", [(7000 + INT, EXT), (W - EXT, EXT), (W - EXT, 5800), (11800, 5800), (11800, D - EXT),
                            (7000 + INT, D - EXT)], (9000, 2500)),
    ("03", "Lift", [(11800 + INT, 5800 + INT), (W - EXT, 5800 + INT), (W - EXT, D - EXT), (11800 + INT, D - EXT)],
     (12800, 6800)),
]


def storey(number: int, origin: tuple[float, float]) -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4         # millimetres
    for name in (ROOMS, GROSS, INNER):
        doc.layers.add(name)
    tag = doc.blocks.new("ROOMTAG")
    tag.add_attdef("ROOMNUMBER", (0, 0), dxfattribs={"height": 200})
    tag.add_attdef("ROOMNAME", (0, -300), dxfattribs={"height": 200})
    msp = doc.modelspace()
    ox, oy = origin

    def at(points) -> list[tuple[float, float]]:
        return [(x + ox, y + oy) for x, y in points]

    def closed(shape, layer: str = LINES) -> None:
        msp.add_lwpolyline(at(list(shape.exterior.coords)[:-1]), close=True, dxfattribs={"layer": layer})

    windows = WINDOWS + ([ENTRANCE] if number == 2 else [])
    doors = [DOOR] + ([ENTRANCE] if number == 1 else [])
    mass = unary_union(WALLS).difference(unary_union([*windows, *doors, LIFT]))
    for piece in getattr(mass, "geoms", [mass]):       # a wall is what the lines close: only its outline is drawn
        closed(piece)

    for bay in windows:                                 # the hole through the wall, two posts, and the glass between them
        x0, y0, x1, y1 = bay.bounds
        closed(bay)
        if x1 - x0 > y1 - y0:
            mid = (y0 + y1) / 2.0
            parts = [box(x0, mid - 30, x0 + 50, mid + 30), box(x1 - 50, mid - 30, x1, mid + 30),
                     box(x0 + 50, mid - 15, x1 - 50, mid + 15)]
        else:
            mid = (x0 + x1) / 2.0
            parts = [box(mid - 30, y0, mid + 30, y0 + 50), box(mid - 30, y1 - 50, mid + 30, y1),
                     box(mid - 15, y0 + 50, mid + 15, y1 - 50)]
        for part in parts:
            closed(part)

    # door symbols: the leaf as drawn, open, and the arc from its tip to where it shuts
    swings = [((7000 + INT, 3000), 900.0)] + ([((2000, EXT), 1000.0)] if number == 1 else [])
    for (hx, hy), r in swings:
        leaf_to = (hx + r, hy) if hx > 5000 else (hx, hy + r)
        msp.add_line(*at([(hx, hy), leaf_to]), dxfattribs={"layer": LINES})
        msp.add_arc((hx + ox, hy + oy), r, 0, 90, dxfattribs={"layer": LINES})

    x0, y0, x1, y1 = STAIR
    for i in range(round((x1 - x0) / GOING) + 1):
        msp.add_line(*at([(x0 + i * GOING, y0), (x0 + i * GOING, y1)]), dxfattribs={"layer": LINES})
    msp.add_line(*at([(x0, y0), (x1, y0)]), dxfattribs={"layer": LINES})
    msp.add_circle((COLUMN[0] + ox, COLUMN[1] + oy), COLUMN[2], dxfattribs={"layer": LINES})

    for suffix, name, outline, label in ROOM_OUTLINES:
        msp.add_lwpolyline(at(outline), close=True, dxfattribs={"layer": ROOMS})
        ref = msp.add_blockref("ROOMTAG", at([label])[0], dxfattribs={"layer": ROOMS})
        ref.add_auto_attribs({"ROOMNUMBER": f"{number}{suffix}", "ROOMNAME": name})
    closed(box(0, 0, W, D), GROSS)
    msp.add_polyline2d(at([(EXT, EXT), (W - EXT, EXT), (W - EXT, D - EXT), (EXT, D - EXT)]), close=True,
                       dxfattribs={"layer": INNER})
    return doc


if __name__ == "__main__":
    storey(1, (0.0, 0.0)).saveas(HERE / "level-1.dxf")
    storey(2, (50000.0, -20000.0)).saveas(HERE / "level-2.dxf")
