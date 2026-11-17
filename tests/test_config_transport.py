import io
import os
import socket
import urllib.error
from pathlib import Path

import pytest

from copaper_cli.config import DEFAULT_URL, Store, atomic_json, check_directory, normalize_url, validate_key
from copaper_cli.errors import CliError, redact
from copaper_cli.transport import Client, NoRedirect


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://EXAMPLE.test:443/", "https://example.test"),
        ("https://example.test/latex/", "https://example.test/latex"),
        ("http://localhost:1234", "http://localhost:1234"),
        ("http://127.0.0.2", "http://127.0.0.2"),
        ("http://[::1]:8080/", "http://[::1]:8080"),
        ("https://example.test:8443", "https://example.test:8443"),
    ],
)
def test_urls(source, expected):
    assert normalize_url(source) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        " http://localhost",
        "https://a.test/\nx",
        "https://a.test\\x",
        "http://public.example",
        "ftp://example.test",
        "https://user:password@example.test",
        "https://example.test?q=1",
        "https://example.test/#fragment",
        "https://example.test:invalid",
        "https://example.test/%2e%2e",
        "https://",
        "http://localhost.example",
    ],
)
def test_bad_urls(url):
    with pytest.raises(CliError):
        normalize_url(url)


def test_key_and_output_redaction(server):
    assert validate_key(server.key) == server.key
    for value in ["", "sk-short", "invalid", server.key + "\n", None]:
        with pytest.raises(CliError):
            validate_key(value)
    assert server.key not in redact({"key": server.key})
    assert redact("Bearer arbitrary") == "Bearer [REDACTED]"
    assert redact("extra other-secret", ("other-secret", "")) == "extra [REDACTED]"
    error = CliError("changed", 3, 409)
    assert error.exit_code == 3 and error.status == 409


def test_store_defaults_login_and_logout(store, server):
    assert store.load()["url"] == DEFAULT_URL
    assert store.url("https://example.test/") == "https://example.test"
    with pytest.raises(CliError):
        store.token(server.url)
    store.login(server.url, server.key)
    assert store.token(server.url) == server.key
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.directory.stat().st_mode & 0o777 == 0o700
    store.set("timeout", 30)
    assert store.load()["timeout"] == 30
    with pytest.raises(CliError):
        store.token("https://other.example")
    store.logout()
    assert "credential" not in store.load()
    store.logout()


def test_store_environment_and_directories(tmp_path, server):
    directory = tmp_path.resolve() / "config"
    store = Store(
        environ={
            "COPAPER_CONFIG_DIR": str(directory),
            "COPAPER_BASE_URL": server.url,
            "COPAPER_API_KEY": server.key,
        }
    )
    assert store.url() == server.url
    assert store.token(server.url) == server.key
    with pytest.raises(CliError):
        store.token("https://another.example")
    assert Store(environ={"XDG_CONFIG_HOME": str(directory)}).directory == directory / "copaper"
    assert Store(environ={"LOCALAPPDATA": str(directory)}).directory == directory / "copaper"
    assert Store(environ={}).directory == Path.home() / ".config" / "copaper"
    store.logout()


@pytest.mark.parametrize(
    "content", ["[1]", "invalid JSON", '{"timeout":0}', '{"timeout":"bad"}', '{"timeout":601}']
)
def test_corrupt_configuration(store, content):
    check_directory(store.directory)
    store.path.write_text(content)
    store.path.chmod(0o600)
    with pytest.raises(CliError):
        store.load()


def test_configuration_permissions_and_symlinks(store, server, tmp_path):
    store.login(server.url, server.key)
    store.path.chmod(0o644)
    with pytest.raises(CliError):
        store.load()
    store.path.unlink()
    external = tmp_path / "external"
    external.write_text("keep")
    store.path.symlink_to(external)
    for action in [store.load, store.logout, lambda: atomic_json(store.path, {})]:
        with pytest.raises(CliError):
            action()
    assert external.read_text() == "keep"
    store.path.unlink()
    store.directory.rmdir()
    store.directory.symlink_to(tmp_path.resolve(), target_is_directory=True)
    with pytest.raises(CliError):
        check_directory(store.directory)
    with pytest.raises(CliError):
        store.load()


def test_wrong_owner(store, monkeypatch):
    check_directory(store.directory)
    monkeypatch.setattr(os, "getuid", lambda: store.directory.stat().st_uid + 1)
    with pytest.raises(CliError):
        check_directory(store.directory)


def test_atomic_configuration_failure_preserves_original(store, monkeypatch):
    atomic_json(store.path, {"url": DEFAULT_URL})
    previous = store.path.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *args: (_ for _ in ()).throw(OSError("blocked")))
    with pytest.raises(OSError):
        atomic_json(store.path, {"credential": "new"})
    assert store.path.read_bytes() == previous
    assert list(store.directory.glob(".cp-*")) == []


def test_transport_me_binary_requests_and_pagination(server):
    trace = []
    client = Client(server.url, server.key, verbose=trace.append)
    assert client.request("GET", "/me")["apiVersion"] == 1
    server.pagination = True
    assert len(client.projects()) == 2
    digest = server.manifest("project0000000001")["files"][0]
    assert client.download("project0000000001", digest["path"], digest["hash"])
    assert all(server.key not in line for line in trace)
    assert client.project_path("safe_id-1") == "/projects/safe_id-1"
    for value in ["", "a/b", "..", "bad value", None]:
        with pytest.raises(CliError):
            client.project_path(value)
    for endpoint in ["https://elsewhere.test", "//elsewhere", "relative"]:
        with pytest.raises(CliError):
            client.request("GET", endpoint)
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test") is None


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {"error": {"message": "bad"}}, 1),
        (403, {"error": {"message": "permission"}}, 1),
        (409, {"error": {}}, 3),
        (412, {}, 3),
        (429, b"not json", 1),
        (500, [1], 1),
        (302, b"", 1),
    ],
)
def test_http_errors(server, status, body, code):
    server.override = lambda *args: (status, {"Location": "https://other.test/"}, body)
    with pytest.raises(CliError) as caught:
        Client(server.url, server.key).request("GET", "/me")
    assert caught.value.exit_code == code
    assert caught.value.status == status
    assert len(server.requests) == 1


def test_error_message_never_echoes_key(server):
    server.override = lambda *args: (400, {}, {"error": {"message": server.key}})
    with pytest.raises(CliError) as caught:
        Client(server.url, server.key).request("GET", "/me")
    assert server.key not in str(caught.value)


@pytest.mark.parametrize(
    ("headers", "body", "binary", "limit"),
    [
        ({"Content-Length": "nope"}, b"", False, 10),
        ({"Content-Length": "9999999"}, b"", False, 10),
        ({}, b"bad json", False, 10),
        ({}, b"[1]", False, 10),
        ({}, b"12345678901", True, 10),
    ],
)
def test_invalid_responses(server, headers, body, binary, limit):
    server.override = lambda *args: (200, headers, body)
    with pytest.raises(CliError):
        Client(server.url, server.key).request("GET", "/me", binary=binary, limit=limit)


def test_stream_without_content_length_is_bounded(server):
    client = Client(server.url, server.key)

    class Reply(io.BytesIO):
        headers = {}

    client.opener.open = lambda *args, **kwargs: Reply(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(CliError):
        client.request("GET", "/me")


@pytest.mark.parametrize("error", [urllib.error.URLError("offline"), socket.timeout(), OSError("offline")])
def test_network_errors(server, error):
    client = Client(server.url, server.key)
    client.opener.open = lambda *args, **kwargs: (_ for _ in ()).throw(error)
    with pytest.raises(CliError):
        client.request("GET", "/me")


def test_bad_pagination(server):
    client = Client(server.url, server.key)
    server.override = lambda *args: (200, {}, {"projects": "bad"})
    with pytest.raises(CliError):
        client.projects()
    server.override = lambda *args: (200, {}, {"projects": [], "nextCursor": "same"})
    with pytest.raises(CliError):
        client.projects()
    server.override = lambda *args: (200, {}, {"projects": [], "nextCursor": 3})
    with pytest.raises(CliError):
        client.projects()


def test_invalid_stored_credentials(store):
    for credential in [None, "invalid", {"url": DEFAULT_URL}, {"url": DEFAULT_URL, "key": "bad"}]:
        atomic_json(store.path, {"url": DEFAULT_URL, "credential": credential})
        with pytest.raises(CliError):
            store.token(DEFAULT_URL)


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"apiVersion": 2},
        {"apiVersion": 1, "user": None, "key": {}},
        {"apiVersion": 1, "user": {"id": "one"}, "key": {"scopes": []}},
        {"apiVersion": 1, "user": {"id": "one", "email": "you@example.test"}, "key": {"scopes": "read"}},
    ],
)
def test_invalid_identity_is_rejected(server, identity):
    server.override = lambda *args: (200, {}, identity)
    with pytest.raises(CliError):
        Client(server.url, server.key).identity()
