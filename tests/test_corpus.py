"""Buildings that are not in the repository: real drawings, which cannot be published.

Set PLAN2BIM_CORPUS to a directory. Each NAME.toml in it is a project, and NAME.expected.json beside it
holds what its build must give: any of the top keys of the NAME.json that a build writes, for example
"status", "counts", "elements", "rooms" and "storeys". Make it from a build that a person has reviewed:

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
PROJECTS = sorted(Path(CORPUS).glob("*.toml")) if CORPUS else []


@pytest.mark.parametrize("path", PROJECTS, ids=lambda p: p.stem)
def test_building_gives_what_was_reviewed(path: Path, tmp_path):
    expected = json.loads(path.with_suffix(".expected.json").read_text(encoding="utf-8"))
    project = load(path)
    project.out = tmp_path
    pipeline.run(project, path.stem, plots=False)
    data = json.loads((tmp_path / f"{path.stem}.json").read_text(encoding="utf-8"))
    assert {key: data[key] for key in expected} == expected
