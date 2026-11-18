import json
import secrets


def test_review_request_contract(run_cli, server, monkeypatch):
    project = "project0000000001"
    base = "/projects/" + project
    thread = secrets.token_hex(12)
    reply = secrets.token_hex(12)
    member = secrets.token_hex(12)
    resource = base + "/comments/" + thread
    received = []
    thread_data = {"id": thread, "messages": [{"content": "第一条评论"}]}

    def transport(method, path, query, data, headers):
        if path != base + "/members" and not path.startswith(base + "/comments"):
            return None
        assert headers["Authorization"] == "Bearer " + server.key
        request = json.loads(data) if data else None
        received.append((method, path, query, request))
        if path == base + "/members":
            return 200, {}, {"members": [{"id": member, "name": "林 同学"}]}
        if (method, path) == ("GET", base + "/comments"):
            return 200, {}, {"threads": [thread_data]}
        return 200, {}, thread_data

    server.override = transport
    assert run_cli("comments", "members", "Paper One")[0] == 0
    assert json.loads(run_cli("comments", "list", "Paper One", "--status", "open", "--file", "论文.tex")[1]) == [thread_data]
    assert received[-1] == ("GET", base + "/comments", {"status": ["open"], "path": ["论文.tex"]}, None)
    assert run_cli("pull", "Paper One", "paper")[0] == 0
    monkeypatch.chdir("paper")
    assert run_cli("comments", "list")[0] == 0
    assert received[-1][2] == {"status": ["all"]}
    assert run_cli("comment", "create", "main.tex", "  请看 @{林 同学}  ", "--from", "2", "--to", "3", "--id", thread)[0] == 0
    assert received[-1] == (
        "POST", base + "/comments", {},
        {"path": "main.tex", "from": 2, "to": 3, "content": f"请看 @[{member}]", "id": thread},
    )
    assert run_cli("comments", "add", "main.tex", "Fix", "--quote", "被引用", "--project", "Paper One")[0] == 0
    assert received[-1][3] == {"path": "main.tex", "quote": "被引用", "content": "Fix"}
    assert run_cli("comments", "show", thread)[0] == 0
    assert received[-1][:2] == ("GET", resource)
    assert run_cli("comments", "reply", thread, f"Done @[{member}]")[0] == 0
    assert received[-1] == ("POST", resource + "/replies", {}, {"content": f"Done @[{member}]"})
    assert run_cli("comments", "edit", thread, "Updated")[0] == 0
    assert received[-1] == ("PATCH", resource, {}, {"content": "Updated"})
    assert run_cli("comments", "edit", thread, "回复已改", "--message-id", reply)[0] == 0
    assert received[-1][3] == {"content": "回复已改", "messageId": reply}
    assert run_cli("comments", "delete", thread, "--message-id", reply)[0] == 0
    assert received[-1] == ("DELETE", resource, {"messageId": [reply]}, None)
    for action in ("resolve", "reopen"):
        assert run_cli("comments", action, thread)[0] == 0
        assert received[-1] == ("POST", f"{resource}/{action}", {}, None)
    assert run_cli("comments", "delete", thread)[0] == 0
    assert received[-1] == ("DELETE", resource, {}, None)


def test_review_validates_inputs_and_reports_ambiguous_members(run_cli, server):
    thread = secrets.token_hex(12)
    name = ("--project", "Paper One")
    for args, reason in (
        (("add", "main.tex", "Text"), "指定起始行"),
        (("add", "main.tex", "Text", "--from", "0"), "行号"),
        (("add", "main.tex", "Text", "--to", "4", "--quote", "Word"), "--to"),
        (("add", "main.tex", "Text", "--from", "4", "--to", "3"), "--to"),
        (("show", "nope"), "十六进制"),
        (("reply", thread, "  "), "填写评论"),
        (("reply", thread, server.key), "含有当前 API Key"),
    ):
        code, out, err = run_cli("comments", *args, *name)
        assert code == 1 and reason in err and not out
        assert server.key not in err

    def members(_method, path, _query, _data, _headers):
        if path.endswith("/members"):
            return 200, {}, {"members": [
                {"id": secrets.token_hex(12), "name": "Alex"},
                {"id": secrets.token_hex(12), "name": "Alex"},
            ]}
        return None

    server.override = members
    for person in ("Alex", "Someone else"):
        code, _, error = run_cli("comments", "reply", thread, f"Hi @{{{person}}}", *name)
        assert code == 1 and "comments members" in error
    assert all(not path.endswith("/replies") for _, path, _, _ in server.requests)
