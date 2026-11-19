import json
import runpy
import sys
from pathlib import Path

import pytest

from copaper_cli import __version__
from copaper_cli.cli import main, resolve_project
from copaper_cli.errors import CliError
from copaper_cli.files import Workspace
from copaper_cli.transport import Client

ID = "project0000000001"


def test_help_version_and_parser_errors(run_cli, server):
    for args in [("--help",), ("--version",), ("login", "--help")]:
        code, out, err = run_cli(*args)
        assert code == 0 and out and not err
    code, out, err = run_cli("nonesuch")
    assert code == 2 and "invalid choice" in err
    code, out, err = run_cli(server.key)
    assert code == 2 and server.key not in err
    assert run_cli("--timeout", "abc", "list")[0] == 2
    assert run_cli("--timeout", "0", "list")[0] == 1
    assert run_cli("--timeout=10", "list", "--json", "--verbose")[0] == 0


def test_login_key_stdin_prompt_and_auth_status(run_cli, server, store, monkeypatch):
    store.logout()
    assert run_cli("whoami")[0] == 1
    code, out, err = run_cli("login", "--key", "-", "--base-url", server.url, stdin=server.key + "\n")
    assert code == 0 and server.key not in out + err
    assert store.token(server.url) == server.key
    assert run_cli("auth", "status")[0] == 0
    assert run_cli("login", "status")[0] == 0
    monkeypatch.setattr("getpass.getpass", lambda *args: server.key)
    assert run_cli("auth", "--key")[0] == 0
    assert run_cli("login", "--key", server.key)[0] == 0
    assert run_cli("login", "--key", "invalid")[0] == 1
    original = store.path.read_bytes()
    assert run_cli("login", "--key", "sk-" + "0" * 64)[0] == 1
    assert store.path.read_bytes() == original
    assert run_cli("logout")[0] == 0
    assert "credential" not in store.load()


@pytest.mark.parametrize("minimum", ["10.12.0", None, "invalid"])
def test_upgrade_required_stops_commands_and_reports_versions(run_cli, server, minimum):
    server.override = lambda *args: (
        400,
        {},
        {
            "error": {
                "code": "cli_upgrade_required",
                "minimumCliVersion": minimum,
                "upgradeUrl": "https://untrusted.example.test/",
                "message": server.key,
            }
        },
    )
    code, out, err = run_cli("list")
    assert code == 1 and not out
    assert f"Copaper CLI {__version__}" in err and "升级" in err
    assert "https://github.com/huangshiyu13/copaper-cli" in err
    assert ("10.12.0" in err) == (minimum == "10.12.0")
    assert server.key not in err and "untrusted.example.test" not in err
    assert len(server.requests) == 1
    headers = {name.lower(): value for name, value in server.requests[0][3].items()}
    assert headers["user-agent"] == f"copaper-cli/{__version__}"


def test_config_and_origin_binding(run_cli, server, store):
    assert run_cli("config", "get-url")[0] == 0
    assert run_cli("config", "set-timeout", "45")[0] == 0
    assert store.load()["timeout"] == 45
    assert run_cli("config", "get-timeout")[0] == 0
    assert run_cli("config", "set-timeout", "0")[0] == 1
    assert run_cli("config", "set-timeout", "not-a-number")[0] == 1
    previous = len(server.requests)
    assert run_cli("whoami", "--base-url", "https://elsewhere.example")[0] == 1
    assert len(server.requests) == previous
    assert run_cli("config", "set-url", "https://elsewhere.example")[0] == 0
    assert run_cli("list")[0] == 1
    assert len(server.requests) == previous


def test_project_commands_and_resolution(run_cli, server, store):
    code, out, err = run_cli("list", "--json")
    assert code == 0 and len(json.loads(out)) == 2
    assert run_cli("list", "-n")[1].splitlines() == ["Paper One", "Paper Two"]
    code, out, err = run_cli("project", "create", "New Paper", "--template", "example")
    assert code == 0 and json.loads(out)["name"] == "New Paper"
    assert run_cli("project", "rename", "Renamed", "New Paper")[0] == 0
    assert run_cli("info", "Renamed")[0] == 0
    assert run_cli("info", "Paper")[0] == 1
    assert run_cli("info", "no-such-project")[0] == 1
    assert run_cli("info", "000000000000000000000000")[0] == 1
    assert run_cli("info", server.url + "/project/" + ID)[0] == 0
    assert run_cli("info", "https://other.example/project/" + ID)[0] == 1
    assert run_cli("info", server.url + "/project/" + ID + "?query=1")[0] == 1
    assert run_cli("info")[0] == 1
    assert run_cli("pull", "Paper One", "paper")[0] == 0
    workspace = Workspace(Path("paper"))
    assert resolve_project(Client(server.url, server.key), "paper one", workspace) == ID
    workspace.save("https://another.example", ID, {})
    with pytest.raises(CliError):
        resolve_project(Client(server.url, server.key), None, workspace)


def test_pull_push_sync_and_diff_workflow(run_cli, server, monkeypatch):
    assert run_cli("pull", "Paper One", "--dry-run")[0] == 0
    assert not Path("Paper One").exists()
    assert run_cli("pull", "Paper One")[0] == 0
    monkeypatch.chdir("Paper One")
    assert run_cli("info")[0] == 0
    Path("main.tex").write_text("local update\n")
    code, out, err = run_cli("diff", "--exit-code")
    assert code == 1 and "local update" in out
    assert run_cli("diff", "--name-only")[0] == 0
    assert run_cli("diff", "--json")[0] == 0
    assert run_cli("push", "--dry-run")[0] == 0
    assert server.projects[ID]["files"]["main.tex"] == b"base\n"
    assert run_cli("push")[0] == 0
    assert server.projects[ID]["files"]["main.tex"] == b"local update\n"
    server.projects[ID]["files"]["remote.tex"] = b"remote new"
    assert run_cli("sync", "--no-delete")[0] == 0
    assert Path("remote.tex").read_bytes() == b"remote new"
    assert run_cli("diff", "--exit-code")[0] == 0
    Path("main.tex").write_text("local again")
    server.projects[ID]["files"]["main.tex"] = b"remote conflict"
    assert run_cli("sync")[0] == 3
    assert run_cli("push", "--force")[0] == 0
    Path("remote.tex").unlink()
    assert run_cli("push", "--delete")[0] == 0
    assert "remote.tex" not in server.projects[ID]["files"]
    assert run_cli("pull")[0] == 0


def test_nonempty_directory_and_unbound_push(run_cli):
    Path("occupied").mkdir()
    Path("occupied/untracked.tex").write_text("keep")
    assert run_cli("pull", "Paper One", "occupied")[0] == 1
    assert run_cli("pull", "Paper One", "occupied", "--force")[0] == 0
    assert Path("occupied/untracked.tex").read_text() == "keep"
    assert run_cli("push")[0] == 1


def test_file_upload_download_rename_delete_and_zip(run_cli, server, store):
    Path("upload.bin").write_bytes(b"\0\xff\n")
    assert run_cli("upload", "upload.bin", "Paper One", "--to", "nested/data.bin")[0] == 0
    assert server.projects[ID]["files"]["nested/data.bin"] == b"\0\xff\n"
    assert run_cli("download", "nested/data.bin", "Paper One")[0] == 0
    assert Path("data.bin").read_bytes() == b"\0\xff\n"
    assert run_cli("download", "nested/data.bin", "Paper One")[0] == 1
    assert run_cli("download", "nested/data.bin", "Paper One", "--force")[0] == 0
    assert run_cli("download", "missing", "Paper One")[0] == 1
    assert run_cli("download", "nested/data.bin", "Paper One", "-o", str(store.path), "--force")[0] == 1
    assert run_cli("rename", "nested", "moved", "Paper One", "--dry-run")[0] == 0
    assert "nested/data.bin" in server.projects[ID]["files"]
    assert run_cli("mv", "nested", "moved", "Paper One")[0] == 0
    assert "moved/data.bin" in server.projects[ID]["files"]
    assert run_cli("rename", "moved/data.bin", "main.tex", "Paper One")[0] == 1
    assert run_cli("rename", "missing", "new", "Paper One")[0] == 1
    assert run_cli("delete", "moved", "Paper One", "--dry-run")[0] == 0
    assert run_cli("rm", "moved", "Paper One")[0] == 0
    assert "moved/data.bin" not in server.projects[ID]["files"]
    assert run_cli("delete", "missing", "Paper One")[0] == 1
    assert run_cli("zip", "Paper One", "-o", "source.zip")[0] == 0
    assert Path("source.zip").read_bytes().startswith(b"PK")
    assert run_cli("zip", "Paper One")[0] == 0
    assert run_cli("zip", "Paper One")[0] == 1
    Path("secret.tex").write_text(server.key)
    assert run_cli("upload", "secret.tex", "Paper One")[0] == 1
    Path("secret.key").write_text("private")
    assert run_cli("upload", "secret.key", "Paper One")[0] == 1
    assert run_cli("upload", str(store.path), "Paper One")[0] == 1


def test_compile_pdf_and_output(run_cli, server):
    assert run_cli("compile", "Paper One", "-r", "main.tex", "--compiler", "xelatex")[0] == 0
    assert run_cli("pdf", "Paper One", "-o", "paper.pdf")[0] == 0
    assert Path("paper.pdf").read_bytes().startswith(b"%PDF-")
    assert run_cli("pdf", "Paper One")[0] == 0
    assert run_cli("pdf", "Paper One", "--force")[0] == 0
    assert run_cli("pull", "Paper One", "paper")[0] == 0
    assert run_cli("output", "--list", "--project", "Paper One")[0] == 0
    assert run_cli("output", "log", "--project", "Paper One")[0] == 0
    assert Path("output.log").read_bytes() == b"compilation log\n"
    assert run_cli("output", "output.bbl", "--project", "Paper One")[0] == 0
    assert run_cli("output", "missing", "--project", "Paper One")[0] == 1
    server.compile_status = "failed"
    assert run_cli("compile", "Paper One")[0] == 2
    assert run_cli("pdf", "Paper One")[0] == 2
    server.compile_status = "success"
    server.pdf = b"bad data"
    assert run_cli("pdf", "Paper One", "--force")[0] == 1
    assert run_cli("ignored")[0] == 0


def test_invalid_download_and_zip(run_cli, server):
    def response(method, path, query, data, headers):
        if path.endswith("/files") or path.endswith("/zip"):
            return 200, {}, b"invalid"

    server.override = response
    assert run_cli("download", "main.tex", "Paper One")[0] == 1
    assert run_cli("zip", "Paper One")[0] == 1


def test_binary_diff(run_cli, server, monkeypatch):
    assert run_cli("pull", "Paper One", "paper")[0] == 0
    monkeypatch.chdir("paper")
    Path("images/plot.pdf").write_bytes(b"\0binary")
    assert "二进制" in run_cli("diff")[1]
    Path("new.tex").write_text("new")
    del server.projects[ID]["files"]["main.tex"]
    assert run_cli("diff")[0] == 0


def test_main_module_and_default_streams(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cp-cli", "--version"])
    assert main() == 0
    assert "cp-cli" in capsys.readouterr().out
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("copaper_cli", run_name="__main__")
    assert caught.value.code == 0


def test_interrupt_and_filesystem_errors(run_cli, monkeypatch, store):
    monkeypatch.setattr(store, "url", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert run_cli("list")[0] == 130
    monkeypatch.setattr(store, "url", lambda *args: (_ for _ in ()).throw(OSError("unavailable")))
    assert run_cli("list")[0] == 1


def test_login_does_not_persist_invalid_identity(run_cli, server, store):
    original = store.path.read_bytes()
    server.override = lambda *args: (200, {}, {"apiVersion": 2})
    assert run_cli("login", "--key", server.key)[0] == 1
    assert store.path.read_bytes() == original


def test_console_redacts_echoed_keys(run_cli, server):
    server.projects[ID]["name"] = server.key
    code, out, err = run_cli("list")
    assert code == 0 and server.key not in out + err and "[REDACTED]" in out


def test_id_resolution_with_restricted_listing(server, tmp_path, monkeypatch):
    client = Client(server.url, server.key)

    def forbidden():
        raise CliError("read not allowed", status=403)

    monkeypatch.setattr(client, "projects", forbidden)
    assert resolve_project(client, ID, Workspace(tmp_path.resolve())) == ID
    with pytest.raises(CliError):
        resolve_project(client, "short name", Workspace(tmp_path.resolve()))


def test_global_option_terminator(run_cli):
    assert run_cli("project", "create", "--", "--json")[0] == 0
