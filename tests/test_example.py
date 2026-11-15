"""The example building, end to end. It is drawn without a flaw, so each number below is known beforehand."""
from __future__ import annotations

import base64
import gzip
import json
import re
from collections import Counter

import ifcopenshell
import ifcopenshell.util.element
import pytest

from plan2bim.cli import main


def test_build_passes_with_nothing_to_look_at(built):
    assert [n.text for n in built.report.notes() if n.severity != "info"] == []
    assert built.report.status == "pass"


def test_model_holds_what_the_drawings_show(built):
    data = json.loads(built.files["data"].read_text(encoding="utf-8"))
    # per storey 3 rooms; doors: 2 on storey 1, 1 on storey 2; windows: 5 and 6; one column each; one flight
    assert data["rooms"] == 6
    assert data["elements"] == {"column": 2, "door": 3, "roof": 1, "slab": 2, "stair": 1, "wall": 28, "window": 11}
    for s in data["storeys"]:
        assert s["cut_on_line"] == pytest.approx(1.0, abs=1e-6)             # every wall face lies on a drawn line
        assert s["areas_m2"]["nothing"] == pytest.approx(0.0, abs=0.001)    # the gross outline is all accounted for
        assert s["areas_m2"]["rooms"] == pytest.approx(49.58 + 45.23 + 3.24, abs=0.001)


def test_storey_with_another_origin_is_put_on_the_reference(built):
    data = json.loads(built.files["data"].read_text(encoding="utf-8"))
    block = next(b for b in data["registration"] if b["block"] == "2/0")
    assert (block["tx"], block["ty"]) == pytest.approx((-50000.0, 20000.0), abs=0.01)


def test_stair_and_lift_open_the_floor_above(built):
    told = [n.text for n in built.report.vertical]
    assert any(t.startswith("stair A from 1: 16 risers of 180 mm; the model holds 15 steps") for t in told)
    assert any("open over 5.04 m2 for stair A" in t for t in told)
    assert any("open over 3.24 m2 for 203 Lift" in t for t in told)


def test_ifc_file_gives_areas_and_sizes(built):
    f = ifcopenshell.open(str(built.files["ifc"]))
    areas = {s.Name: ifcopenshell.util.element.get_psets(s)["Qto_SpaceBaseQuantities"]["NetFloorArea"]
             for s in f.by_type("IfcSpace")}
    assert areas == pytest.approx({"101": 49.58, "102": 45.23, "103": 3.24, "201": 49.58, "202": 45.23, "203": 3.24})
    assert sorted(d.OverallWidth for d in f.by_type("IfcDoor")) == pytest.approx([900.0, 900.0, 1000.0])
    assert {d.OverallHeight for d in f.by_type("IfcDoor")} == {2100.0}
    assert sorted(w.OverallWidth for w in f.by_type("IfcWindow")) == pytest.approx([1000.0] + [1200.0] * 4 + [1500.0] * 6)


def test_viewer_holds_the_ifc_file_for_a_person_to_save(built):
    page = built.files["viewer"].read_text(encoding="utf-8")
    packed = re.search(r'<script id="ifc" type="application/gzip">([A-Za-z0-9+/=]+)</script>', page)
    assert packed and gzip.decompress(base64.b64decode(packed[1])) == built.files["ifc"].read_bytes()


def test_export_writes_the_ifc_file_of_a_build_and_nothing_else(built, example, write, tmp_path):
    target = tmp_path / "handover" / "building.ifc"
    assert main(["export", str(write(example)), "-o", str(target), "-q"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["handover", "project.json"]       # no `out` of the project
    assert [p.name for p in target.parent.iterdir()] == ["building.ifc"]
    exported, of_build = ifcopenshell.open(str(target)), ifcopenshell.open(str(built.files["ifc"]))
    assert Counter(e.is_a() for e in exported.by_type("IfcProduct")) == Counter(e.is_a() for e in of_build.by_type("IfcProduct"))
    assert {e.GlobalId for e in exported.by_type("IfcRoot")} == {e.GlobalId for e in of_build.by_type("IfcRoot")}


def test_export_of_a_model_that_fails_a_check_says_so_and_exits_with_1(example, write, tmp_path, capsys):
    example["stair"][0]["box"] = [8000, 1000, 11000, 3000]      # no riser is drawn there
    assert main(["export", str(write(example)), "-o", str(tmp_path / "building.ifc"), "-q"]) == 1
    assert "failed: stair A from 1 is not in the model" in capsys.readouterr().out


def test_stair_that_is_not_drawn_fails_the_build_and_leaves_the_model(example, write, tmp_path):
    example["stair"][0]["box"] = [8000, 1000, 11000, 3000]      # no riser is drawn there
    code = main(["build", str(write(example)), "--out", str(tmp_path / "out"), "--no-plots", "-q"])
    data = json.loads((tmp_path / "out" / "project.json").read_text(encoding="utf-8"))
    failed = [n for n in data["notes"] if n["severity"] == "fail"]
    assert code == 1 and data["status"] == "fail"
    assert [n["check"] for n in failed] == ["stair"] and "stair A from 1 is not in the model" in failed[0]["text"]
    assert data["elements"]["wall"] == 28 and (tmp_path / "out" / "project.html").stat().st_size > 100_000


@pytest.mark.parametrize(("flags", "code"), [([], 0), (["--strict"], 1)])
def test_unusual_riser_is_a_point_to_look_at(example, write, tmp_path, flags, code):
    example["level"][1]["elevation"] = 3600                     # 16 risers of 225 mm
    example["heights"]["roof"] = 7200
    assert main(["build", str(write(example)), "--out", str(tmp_path / "out"), "--no-plots", "-q", *flags]) == code
    data = json.loads((tmp_path / "out" / "project.json").read_text(encoding="utf-8"))
    assert data["status"] == "warn"
    assert ["a riser of 225 mm is not usual" in n["text"] for n in data["notes"] if n["severity"] == "warn"] == [True]
