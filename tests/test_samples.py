"""Public DWG files, which are not in the repository: the reader on drawings that AutoCAD and others wrote.

    uv run python tests/samples.py dwg/samples
    PLAN2BIM_SAMPLES=dwg/samples uv run pytest tests/test_samples.py

These tests need `dwg2dxf` of LibreDWG on the PATH. Without PLAN2BIM_SAMPLES they are skipped.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from plan2bim import source
from plan2bim.cli import main
from plan2bim.project import Input

SAMPLES = Path(os.environ.get("PLAN2BIM_SAMPLES", ""))
HOUSE = SAMPLES / "autodesk" / "blocks_and_tables_-_metric.dwg"

pytestmark = pytest.mark.skipif("PLAN2BIM_SAMPLES" not in os.environ, reason="PLAN2BIM_SAMPLES names no directory")


@pytest.mark.parametrize("version", ["r14", "2000", "2004", "2007", "2010", "2013", "2018"])
def test_dwg_of_each_version_gives_the_same_lines(version, tmp_path):
    """One drawing, saved as each version of DWG. R14 has no layer name with a space in it."""
    dxf = source.to_dxf(SAMPLES / "libredwg" / f"example_{version}.dwg", tmp_path / "example.dxf")
    d = source.read(version, {"example.dwg": dxf}, Input(lines=("Tavolo 3", "TAVOLO_3"), gross=("Tavolo 2", "TAVOLO_2")))
    xs = [x for poly in d.lines for x, _, _ in poly.pts]
    assert (len(d.lines), sum(len(poly.pts) for poly in d.lines), len(d.gross)) == (16, 3690, 4)
    assert (min(xs), max(xs)) == pytest.approx((-3088.15, 11211.63), abs=0.01)


def test_inspect_lists_the_layers_of_a_drawing_from_autocad(capsys):
    assert main(["inspect", str(HOUSE)]) == 0
    out = capsys.readouterr().out
    assert "unit: m" in out
    assert re.search(r"^\s+Walls\s+LINE\s+211\s+building lines$", out, re.MULTILINE)     # every layer is read by default
    assert re.search(r"^\s+Doors\s+INSERT\s+16\s+building lines$", out, re.MULTILINE)


def test_drawing_with_no_area_layers_is_refused_and_its_layers_are_named(write, capsys):
    project = write({"name": "House", "reference": "1", "heights": {"roof": 3000},
                     "level": [{"key": "1", "name": "Both storeys", "drawing": str(HOUSE.resolve()), "elevation": 0}],
                     "input": {"unit": "m", "lines": ["Walls"]}})
    assert main(["build", str(project), "-q"]) == 2
    assert "holds no gross outline on layer Area_BTA" in capsys.readouterr().err
    assert not list(project.parent.glob("out/*.ifc"))
