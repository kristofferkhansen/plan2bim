"""The example building, built once for all tests that look at its result."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from plan2bim import pipeline
from plan2bim.project import load

EXAMPLE = Path(__file__).parent.parent / "example" / "example.toml"


@pytest.fixture(scope="session")
def built(tmp_path_factory) -> pipeline.Outcome:
    project = load(EXAMPLE)
    project.out = tmp_path_factory.mktemp("example")
    return pipeline.run(project, "example", plots=False)


@pytest.fixture
def example() -> dict:
    """The example project as data, with the drawings named by their full paths: change it, then `write` it."""
    data = tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))
    for lv in data["level"]:
        lv["drawing"] = str(EXAMPLE.parent / lv["drawing"])
    return data


@pytest.fixture
def write(tmp_path):
    def to_file(data: dict) -> Path:
        path = tmp_path / "project.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path
    return to_file
