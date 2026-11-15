"""Buildings that are not in the repository: real drawings, which cannot be published.

Set PLAN2BIM_CORPUS to a directory. Each NAME.expected.json in it names a building: NAME.toml beside it
is the project, and the file holds what its build must give: any of the top keys of the NAME.json that
a build writes, for example "status", "counts", "elements", "rooms" and "storeys". Make it from a build
that a person has reviewed:

    jq '{status, counts, elements, rooms, storeys}' out/NAME.json > NAME.expected.json

Without PLAN2BIM_CORPUS there is no building here, and the test is skipped.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from plan2bim import pipeline
from plan2bim.project import load

CORPUS = os.environ.get("PLAN2BIM_CORPUS")
EXPECTED = sorted(Path(CORPUS).glob("*.expected.json")) if CORPUS else []


@pytest.mark.parametrize("expected", EXPECTED, ids=lambda p: p.name.removesuffix(".expected.json"))
def test_building_gives_what_was_reviewed(expected: Path, tmp_path):
    name = expected.name.removesuffix(".expected.json")
    project = load(expected.with_name(f"{name}.toml"))
    project.out = tmp_path
    pipeline.run(project, name, plots=False)
    data = json.loads((tmp_path / f"{name}.json").read_text(encoding="utf-8"))
    reviewed = json.loads(expected.read_text(encoding="utf-8"))
    assert {key: data[key] for key in reviewed} == reviewed
