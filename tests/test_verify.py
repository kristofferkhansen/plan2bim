"""The checks themselves: what they take the model to be."""
from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import Point, Polygon, box

from plan2bim import mesh, verify
from plan2bim.level import Level
from plan2bim.model import Element, Model, Piece


def ring(x0: float, x1: float, thick: float) -> Polygon:
    """Four walls round a square, from `x0` to `x1` in both directions."""
    return Polygon(box(x0, x0, x1, x1).exterior, [box(x0 + thick, x0 + thick, x1 - thick, x1 - thick).exterior])


def test_plan_cut_holds_what_stands_inside_a_closed_ring_of_walls():
    """A ring of walls with no door at the height of the cut, a shaft inside it, and a column inside the shaft."""
    room, shaft = ring(0, 9000, 300), ring(3000, 6000, 200)
    column = Point(4500, 4500).buffer(200)
    model = Model("ring")
    model.add(Element("wall", "1", "Wall of the room", [Piece(room, 0.0, 2700.0, "wall")]))
    model.add(Element("wall", "1", "Wall of the shaft", [Piece(shaft, 0.0, 2700.0, "wall")]))
    model.add(Element("column", "1", "Column", [Piece(column, 0.0, 2700.0, "column")]))

    cut = verify.cut_area(mesh.solids(model), "1", 1200.0)

    assert cut.area == pytest.approx(room.area + shaft.area + column.area, rel=1e-6)
    assert cut.contains(Point(4500, 4500))          # the column
    assert not cut.contains(Point(3500, 4500))      # the open shaft beside it


def test_areas_take_a_wall_that_only_touches_the_gross_outline():
    """A wall outside the gross outline that shares an edge with it gives a line in the intersection, no area."""
    gross = box(0, 0, 9000, 9000)
    level = Level("1", np.empty((0, 4)), np.empty(0, int), np.empty(0, bool), [], [], [], [gross], [], [])
    inside, outside = box(0, 0, 9000, 300), box(9000, 0, 9300, 9000)
    model = Model("touch")
    model.add(Element("wall", "1", "Wall inside", [Piece(inside, 0.0, 2700.0, "wall")]))
    model.add(Element("wall", "1", "Wall outside", [Piece(outside, 0.0, 2700.0, "wall")]))

    balance, _ = verify.areas(level, model)

    assert balance["walls"] == pytest.approx(inside.area)
    assert balance["nothing"] == pytest.approx(gross.area - inside.area)
