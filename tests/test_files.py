import pytest

from copaper_cli.config import DEFAULT_URL
from copaper_cli.errors import CliError
from copaper_cli.files import (
    MAX_FILE,
    MAX_FILES,
    MAX_PROJECT,
    Workspace,
    clean_path,
    file_hash,
    private_path,
    safe_name,
    validate_manifest,
)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/",
        "/etc/passwd",
        "a//b",
        "../a",
        "a/../b",
        "a/./b",
        "a\\b",
        "a\0b",
        "a\x7fb",
        "C:/file",
        "a" * 1001,
        None,
    ],
)
def test_unsafe_paths(name):
    with pytest.raises(CliError):
        clean_path(name)


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        "nested/.env.local",
        ".git/config",
        ".copaper/project.json",
        ".ssh/id_rsa",
        "data/secrets.json",
        "id_ed25519",
        "certificate.pem",
        "ssl.p12",
        ".npmrc",
    ],
)
def test_private_paths_are_blocked(tmp_path, name):
    workspace = Workspace(tmp_path.resolve())
    assert private_path(name)
    assert not workspace.included(name)
    with pytest.raises(CliError):
        workspace.path(name)


def test_ignore_and_scan(tmp_path):
    root = tmp_path.resolve()
    (root / ".cpignore").write_text("draft*\n!draft-keep.tex\n")
    (root / ".cpignore.local").write_text("local.tex\n")
    for name in [
        "main.tex",
        "draft-old.tex",
        "draft-keep.tex",
        "local.tex",
        "output.log",
        "output.pdf",
        "figure.pdf",
    ]:
        (root / name).write_text(name)
    (root / ".git").mkdir()
    (root / ".git/config").write_text("private")
    (root / "chapters").mkdir()
    (root / "chapters/a.tex").write_text("nested")
    files = Workspace(root).scan()
    assert set(files) == {".cpignore", "main.tex", "draft-keep.tex", "figure.pdf", "chapters/a.tex"}
    assert file_hash(b"main.tex") == files["main.tex"]


def test_manifest_validation(server):
    good = server.manifest("project0000000001")
    assert len(validate_manifest(good)) == 2
    invalid = [
        {},
        {"revision": "bad", "files": []},
        {"revision": "a" * 64, "files": {}},
        {"revision": "a" * 64, "files": [None]},
        {"revision": "a" * 64, "files": [{"path": "main.tex", "hash": "a" * 64, "size": -1}]},
        {"revision": "a" * 64, "files": [{"path": "main.tex", "hash": "bad", "size": 0}]},
        {"revision": "a" * 64, "files": [{"path": "main.tex", "hash": "a" * 64, "size": MAX_FILE + 1}]},
    ]
    for manifest in invalid:
        with pytest.raises(CliError):
            validate_manifest(manifest)
    duplicate = dict(good, files=good["files"] + good["files"])
    with pytest.raises(CliError):
        validate_manifest(duplicate)
    entry = {"path": "folder", "hash": "a" * 64, "size": 0}
    with pytest.raises(CliError):
        validate_manifest({"revision": "a" * 64, "files": [entry, dict(entry, path="folder/file")]})
    with pytest.raises(CliError):
        validate_manifest(
            {"revision": "a" * 64, "files": [dict(entry, path=str(i)) for i in range(MAX_FILES + 1)]}
        )
    with pytest.raises(CliError):
        validate_manifest(
            {
                "revision": "a" * 64,
                "files": [
                    dict(entry, path=str(i), size=MAX_FILE) for i in range(MAX_PROJECT // MAX_FILE + 1)
                ],
            }
        )


def test_workspace_state_and_safe_writes(tmp_path):
    workspace = Workspace(tmp_path.resolve())
    assert workspace.state() is None
    workspace.write("nested/main.tex", b"initial", None)
    digest = workspace.current_hash("nested/main.tex")
    with pytest.raises(CliError):
        workspace.write("nested/main.tex", b"overwritten", None)
    workspace.write("nested/main.tex", b"updated", digest)
    workspace.save(DEFAULT_URL, "project-one", {"nested/main.tex": file_hash(b"updated")})
    assert workspace.state()["project"] == "project-one"
    assert workspace.state_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(CliError):
        workspace.remove("nested/main.tex", digest)
    workspace.remove("nested/main.tex", file_hash(b"updated"))
    workspace.remove("absent.tex", None)
    assert workspace.current_hash("absent.tex") is None
    with pytest.raises(CliError):
        workspace.read("absent.tex")


def test_symlinks_and_protected_configuration(tmp_path):
    base = tmp_path.resolve()
    root, private = base / "project", base / "private"
    root.mkdir()
    private.mkdir()
    (private / "token").write_text("secret")
    (root / "leak").symlink_to(private, target_is_directory=True)
    with pytest.raises(CliError):
        Workspace(root / "leak")
    with pytest.raises(CliError):
        Workspace(root).scan()
    (root / "leak").unlink()
    (root / ".cpignore").symlink_to(private / "token")
    with pytest.raises(CliError):
        Workspace(root)
    (root / ".cpignore").unlink()
    (root / ".copaper").symlink_to(private, target_is_directory=True)
    with pytest.raises(CliError):
        Workspace(root).state()
    (root / ".copaper").unlink()
    (root / ".copaper").mkdir()
    (root / ".copaper/project.json").symlink_to(private / "token")
    with pytest.raises(CliError):
        Workspace(root).state()
    with pytest.raises(CliError):
        Workspace(private, private)
    with pytest.raises(CliError):
        Workspace(private / "inside", private)
    assert Workspace(base, private).scan() == {}
    assert not Workspace(base, private).included("private/token")
    with pytest.raises(CliError):
        Workspace(base, private).path("private/token")


@pytest.mark.parametrize(
    "content",
    ["invalid", "[]", "{}", '{"version":1,"project":"id","url":"https://example.test","files":{"x":"bad"}}'],
)
def test_invalid_project_state(tmp_path, content):
    workspace = Workspace(tmp_path.resolve())
    workspace.state_path.parent.mkdir()
    workspace.state_path.write_text(content)
    with pytest.raises(CliError):
        workspace.state()


def test_large_local_files_and_project_limits(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "a.tex").write_bytes(b"abc")
    monkeypatch.setattr("copaper_cli.files.MAX_FILE", 2)
    with pytest.raises(CliError):
        Workspace(root).read("a.tex")
    monkeypatch.setattr("copaper_cli.files.MAX_FILE", 100)
    monkeypatch.setattr("copaper_cli.files.MAX_PROJECT", 2)
    with pytest.raises(CliError):
        Workspace(root).scan()
    monkeypatch.setattr("copaper_cli.files.MAX_PROJECT", 100)
    monkeypatch.setattr("copaper_cli.files.MAX_FILES", 0)
    with pytest.raises(CliError):
        Workspace(root).scan()


def test_directory_replacement_and_partial_write_protection(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "folder/sub").mkdir(parents=True)
    workspace = Workspace(root)
    workspace.write("folder", b"file instead", None)
    assert (root / "folder").read_bytes() == b"file instead"
    (root / "nonempty").mkdir()
    (root / "nonempty/untracked").write_text("keep")
    with pytest.raises(CliError):
        workspace.write("nonempty", b"no", None)
    real = workspace.current_hash
    calls = 0

    def changing(name):
        nonlocal calls
        calls += 1
        return real(name) if calls == 1 else "concurrent"

    monkeypatch.setattr(workspace, "current_hash", changing)
    with pytest.raises(CliError):
        workspace.write("next.tex", b"new", None)
    assert not (root / "next.tex").exists()
    assert list(root.glob(".cp-*")) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (" A/B:Paper. ", "A_B_Paper"),
        ("..", "project"),
        ("CON", "_CON"),
        ("LPT2", "_LPT2"),
        ("Normal Paper", "Normal Paper"),
        ("x" * 200, "x" * 180),
    ],
)
def test_safe_directory_names(name, expected):
    assert safe_name(name) == expected
