"""The project file: what is wrong in it is said, all at once, and before anything is built."""
from __future__ import annotations

from pathlib import Path

import pytest

from plan2bim.cli import main
from plan2bim.errors import ProjectError
from plan2bim.project import load, parse

EXAMPLE = Path(__file__).parent.parent / "example" / "example.toml"


def problems(data: dict) -> list[str]:
    with pytest.raises(ProjectError) as caught:
        parse(data, Path("/"))
    return caught.value.problems


def test_every_problem_is_reported_at_once(example):
    example["reference"] = "3"
    del example["heights"]["roof"]
    example["stair"][0]["kind"] = "ladder"
    example["gates"] = {"riser": [190, 150]}
    example["level"][0]["drawing"] = "/nowhere/level-1.dxf"
    found = "\n".join(problems(example))
    for part in ('"reference" names level "3"', 'heights: "roof" is missing', 'stair "A": "kind" must be one of',
                 'gates: "riser"', "/nowhere/level-1.dxf does not exist"):
        assert part in found


def test_key_of_an_earlier_version_is_named(example):
    example["level"][0]["dwg"] = example["level"][0].pop("drawing")
    assert 'level 1: unknown key "dwg"; it is named "drawing" now' in problems(example)


def test_storey_too_low_for_its_doors_is_refused(example):
    example["level"][1]["elevation"] = 2300         # 2000 mm under the plate, door head 2100
    assert any(p.startswith('level "1": 2300 mm up to the next floor leaves 2000 mm') for p in problems(example))


def test_dogleg_stair_needs_a_first_flight_beside_its_landing(example):
    example["stair"][0] |= {"kind": "dogleg", "entry": "N", "up": "S"}
    assert any('"up" is the side of the first flight' in p for p in problems(example))


def test_json_and_toml_give_the_same_project(example, write):
    a, b = load(EXAMPLE), load(write(example))
    assert (a.levels, a.heights, a.stairs, a.input, a.gates, a.tol) == (b.levels, b.heights, b.stairs, b.input, b.gates, b.tol)


def test_check_command_names_the_problem_and_exits_with_2(example, write, capsys):
    example["heights"]["window_sill"] = 2200
    assert main(["check", str(write(example))]) == 2
    assert 'error: heights: "window_sill" must be under "window_head"' in capsys.readouterr().err
