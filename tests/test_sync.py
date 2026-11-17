import pytest

from copaper_cli.errors import CliError
from copaper_cli.files import Workspace, file_hash
from copaper_cli.sync import plan, remote_files, synchronize
from copaper_cli.transport import Client


@pytest.mark.parametrize("base", [None, "base", "shared"])
@pytest.mark.parametrize("local", [None, "base", "shared", "local"])
@pytest.mark.parametrize("remote", [None, "base", "shared", "remote"])
def test_sync_three_way_safety(base, local, remote):
    def mapping(value):
        return {"file": value} if value is not None else {}

    result = plan("sync", mapping(base), mapping(local), mapping(remote))
    if local == remote:
        assert not any(result.values())
    elif local == base:
        assert result["download" if remote is not None else "remove_local"] == ["file"]
    elif remote == base:
        assert result["upload" if local is not None else "remove_remote"] == ["file"]
    else:
        assert result["conflicts"] == ["file"]
        assert not result["upload"] and not result["download"]


@pytest.mark.parametrize(
    ("mode", "base", "local", "remote", "options", "category"),
    [
        ("pull", "a", "b", "a", {}, None),
        ("pull", "a", "b", "c", {}, "conflicts"),
        ("pull", "a", "a", None, {}, "remove_local"),
        ("pull", None, "a", None, {"force": True}, None),
        ("pull", "a", "b", "a", {"force": True}, "download"),
        ("push", "a", None, "a", {}, None),
        ("push", "a", None, "a", {"delete": True}, "remove_remote"),
        ("push", "a", "a", "b", {}, None),
        ("push", "a", "b", "c", {}, "conflicts"),
        ("push", "a", "b", "c", {"force": True}, "upload"),
        ("sync", "a", None, "a", {"no_delete": True}, None),
        ("sync", "a", None, "b", {"no_delete": True}, "download"),
    ],
)
def test_push_pull_and_deletion_rules(mode, base, local, remote, options, category):
    def mapping(value):
        return {"file": value} if value is not None else {}

    result = plan(mode, mapping(base), mapping(local), mapping(remote), **options)
    assert result[category] == ["file"] if category else not any(result.values())


def setup_workspace(tmp_path, server):
    workspace = Workspace(tmp_path.resolve() / "paper")
    workspace.root.mkdir()
    client = Client(server.url, server.key)
    synchronize(client, workspace, "project0000000001", "pull")
    return client, workspace, server.projects["project0000000001"]["files"]


def test_real_transport_sync_preserves_binary_and_disjoint_changes(tmp_path, server):
    client, workspace, remote = setup_workspace(tmp_path, server)
    assert workspace.read("images/plot.pdf") == remote["images/plot.pdf"]
    (workspace.root / "main.tex").write_bytes(b"local edit\n")
    remote["new.tex"] = b"coworker edit"
    result = synchronize(client, workspace, "project0000000001", "sync", dry_run=True)
    assert result["upload"] == ["main.tex"] and result["download"] == ["new.tex"]
    assert remote["main.tex"] == b"base\n"
    synchronize(client, workspace, "project0000000001", "sync")
    assert remote["main.tex"] == b"local edit\n"
    assert workspace.read("new.tex") == b"coworker edit"
    assert workspace.state()["files"]["main.tex"] == file_hash(b"local edit\n")


def test_conflicts_do_not_overwrite_either_side(tmp_path, server):
    client, workspace, remote = setup_workspace(tmp_path, server)
    (workspace.root / "main.tex").write_bytes(b"ours")
    remote["main.tex"] = b"theirs"
    with pytest.raises(CliError) as caught:
        synchronize(client, workspace, "project0000000001", "sync")
    assert caught.value.exit_code == 3
    assert workspace.read("main.tex") == b"ours" and remote["main.tex"] == b"theirs"
    synchronize(client, workspace, "project0000000001", "push", force=True)
    assert remote["main.tex"] == b"ours"


def test_push_does_not_acknowledge_unpulled_remote_edits(tmp_path, server):
    client, workspace, remote = setup_workspace(tmp_path, server)
    remote["main.tex"] = b"remote later edit"
    synchronize(client, workspace, "project0000000001", "push")
    assert workspace.state()["files"]["main.tex"] == file_hash(b"base\n")
    (workspace.root / "main.tex").write_bytes(b"local later edit")
    with pytest.raises(CliError):
        synchronize(client, workspace, "project0000000001", "push")
    assert remote["main.tex"] == b"remote later edit"


def test_deletions_and_no_delete(tmp_path, server):
    client, workspace, remote = setup_workspace(tmp_path, server)
    (workspace.root / "main.tex").unlink()
    synchronize(client, workspace, "project0000000001", "sync", no_delete=True)
    assert "main.tex" in remote and not (workspace.root / "main.tex").exists()
    synchronize(client, workspace, "project0000000001", "push", delete=True)
    assert "main.tex" not in remote
    del remote["images/plot.pdf"]
    synchronize(client, workspace, "project0000000001", "sync")
    assert not (workspace.root / "images/plot.pdf").exists()
    assert workspace.state()["files"] == {}


def test_wrong_site_or_project_is_rejected(tmp_path, server):
    client, workspace, _ = setup_workspace(tmp_path, server)
    with pytest.raises(CliError):
        synchronize(client, workspace, "project0000000002", "pull")
    client.url = "https://other.example"
    with pytest.raises(CliError):
        synchronize(client, workspace, "project0000000001", "pull")


def test_secret_in_file_is_never_uploaded(tmp_path, server):
    client, workspace, remote = setup_workspace(tmp_path, server)
    (workspace.root / "main.tex").write_text(server.key)
    with pytest.raises(CliError):
        synchronize(client, workspace, "project0000000001", "push")
    assert remote["main.tex"] == b"base\n"


def test_download_corruption_and_remote_revision_change(tmp_path, server, monkeypatch):
    client, workspace, remote = setup_workspace(tmp_path, server)
    remote["main.tex"] = b"new"
    original = client.download
    monkeypatch.setattr(client, "download", lambda *args: b"corrupt")
    with pytest.raises(CliError):
        synchronize(client, workspace, "project0000000001", "pull")
    assert workspace.read("main.tex") == b"base\n"

    def change_after_download(*args):
        value = original(*args)
        remote["other.tex"] = b"concurrent"
        return value

    monkeypatch.setattr(client, "download", change_after_download)
    with pytest.raises(CliError):
        synchronize(client, workspace, "project0000000001", "pull")
    assert workspace.read("main.tex") == b"base\n"


def test_stale_local_content_and_concurrent_apply_are_rejected(tmp_path, server, monkeypatch):
    client, workspace, remote = setup_workspace(tmp_path, server)
    (workspace.root / "main.tex").write_bytes(b"new local")
    original_upload = client.upload_blob

    def change_after_upload(*args):
        original_upload(*args)
        (workspace.root / "other.tex").write_bytes(b"local changed")

    monkeypatch.setattr(client, "upload_blob", change_after_upload)
    with pytest.raises(CliError):
        synchronize(client, workspace, "project0000000001", "push")
    assert remote["main.tex"] == b"base\n"
    (workspace.root / "other.tex").unlink()

    def remote_change(*args):
        original_upload(*args)
        remote["concurrent.tex"] = b"coworker"

    monkeypatch.setattr(client, "upload_blob", remote_change)
    with pytest.raises(CliError) as caught:
        synchronize(client, workspace, "project0000000001", "push")
    assert caught.value.status == 409
    assert remote["main.tex"] == b"base\n"


def test_file_changes_before_upload_abort(tmp_path, server, monkeypatch):
    client, workspace, _ = setup_workspace(tmp_path, server)
    (workspace.root / "main.tex").write_text("new")
    original = workspace.read

    def read_changed(name):
        content = original(name)
        if name == "main.tex":
            (workspace.root / "main.tex").write_text("later")
        return content

    monkeypatch.setattr(workspace, "read", read_changed)
    with pytest.raises(CliError):
        synchronize(client, workspace, "project0000000001", "push")


def test_ignored_remote_paths_are_not_written(tmp_path, server):
    server.projects["project0000000001"]["files"][".env"] = b"private"
    client, workspace, _ = setup_workspace(tmp_path, server)
    assert ".env" not in remote_files(workspace, client.manifest("project0000000001"))
    assert not (workspace.root / ".env").exists()
