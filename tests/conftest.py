import hashlib
import io
import json
import secrets
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from copaper_cli.cli import main
from copaper_cli.config import Store


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self):
        self.key = "sk-" + secrets.token_hex(32)
        self.projects = {
            "project0000000001": {
                "name": "Paper One",
                "files": {"main.tex": b"base\n", "images/plot.pdf": b"%PDF-fixture"},
                "blobs": {},
            },
            "project0000000002": {"name": "Paper Two", "files": {}, "blobs": {}},
        }
        self.requests = []
        self.override = None
        self.compile_status = "success"
        self.pdf = b"%PDF-1.4\nfixture\n%%EOF"
        self.pagination = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                path = urlsplit(self.path)
                endpoint = path.path.removeprefix("/api/v1")
                query = parse_qs(path.query)
                data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                fixture.requests.append((self.command, endpoint, data, dict(self.headers)))
                if fixture.override:
                    result = fixture.override(self.command, endpoint, query, data, self.headers)
                    if result is not None:
                        status, headers, body = result
                        self.respond(status, body, headers)
                        return
                if self.headers.get("Authorization") != f"Bearer {fixture.key}":
                    self.respond(401, {"error": {"message": "unauthorized"}})
                    return
                if endpoint == "/me":
                    self.respond(
                        200,
                        {
                            "user": {"id": "user-one", "email": "collaborator@example.test"},
                            "key": {"scopes": ["read", "write", "compile"]},
                            "apiVersion": 1,
                        },
                    )
                elif endpoint == "/projects" and self.command == "POST":
                    body = json.loads(data)
                    identifier = "project0000000003"
                    fixture.projects[identifier] = {
                        "name": body["name"],
                        "files": {"main.tex": b"new"},
                        "blobs": {},
                    }
                    self.respond(201, {"id": identifier, "name": body["name"]})
                elif endpoint == "/projects":
                    items = [{"id": key, "name": value["name"]} for key, value in fixture.projects.items()]
                    if fixture.pagination:
                        items = items[1:] if query.get("cursor") else items[:1]
                    self.respond(
                        200,
                        {
                            "projects": items,
                            "nextCursor": "next" if fixture.pagination and not query.get("cursor") else None,
                        },
                    )
                else:
                    parts = [unquote(part) for part in endpoint.split("/") if part]
                    identifier = parts[1] if len(parts) > 1 else ""
                    project = fixture.projects.get(identifier)
                    if project is None:
                        self.respond(404, {"error": {"message": "not found"}})
                    elif len(parts) == 2 and self.command == "PATCH":
                        project["name"] = json.loads(data)["name"]
                        self.respond(200, {"id": identifier, "name": project["name"]})
                    elif len(parts) == 2:
                        self.respond(200, fixture.manifest(identifier))
                    elif parts[2] == "files":
                        name = query.get("path", [""])[0]
                        content = project["files"].get(name)
                        if content is None:
                            self.respond(404, {"error": {"message": "missing"}})
                        elif self.headers.get("If-Match") != '"' + sha(content) + '"':
                            self.respond(412, {"error": {"message": "changed"}})
                        else:
                            self.respond(200, content)
                    elif parts[2] == "blobs":
                        if sha(data) != parts[3]:
                            self.respond(400, {"error": {"message": "hash mismatch"}})
                        else:
                            project["blobs"][parts[3]] = data
                            self.respond(200, {"hash": sha(data), "size": len(data)})
                    elif parts[2] == "apply":
                        body = json.loads(data)
                        if body["revision"] != fixture.manifest(identifier)["revision"]:
                            self.respond(409, {"error": {"message": "changed"}})
                        else:
                            existing = {sha(value): value for value in project["files"].values()}
                            for change in body["changes"]:
                                if change["hash"] is None:
                                    project["files"].pop(change["path"], None)
                                else:
                                    project["files"][change["path"]] = project["blobs"].get(
                                        change["hash"], existing.get(change["hash"])
                                    )
                            self.respond(200, fixture.manifest(identifier))
                    elif parts[2] == "compile":
                        self.respond(200, fixture.build())
                    elif parts[2] == "outputs" and parts[3] == "latest":
                        self.respond(200, fixture.build())
                    elif parts[2] == "outputs":
                        self.respond(200, fixture.pdf if parts[4] == "output.pdf" else b"compilation log\n")
                    elif parts[2] == "zip":
                        stream = io.BytesIO()
                        with zipfile.ZipFile(stream, "w") as archive:
                            for name, content in project["files"].items():
                                archive.writestr(name, content)
                        self.respond(200, stream.getvalue())
                    else:
                        self.respond(404, {})

            def respond(self, status, value, headers=None):
                data = json.dumps(value).encode() if isinstance(value, (dict, list)) else value
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                if not headers or "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = handle_request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()

    def manifest(self, project):
        value = self.projects[project]
        files = [
            {"path": name, "hash": sha(data), "size": len(data)}
            for name, data in sorted(value["files"].items())
        ]
        return {
            "id": project,
            "name": value["name"],
            "revision": sha(json.dumps(files).encode()),
            "files": files,
        }

    def build(self):
        return {
            "id": "build-one",
            "status": self.compile_status,
            "files": [{"name": "output.pdf"}, {"name": "output.log"}, {"name": "output.bbl"}],
        }

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    fixture = Fixture()
    yield fixture
    fixture.close()


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path.resolve() / "configuration", environ={})


@pytest.fixture
def run_cli(server, store, monkeypatch, tmp_path):
    root = tmp_path.resolve() / "work"
    root.mkdir()
    monkeypatch.chdir(root)
    store.login(server.url, server.key)

    def run(*args, stdin=""):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(list(args), stdout=stdout, stderr=stderr, stdin=io.StringIO(stdin), store=store)
        return code, stdout.getvalue(), stderr.getvalue()

    return run
