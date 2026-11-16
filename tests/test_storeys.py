"""Storeys as real drawing sets give them: outlines in a drawing of their own, room numbers as plain texts,
one outline round two buildings, and a block that no other storey holds."""
from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from plan2bim import pipeline
from plan2bim.errors import InputError
from plan2bim.project import Project, parse

EXAMPLE = Path(__file__).parent.parent / "example"
ROOMS = {"101": ("Kontor", (300, 300, 4900, 5700)), "102": ("Støperi", (5100, 300, 9700, 5700))}


def new():
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4         # millimetres
    return doc


def box(msp, x0: float, y0: float, x1: float, y1: float, layer: str) -> None:
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


def house(msp, x: float = 0.0, layer: str = "A231") -> None:
    """The wall faces of a house of 10 x 6 m with two rooms."""
    box(msp, x, 0, x + 10000, 6000, layer)
    for _, (x0, y0, x1, y1) in ROOMS.values():
        box(msp, x + x0, y0, x + x1, y1, layer)


def one_storey(tmp_path: Path, drawings: list[str]) -> Project:
    return parse({"name": "House", "reference": "1", "heights": {"roof": 3000},
                  "level": [{"key": "1", "drawing": drawings, "elevation": 0}]}, tmp_path)


def test_area_drawing_and_plan_drawing_give_rooms_with_number_and_name(tmp_path):
    area, plan = new(), new()
    box(area.modelspace(), 0, 0, 10000, 6000, "861 - Etasje BTA")
    house(plan.modelspace())
    for number, (name, (x0, y0, x1, y1)) in ROOMS.items():
        box(area.modelspace(), x0, y0, x1, y1, "862 - Rom NTA")
        plan.modelspace().add_text(number, dxfattribs={"layer": "F29-T1-Nr", "insert": (x0 + 500, y0 + 900)})
        plan.modelspace().add_text(name, dxfattribs={"layer": "F29-T2-Navn", "insert": (x0 + 500, y0 + 500)})
        plan.modelspace().add_text("NTA 24,8", dxfattribs={"layer": "F29-T3-Areal", "insert": (x0 + 500, y0 + 100)})
    area.saveas(tmp_path / "area.dxf")
    plan.saveas(tmp_path / "plan.dxf")

    level = pipeline.read_drawings(one_storey(tmp_path, ["area.dxf", "plan.dxf"])).levels["1"]
    assert {r.number: r.name for r in level.rooms} == {"101": "Kontor", "102": "Støperi"}
    assert level.notes == []


def test_one_gross_outline_round_two_buildings_is_two_outlines(tmp_path):
    doc = new()
    msp = doc.modelspace()
    house(msp)
    house(msp, 15000.0)
    # one closed polyline: round the first house, along a line to the second, round it, and back along that line
    msp.add_lwpolyline([(10000, 3000), (10000, 0), (0, 0), (0, 6000), (10000, 6000), (10000, 3000), (15000, 3000),
                        (15000, 6000), (25000, 6000), (25000, 0), (15000, 0), (15000, 3000)], close=True,
                       dxfattribs={"layer": "861 - Etasje BTA"})
    doc.saveas(tmp_path / "plan.dxf")

    level = pipeline.read_drawings(one_storey(tmp_path, ["plan.dxf"])).levels["1"]
    assert sorted(round(g.area / 1e6) for g in level.gross) == [60, 60]
    assert any("read as 2 outlines of 60.00, 60.00 m2" in n for n in level.notes)


def test_gross_outline_with_no_area_stops_the_build_and_says_so(tmp_path):
    doc = new()
    house(doc.modelspace())
    doc.modelspace().add_lwpolyline([(0, 0), (5000, 0), (10000, 0)], close=True, dxfattribs={"layer": "861 - Etasje BTA"})
    doc.saveas(tmp_path / "plan.dxf")
    with pytest.raises(InputError, match="storey 1: no gross outline of the storey encloses an area"):
        pipeline.read_drawings(one_storey(tmp_path, ["plan.dxf"]))


def test_block_that_no_other_storey_holds_is_placed_with_its_drawing_and_noted(tmp_path):
    doc = ezdxf.readfile(EXAMPLE / "level-2.dxf")
    msp = doc.modelspace()
    box(msp, 80000, 0, 90000, 6000, "Area_BTA")         # a wing that only storey 2 has, beside the building
    for y in (0, 300, 5700, 6000):
        msp.add_line((80000, y), (90000, y))
    doc.saveas(tmp_path / "level-2.dxf")
    project = parse({"name": "Example", "reference": "1", "heights": {"roof": 5760}, "level": [
        {"key": "1", "drawing": str(EXAMPLE / "level-1.dxf"), "elevation": 0},
        {"key": "2", "drawing": "level-2.dxf", "elevation": 2880}]}, tmp_path)

    read = pipeline.read_drawings(project)
    building, wing = sorted(read.blocks["2"], key=lambda b: -b.outline.area)
    assert building.t == pytest.approx((-50000.0, 20000.0), abs=0.01)       # found: the storeys have other origins
    assert wing.t == building.t
    assert any(n.startswith("block 2/1: its place is in doubt.") and "block 2/0 of the same drawing" in n
               for n in read.levels["2"].notes)
