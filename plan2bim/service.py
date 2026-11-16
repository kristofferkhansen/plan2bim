"""The web service: upload the drawings of a building, build its model, look at it, save its IFC file.

  GET     /                              the page
  GET     /api/me                        who is signed in
  GET     /api/defaults                  the heights and the drawing convention that a new project starts with
  GET     /api/projects                  the projects of the user
  POST    /api/projects                  a new project: its definition and its drawings. The build starts.
  GET     /api/projects/ID               one project: its definition, its state, its result
  PUT     /api/projects/ID               another definition, other drawings. The build starts again.
  DELETE  /api/projects/ID
  GET     /api/projects/ID/files/NAME    viewer, ifc, report, data, or plot-KEY
  GET     /healthz

The service does not sign a user in. In Azure, the built-in authentication of Container Apps stands before
it and puts the Entra object id of the user into the header `X-MS-CLIENT-PRINCIPAL-ID`. Each request must
carry it: a request without it is refused, also when the platform lets it through. The header is true only
behind that authentication, so the service must not be reachable any other way.
On a developer's machine, `plan2bim serve --dev-user NAME` makes every request the request of one user.

A project belongs to the user who made it. No other user can read it.

The definition of a project is the project file of `plan2bim` (see `project.py`). A build is the command
`plan2bim build`, run as a process of its own: an error or a crash in it does not stop the service, and a
build that takes too long is stopped. One build runs at a time. The builds that wait, and the one that
runs, are known to this process only, so the service runs as one process.
"""
from __future__ import annotations

import collections
import contextlib
import json
import logging
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, BinaryIO

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.datastructures import UploadFile
from starlette.middleware.gzip import GZipMiddleware

from .errors import ProjectError
from .project import Heights, Input, parse
from .store import Store

log = logging.getLogger("plan2bim.service")

SITE = Path(__file__).parent / "site"
OID = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")     # the object id of a user in Entra
PID = re.compile(r"[0-9a-f]{16}")
DEV_ID = "00000000-0000-0000-0000-000000000000"
FORM = ("name", "reference", "heights", "input", "level")           # what the page sends as the definition
MORE = ("stair", "review", "registration", "gates", "tolerances", "georeference", "author", "organization")
MAX_DRAWING = 100 << 20         # bytes of one drawing
MAX_DRAWINGS = 240             # drawings of one request
MAX_REQUEST = 400 << 20         # bytes of one request
MAX_LEVELS = 60
STALE = 120.0                   # s; a build that this process does not know, and that is older, has stopped
TYPES = {".html": "text/html; charset=utf-8", ".ifc": "application/x-step", ".txt": "text/plain; charset=utf-8",
         ".json": "application/json", ".png": "image/png"}
PAGE_POLICY = ("default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
               "form-action 'self'; frame-ancestors 'none'")
# The viewer holds text from the drawings. It runs as a page of no origin: it cannot call this service.
VIEWER_POLICY = ("sandbox allow-scripts allow-downloads; default-src 'none'; script-src 'unsafe-inline'; "
                 "style-src 'unsafe-inline'; img-src data: blob:; frame-ancestors 'self'")


@dataclass(frozen=True)
class User:
    id: str
    name: str


class Problems(Exception):
    """What a person must change before the project can be built."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _stem(name: str) -> str:
    """The name of the output files of a project: its name in letters, digits and '-'."""
    plain = unicodedata.normalize("NFKD", name.translate(str.maketrans(
        {"æ": "ae", "ø": "o", "å": "a", "Æ": "Ae", "Ø": "O", "Å": "A"}))).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", plain).strip("-")[:60] or "model"


def _local(name: str) -> str:
    """The name of a drawing as a file name on this machine: the name that the user gave it, made harmless."""
    path = Path(re.sub(r"[\x00-\x1f/\\]", "_", name))
    return f"{path.stem[:120] or 'drawing'}{path.suffix.lower()}"


def _write(source: BinaryIO, target: Path, name: str) -> None:
    size = 0
    with target.open("wb") as f:
        while piece := source.read(1 << 20):
            size += len(piece)
            if size > MAX_DRAWING:
                raise Problems([f"{name}: a drawing can have {MAX_DRAWING >> 20} MB at most"])
            f.write(piece)
    if size == 0:
        raise Problems([f"{name}: the file is empty"])


class Service:
    def __init__(self, store: Store, timeout: float) -> None:
        self.store = store
        self.timeout = timeout
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="build")
        self.running: dict[tuple[str, str], str] = {}       # builds that wait or run here, and how far each is
        self.child: tuple[tuple[str, str], subprocess.Popen] | None = None

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
        if self.child:
            self.child[1].kill()

    # ------------------------------------------------------------ projects

    @staticmethod
    def _meta(owner: str, pid: str) -> str:
        return f"meta/{owner}/{pid}.json"

    @staticmethod
    def _files(owner: str, pid: str) -> str:
        return f"files/{owner}/{pid}/"

    def load(self, owner: str, pid: str) -> dict:
        if not PID.fullmatch(pid):
            raise HTTPException(404, "there is no such project")
        try:
            meta = json.loads(self.store.get(self._meta(owner, pid)))
        except KeyError:
            raise HTTPException(404, "there is no such project") from None
        age = (datetime.now(UTC) - datetime.fromisoformat(meta["since"])).total_seconds()
        if meta["state"] == "building" and (owner, pid) not in self.running and age > STALE:
            meta.update(state="error", since=_now(), message="The build stopped before its end. Build the project again.")
            self.store.put(self._meta(owner, pid), json.dumps(meta).encode())
        return meta

    def listing(self, owner: str) -> list[dict]:
        found = []
        for key in self.store.keys(f"meta/{owner}/"):
            with contextlib.suppress(HTTPException):        # removed since the keys were listed
                meta = self.load(owner, key.rsplit("/", 1)[1].removesuffix(".json"))
                found.append({"id": meta["id"], "name": meta["name"], "created": meta["created"], "state": meta["state"],
                              "status": (meta.get("result") or {}).get("status")})
        return sorted(found, key=lambda m: m["created"], reverse=True)

    def save(self, user: User, pid: str | None, definition: object, more: str,
             uploads: dict[str, BinaryIO]) -> dict:
        """Take a definition and its drawings, check them, keep them, and start the build.

        A level names each of its drawings by the name of an uploaded file or, in a project that exists, by
        the name under which the service keeps a drawing of it. Raises Problems with all that is wrong.
        """
        old = self.load(user.id, pid) if pid else None
        if old and old["state"] == "building":
            raise HTTPException(409, "the project is building")
        problems: list[str] = []
        if not isinstance(definition, dict):
            raise Problems(["the definition must be a table"])
        problems += [f'the definition cannot hold "{key}"' for key in definition if key not in FORM]
        try:
            tables = tomllib.loads(more)
        except tomllib.TOMLDecodeError as e:
            raise Problems([f"more settings: {e}"]) from None
        problems += [f'more settings: "{key}" is not set here' + ("; it is set in the form" if key in FORM else "")
                     for key in tables if key not in MORE]
        levels = definition.get("level")
        if not isinstance(levels, list) or not all(isinstance(lv, dict) for lv in levels):
            raise Problems([*problems, "at least one storey with a drawing is needed"])
        if len(levels) > MAX_LEVELS:
            raise Problems([f"a project can have {MAX_LEVELS} storeys at most"])

        kept = {d["file"]: d for d in old["drawings"]} if old else {}
        taken = {int(Path(file).stem) for file in kept}
        used: dict[str, dict] = {}          # the drawings of this definition, by the name that a level gave
        with tempfile.TemporaryDirectory(prefix="plan2bim-") as tmp:
            root = Path(tmp)
            (root / "drawings").mkdir()
            for lv in levels:
                given = lv.get("drawing")
                names = [given] if isinstance(given, str) else given
                if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                    continue                                # `parse` reports a drawing that is not a name
                for i, name in enumerate(names):
                    if name in used:
                        pass
                    elif name in kept:
                        used[name] = kept[name]
                        (root / name).touch()               # `parse` asks only whether the drawing is there
                    elif name in uploads:
                        suffix = Path(name).suffix.lower()
                        if suffix not in (".dwg", ".dxf"):
                            problems.append(f"{name}: a drawing is a .dwg or a .dxf file")
                            continue
                        number = next(n for n in range(len(taken) + 1) if n not in taken)
                        taken.add(number)
                        used[name] = {"file": f"drawings/{number}{suffix}", "name": Path(name).name}
                        try:
                            _write(uploads[name], root / used[name]["file"], name)
                        except Problems as e:
                            problems += e.problems
                    else:
                        problems.append(f'drawing "{name}" was not uploaded')
                    if name in used:
                        names[i] = used[name]["file"]
                lv["drawing"] = names if isinstance(given, list) else names[0]
            try:
                project = parse({**definition, **tables}, root)
            except ProjectError as e:
                raise Problems(problems + e.problems) from None
            if problems:
                raise Problems(problems)

            pid = pid or secrets.token_hex(8)
            base = self._files(user.id, pid)
            drawings = sorted(used.values(), key=lambda d: d["file"])
            for d in drawings:
                if d["file"] not in kept:
                    self.store.upload(base + d["file"], root / d["file"])
        for file in kept:
            if not any(d["file"] == file for d in drawings):
                self.store.delete(base + file)
        self.store.delete(base + "out/")
        meta = {"id": pid, "name": project.name, "owner": user.name, "created": old["created"] if old else _now(),
                "state": "building", "since": _now(), "build": old["build"] + 1 if old else 1,
                "stem": _stem(project.name), "definition": definition, "more": more, "drawings": drawings}
        self.running[user.id, pid] = "waiting for the build before it" if len(self.running) else "starting"
        self.store.put(self._meta(user.id, pid), json.dumps(meta).encode())
        self.pool.submit(self._build, user.id, pid, meta["build"])
        return meta

    def remove(self, owner: str, pid: str) -> None:
        self.load(owner, pid)
        self.store.delete(self._meta(owner, pid))
        if self.child and self.child[0] == (owner, pid):
            self.child[1].kill()
        self.store.delete(self._files(owner, pid))

    # ------------------------------------------------------------ the build

    def _current(self, owner: str, pid: str, build: int) -> dict | None:
        """The project, if it is still there and this is still its build."""
        try:
            meta = json.loads(self.store.get(self._meta(owner, pid)))
        except KeyError:
            return None
        return meta if meta["build"] == build else None

    def _build(self, owner: str, pid: str, build: int) -> None:
        started = time.monotonic()
        try:
            meta = self._current(owner, pid, build)
            if meta is None:
                return
            try:
                outcome = self._run(owner, pid, meta)
            except Exception:
                log.exception("project %s of %s: an error in the service", pid, owner)
                outcome = {"state": "error", "message": "An error in the service stopped the build."}
            if self._current(owner, pid, build) is None:        # removed while it was built
                self.store.delete(self._files(owner, pid) + "out/")
                return
            meta.update(outcome, since=_now())
            self.store.put(self._meta(owner, pid), json.dumps(meta).encode())
            log.info("project %s of %s: %s after %.0f s", pid, owner,
                     (meta.get("result") or {}).get("status") or meta["state"], time.monotonic() - started)
        finally:
            self.running.pop((owner, pid), None)

    def _run(self, owner: str, pid: str, meta: dict) -> dict:
        base = self._files(owner, pid)
        with tempfile.TemporaryDirectory(prefix="plan2bim-") as tmp:
            root = Path(tmp)
            # the build reads each drawing under the name that the user gave it: its messages and its report use it
            local = {d["file"]: f"{Path(d['file']).with_suffix('')}/{_local(d['name'])}" for d in meta["drawings"]}
            for d in meta["drawings"]:
                self.store.download(base + d["file"], root / local[d["file"]])
            definition = {**meta["definition"], **tomllib.loads(meta["more"])}
            definition["level"] = [{**lv, "drawing": [local[name] for name in (
                [lv["drawing"]] if isinstance(lv["drawing"], str) else lv["drawing"])]} for lv in definition["level"]]
            file = root / f"{meta['stem']}.json"
            file.write_text(json.dumps(definition), encoding="utf-8")
            out = root / "out"
            said = root / "said.txt"
            expired = threading.Event()
            with said.open("wb") as stdout:
                child = subprocess.Popen([sys.executable, "-m", "plan2bim", "build", str(file), "--out", str(out)],
                                         stdout=stdout, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
            self.child = ((owner, pid), child)

            def stop() -> None:
                expired.set()
                child.kill()
            timer = threading.Timer(self.timeout, stop)
            timer.start()
            log_lines: collections.deque[str] = collections.deque(maxlen=40)
            try:
                assert child.stderr is not None
                for line in child.stderr:                   # the progress of the build, one line for each step
                    if line := line.rstrip():
                        log_lines.append(line)
                        step = re.sub(r"^\s*[\d.]+ s\s+", "", line).replace(f"{out}/", "")
                        self.running[owner, pid] = f"done so far: {step}"
                code = child.wait()
            finally:
                timer.cancel()
                self.child = None

            if expired.is_set():
                return {"state": "error", "message": f"The build took more than {self.timeout / 60:.0f} minutes "
                                                     "and was stopped."}
            if code == 2:       # the definition or a drawing cannot be used: `plan2bim` says what to change
                return {"state": "rejected", "problems": [line.removeprefix("error: ") for line in log_lines
                                                          if line.startswith("error: ")]}
            if code not in (0, 1):
                log.error("project %s of %s: plan2bim build ended with %s\n%s", pid, owner, code, "\n".join(log_lines))
                return {"state": "error", "message": "plan2bim could not build this project: "
                                                     + (log_lines[-1] if log_lines else f"exit status {code}")}
            data = json.loads((out / f"{meta['stem']}.json").read_text(encoding="utf-8"))
            self.running[owner, pid] = "keeping the result"
            for rel in data["files"].values():
                self.store.upload(f"{base}out/{rel}", out / rel)
            summary = [line for line in said.read_text(encoding="utf-8").splitlines() if not line.startswith("Report:")]
            return {"state": "done", "files": data["files"],
                    "result": {"status": data["status"], "counts": data["counts"], "summary": summary,
                               "rooms": data["rooms"], "elements": data["elements"]}}


def who(request: Request) -> User:
    """The user of a request: the one that the authentication of the platform names, and no other."""
    if request.app.state.dev_user:
        return User(DEV_ID, request.app.state.dev_user)
    oid = request.headers.get("x-ms-client-principal-id", "").lower()
    if not OID.fullmatch(oid):
        raise HTTPException(401, "not signed in")
    return User(oid, request.headers.get("x-ms-client-principal-name") or oid)


def changes(request: Request) -> None:
    """A request that changes something comes from the page: another site cannot set this header."""
    if "x-plan2bim" not in request.headers:
        raise HTTPException(403, "the request does not come from the page")


Signed = Annotated[User, Depends(who)]


def create_app(store: Store, *, dev_user: str | None = None, build_timeout: float = 600.0) -> FastAPI:
    service = Service(store, build_timeout)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        service.close()

    app = FastAPI(title="plan2bim", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.dev_user = dev_user
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", PAGE_POLICY)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Not "no-referrer": the built-in authentication of Azure refuses a POST of a signed-in browser
        # that has no Referer of the same host (403, "Cross-site request forgery detected").
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(Problems)
    async def problems(_: Request, e: Problems):
        return JSONResponse({"problems": e.problems}, status_code=422)

    async def taken(request: Request) -> tuple[object, str, dict[str, BinaryIO]]:
        if int(request.headers.get("content-length") or 0) > MAX_REQUEST:
            raise HTTPException(413, f"a request can have {MAX_REQUEST >> 20} MB at most")
        form = await request.form(max_files=MAX_DRAWINGS, max_fields=4)
        try:
            definition = json.loads(form.get("definition") or "")       # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise HTTPException(400, "the definition must be JSON") from None
        more = form.get("more") or ""
        uploads = {f.filename: f.file for f in form.getlist("drawings") if isinstance(f, UploadFile) and f.filename}
        return definition, more if isinstance(more, str) else "", uploads

    def shown(meta: dict, owner: str) -> dict:
        return {**meta, "progress": service.running.get((owner, meta["id"]))}

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/api/me")
    def me(user: Signed) -> dict:
        return {"name": user.name, "sign_out": None if dev_user else "/.auth/logout"}

    @app.get("/api/defaults")
    def defaults(_: Signed) -> dict:
        heights = asdict(Heights(0.0))
        del heights["roof"]
        return {"heights": heights, "input": asdict(Input()), "max_drawing_mb": MAX_DRAWING >> 20}

    @app.get("/api/projects")
    def projects(user: Signed) -> list[dict]:
        return service.listing(user.id)

    @app.post("/api/projects", status_code=201, dependencies=[Depends(changes)])
    async def create(request: Request, user: Signed) -> dict:
        definition, more, uploads = await taken(request)
        return shown(await run_in_threadpool(service.save, user, None, definition, more, uploads), user.id)

    @app.get("/api/projects/{pid}")
    def project(pid: str, user: Signed) -> dict:
        return shown(service.load(user.id, pid), user.id)

    @app.put("/api/projects/{pid}", dependencies=[Depends(changes)])
    async def change(pid: str, request: Request, user: Signed) -> dict:
        definition, more, uploads = await taken(request)
        return shown(await run_in_threadpool(service.save, user, pid, definition, more, uploads), user.id)

    @app.delete("/api/projects/{pid}", status_code=204, dependencies=[Depends(changes)])
    def remove(pid: str, user: Signed) -> None:
        service.remove(user.id, pid)

    @app.get("/api/projects/{pid}/files/{name}")
    def file(pid: str, name: str, request: Request, user: Signed) -> Response:
        meta = service.load(user.id, pid)
        rel = meta.get("files", {}).get(name.replace("-", " ", 1) if name.startswith("plot-") else name)
        if meta["state"] != "done" or not rel:
            raise HTTPException(404, "there is no such file")
        tag = f'"{pid}-{meta["build"]}"'
        head = {"ETag": tag, "Cache-Control": "private, no-cache"}      # a browser asks each time; a build changes it
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=head)
        if name == "viewer":
            head["Content-Security-Policy"] = VIEWER_POLICY
        elif name == "ifc":
            head["Content-Disposition"] = f'attachment; filename="{meta["stem"]}.ifc"'
        try:
            content = service.store.chunks(f"{service._files(user.id, pid)}out/{rel}")
        except KeyError:
            raise HTTPException(404, "there is no such file") from None
        return StreamingResponse(content, media_type=TYPES[Path(rel).suffix], headers=head)

    def page(name: str):
        def send() -> FileResponse:     # no parameter: a parameter here is one that a request can set
            return FileResponse(SITE / name, headers={"Cache-Control": "no-cache"})
        return send

    for path, name in (("/", "index.html"), ("/app.js", "app.js"), ("/app.css", "app.css")):
        app.add_api_route(path, page(name), include_in_schema=False)
    return app
