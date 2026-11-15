"""The reader of the drawings: units, entities it does not take, arcs that are drawn mirrored, and DWG conversion."""
from __future__ import annotations

import math
import os
from pathlib import Path

import ezdxf
import pytest

from plan2bim import source
from plan2bim.errors import InputError
from plan2bim.project import Input

LEVEL_1 = Path(__file__).parent.parent / "example" / "level-1.dxf"


def drawing(tmp_path: Path, draw) -> Path:
    """A DXF with a gross outline and what `draw` adds to its model space."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4         # millimetres
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (1000, 0), (1000, 1000), (0, 1000)], close=True, dxfattribs={"layer": "Area_BTA"})
    draw(msp)
    path = tmp_path / "drawing.dxf"
    doc.saveas(path)
    return path


def test_drawing_in_another_unit_is_read_in_mm_and_the_mismatch_is_noted():
    d = source.read("1", LEVEL_1, Input(unit="m"))
    assert max(x for x, _, _ in d.gross[0].pts) == pytest.approx(14000.0 * 1000.0)
    assert d.notes == ["the drawing says that its unit is mm; it is read in m, as the project file says"]


def test_entity_that_the_reader_does_not_take_is_reported(tmp_path):
    def draw(msp):
        msp.add_line((0, 0), (1000, 0))
        msp.add_spline([(0, 0), (300, 200), (600, 0)])
        msp.add_text("WC", dxfattribs={"layer": "Notes"})       # on a layer that the reader does not use: not its business
    d = source.read("1", drawing(tmp_path, draw), Input())
    assert len(d.lines) == 1
    assert d.notes == ["not read, because the reader does not take this kind of entity on that layer: 1 SPLINE on layer 0"]


def test_drawing_with_other_layer_names_says_which_layers_it_has(tmp_path):
    path = drawing(tmp_path, lambda msp: msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "A-WALL"}))
    with pytest.raises(InputError, match=r"holds no building lines on layer 0\. .*A-WALL \(1\)"):
        source.read("1", path, Input())
    assert len(source.read("1", path, Input(lines=("a-wall",))).lines) == 1


@pytest.mark.parametrize("mirrored", [False, True])
def test_arc_is_the_arc_that_is_drawn(tmp_path, mirrored):
    def draw(msp):
        msp.add_arc((500, 400), 300, 20, 110, dxfattribs={"extrusion": (0, 0, -1)} if mirrored else {})
    path = drawing(tmp_path, draw)
    drawn = [(v.x, v.y) for v in next(iter(ezdxf.readfile(path).modelspace().query("ARC"))).flattening(0.01)]
    (poly,) = source.read("1", path, Input()).lines
    arc = source.bulge_arc(poly.pts[0][:2], poly.pts[1][:2], poly.pts[0][2])
    half = arc.a0 + arc.sweep / 2.0
    middle = (arc.cx + arc.r * math.cos(half), arc.cy + arc.r * math.sin(half))
    assert arc.r == pytest.approx(300.0)
    assert min(math.dist(middle, p) for p in drawn) < 5.0       # the same side of the chord as the drawn arc


def test_storey_that_is_given_another_dwg_is_converted_again(tmp_path, monkeypatch):
    """`dwg2dxf` here is a program that copies the DWG and counts its runs: the test needs no LibreDWG."""
    tool = tmp_path / "bin" / "dwg2dxf"
    tool.parent.mkdir()
    tool.write_text(f'#!/bin/sh\ncp "$4" "$3"\necho run >> "{tmp_path}/runs"\n', encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool.parent), prepend=os.pathsep)
    new, old, cache = tmp_path / "new.dwg", tmp_path / "old.dwg", tmp_path / "dxf" / "1.dxf"
    new.write_text("new revision", encoding="utf-8")
    old.write_text("old revision", encoding="utf-8")
    os.utime(old, (0, 0))               # older than any conversion

    assert source.to_dxf(new, cache).read_text(encoding="utf-8") == "new revision"
    assert source.to_dxf(new, cache).read_text(encoding="utf-8") == "new revision"
    assert (tmp_path / "runs").read_text(encoding="utf-8").count("run") == 1      # the same drawing: converted once
    assert source.to_dxf(old, cache).read_text(encoding="utf-8") == "old revision"
