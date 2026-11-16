"""The web service, with a directory as its store and the example building as its project."""
from __future__ import annotations

import json
import time
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient

from plan2bim.service import create_app
from plan2bim.store import DirStore

EXAMPLE = Path(__file__).parent.parent / "example"
ANNA = {"X-MS-CLIENT-PRINCIPAL-ID": "11111111-1111-1111-1111-111111111111", "X-MS-CLIENT-PRINCIPAL-NAME": "anna@example.com"}
BERIT = {"X-MS-CLIENT-PRINCIPAL-ID": "22222222-2222-2222-2222-222222222222", "X-MS-CLIENT-PRINCIPAL-NAME": "berit@example.com"}
PAGE = {"X-Plan2bim": "1"}
DEFINITION = {
    "name": "Example building", "reference": "1", "heights": {"roof": 5760},
    "level": [{"key": "1", "name": "Level 1", "drawing": "level-1.dxf", "elevation": 0},
              {"key": "2", "name": "Level 2", "drawing": "level-2.dxf", "elevation": 2880}],
}
STAIR = '[[stair]]\nname = "A"\nkind = "straight"\nlevels = ["1"]\nbox = [1200, 6400, 6000, 7700]\nup = "E"\n'


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    with TestClient(create_app(DirStore(tmp_path_factory.mktemp("store")))) as c:
        yield c


def send(client, method: str, url: str, definition: dict, more: str = "", drawings: tuple[str, ...] = (), user=ANNA,
         made: dict[str, bytes] | None = None):
    files = [("drawings", (name, (EXAMPLE / name).read_bytes())) for name in drawings]
    files += [("drawings", item) for item in (made or {}).items()]
    # a part that is no file makes the request multipart also when it holds no drawing
    return client.request(method, url, headers={**user, **PAGE}, files=[*files, ("more", (None, more)),
                                                                         ("definition", (None, json.dumps(definition)))])


def built(client, pid: str, user=ANNA) -> dict:
    for _ in range(600):
        project = client.get(f"/api/projects/{pid}", headers=user).json()
        if project["state"] != "building":
            return project
        time.sleep(0.1)
    raise AssertionError("the build did not end")


@pytest.fixture(scope="module")
def example(client) -> dict:
    """The example building, uploaded by Anna and built."""
    made = send(client, "POST", "/api/projects", DEFINITION, STAIR, ("level-1.dxf", "level-2.dxf"))
    assert made.status_code == 201, made.text
    return built(client, made.json()["id"])


def test_drawings_become_a_model_that_can_be_seen_and_saved(client, example):
    assert example["state"] == "done"
    assert example["result"]["status"] == "pass"
    assert example["result"]["elements"]["stair"] == 1
    files = f"/api/projects/{example['id']}/files"

    viewer = client.get(f"{files}/viewer", headers=ANNA)
    assert viewer.headers["content-type"].startswith("text/html")
    assert "sandbox" in viewer.headers["content-security-policy"]     # text from a drawing cannot call the service
    assert '"name":"Example building"' in viewer.text

    ifc = client.get(f"{files}/ifc", headers=ANNA)
    assert ifc.headers["content-disposition"] == 'attachment; filename="Example-building.ifc"'
    assert ifc.text.startswith("ISO-10303-21;") and "IFCSTAIR" in ifc.text.upper()

    assert client.get(f"{files}/plot-1", headers=ANNA).content.startswith(b"\x89PNG")
    assert client.get(f"{files}/viewer", headers={**ANNA, "If-None-Match": viewer.headers["etag"]}).status_code == 304


def test_a_request_with_no_identity_is_refused(client, example):
    for url in ("/api/me", "/api/projects", f"/api/projects/{example['id']}", f"/api/projects/{example['id']}/files/ifc"):
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"X-MS-CLIENT-PRINCIPAL-ID": "../../meta"}).status_code == 401
    assert client.get("/healthz").status_code == 200


def test_the_page_serves_its_own_three_files_and_no_other(client):
    site = Path(__file__).parent.parent / "plan2bim" / "site"
    for url, name in (("/", "index.html"), ("/app.js", "app.js"), ("/app.css", "app.css")):
        own = (site / name).read_bytes()
        for query in ("", "?name=/etc/passwd", "?name=../cli.py", "?name=..%2F..%2Fpyproject.toml"):
            assert client.get(url + query).content == own


def test_a_project_is_seen_by_its_owner_only(client, example):
    pid = example["id"]
    assert [p["id"] for p in client.get("/api/projects", headers=ANNA).json()] == [pid]
    assert client.get("/api/projects", headers=BERIT).json() == []
    assert client.get(f"/api/projects/{pid}", headers=BERIT).status_code == 404
    assert client.get(f"/api/projects/{pid}/files/ifc", headers=BERIT).status_code == 404
    assert client.delete(f"/api/projects/{pid}", headers={**BERIT, **PAGE}).status_code == 404
    assert client.get(f"/api/projects/{pid}", headers=ANNA).status_code == 200


def test_a_change_must_come_from_the_page(client, example):
    assert client.delete(f"/api/projects/{example['id']}", headers=ANNA).status_code == 403
    assert client.get(f"/api/projects/{example['id']}", headers=ANNA).status_code == 200


def test_every_problem_of_a_definition_is_reported_and_nothing_is_kept(client):
    wrong = {**DEFINITION, "heights": {}, "out": "/tmp",
             "level": [DEFINITION["level"][0], {**DEFINITION["level"][1], "drawing": "level-3.dxf"}]}
    answer = send(client, "POST", "/api/projects", wrong, "name = 1\n[[stair", ("level-1.dxf",), user=BERIT)
    assert answer.status_code == 422
    assert any("more settings" in p for p in answer.json()["problems"])     # a TOML that cannot be read stops the rest

    answer = send(client, "POST", "/api/projects", wrong, 'name = "x"', ("level-1.dxf",), user=BERIT)
    problems = answer.json()["problems"]
    assert answer.status_code == 422
    assert 'the definition cannot hold "out"' in problems
    assert 'more settings: "name" is not set here; it is set in the form' in problems
    assert 'drawing "level-3.dxf" was not uploaded' in problems
    assert 'heights: "roof" is missing' in problems
    assert client.get("/api/projects", headers=BERIT).json() == []


def test_a_drawing_that_cannot_be_used_is_reported_after_the_build(client):
    other = {**DEFINITION, "input": {"gross": ["No_such_layer"]}}
    made = send(client, "POST", "/api/projects", other, "", ("level-1.dxf", "level-2.dxf"), user=BERIT)
    project = built(client, made.json()["id"], BERIT)
    assert project["state"] == "rejected"
    assert any("level-1.dxf holds no gross outline on layer No_such_layer" in p for p in project["problems"])
    assert client.get(f"/api/projects/{project['id']}/files/ifc", headers=BERIT).status_code == 404
    assert client.delete(f"/api/projects/{project['id']}", headers={**BERIT, **PAGE}).status_code == 204


def test_drawings_of_one_storey_are_built_as_one(client, example, tmp_path):
    """Storey 1 as two files: its outlines in one and its building lines in the other."""
    parts = {}
    for name, keep in (("areas.dxf", lambda layer: layer != "0"), ("plan.dxf", lambda layer: layer == "0")):
        doc = ezdxf.readfile(EXAMPLE / "level-1.dxf")
        for entity in [e for e in doc.modelspace() if not keep(e.dxf.layer)]:
            doc.modelspace().delete_entity(entity)
        doc.saveas(tmp_path / name)
        parts[name] = (tmp_path / name).read_bytes()
    two = {**DEFINITION, "level": [{**DEFINITION["level"][0], "drawing": list(parts)}, DEFINITION["level"][1]]}
    made = send(client, "POST", "/api/projects", two, STAIR, ("level-2.dxf",), user=BERIT, made=parts)
    assert made.status_code == 201, made.text
    project = built(client, made.json()["id"], BERIT)
    assert (project["state"], project["result"]["status"]) == ("done", "pass")
    assert project["result"]["elements"] == example["result"]["elements"]
    assert [d["name"] for d in project["drawings"]] == ["areas.dxf", "plan.dxf", "level-2.dxf"]
    assert client.delete(f"/api/projects/{project['id']}", headers={**BERIT, **PAGE}).status_code == 204


def test_a_changed_definition_builds_again_with_the_kept_drawings(client):
    made = send(client, "POST", "/api/projects", DEFINITION, STAIR, ("level-1.dxf", "level-2.dxf"), user=BERIT)
    first = built(client, made.json()["id"], BERIT)
    assert first["result"]["elements"]["stair"] == 1

    # no stair now, and the drawings are named as the service keeps them
    again = send(client, "PUT", f"/api/projects/{first['id']}", first["definition"], "", user=BERIT)
    assert again.status_code == 200, again.text
    second = built(client, first["id"], BERIT)
    assert second["state"] == "done" and second["build"] == 2
    assert "stair" not in second["result"]["elements"]
    assert [d["name"] for d in second["drawings"]] == ["level-1.dxf", "level-2.dxf"]

    assert client.delete(f"/api/projects/{first['id']}", headers={**BERIT, **PAGE}).status_code == 204
    assert client.get(f"/api/projects/{first['id']}", headers=BERIT).status_code == 404
    assert client.get("/api/projects", headers=BERIT).json() == []
