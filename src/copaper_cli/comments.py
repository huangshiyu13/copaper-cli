"""Project review commands."""

import re
from urllib.parse import urlencode

from .errors import CliError
from .files import clean_path
from .transport import Client

MEMBER_REFERENCE = re.compile(r"@\{([^{}\r\n]+)\}")


def register_comments(commands) -> None:
    review = commands.add_parser("comments", aliases=["comment"], help="读取和管理项目审阅评论")
    actions = review.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="列出评论及回复")
    listing.add_argument("project", nargs="?")
    listing.add_argument("--status", choices=["all", "open", "resolved"], default="all")
    listing.add_argument("--file", help="仅显示指定文件的评论")
    actions.add_parser("members", help="列出可提及的项目成员").add_argument("project", nargs="?")

    add = actions.add_parser("add", aliases=["create"], help="在文档选区创建评论")
    add.add_argument("file")
    add.add_argument("message")
    add.add_argument("--project", help="项目名称、ID 或链接，省略时使用当前目录绑定")
    add.add_argument("--from", dest="start_line", type=int, help="起始行，从 1 起算")
    add.add_argument("--to", dest="end_line", type=int, help="结束行，包含该行")
    add.add_argument("--quote", help="在文档或指定行内匹配一段原文")
    add.add_argument("--id", help="可选的 24 位十六进制线程 ID，用于重试")

    for action, help_text in (
        ("show", "查看评论线程"),
        ("reply", "回复评论线程"),
        ("edit", "修改自己的评论或回复"),
        ("delete", "删除评论线程或指定回复"),
        ("resolve", "标记评论已解决"),
        ("reopen", "重新打开已解决的评论"),
    ):
        command = actions.add_parser(action, help=help_text)
        command.add_argument("thread")
        command.add_argument("--project", help="项目名称、ID 或链接，省略时使用当前目录绑定")
        if action in {"reply", "edit"}:
            command.add_argument("message")
        if action in {"edit", "delete"}:
            command.add_argument("--message-id", help="指定要操作的回复 ID")


def identifier(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{24}", value):
        raise CliError("评论或回复 ID 必须是 24 位小写十六进制字符")
    return value


def message_body(client: Client, base: str, value: str) -> dict:
    value = value.strip()
    if not value:
        raise CliError("请填写评论内容")
    if client.key in value:
        raise CliError("评论含有当前 API Key，已阻止发送")
    if MEMBER_REFERENCE.search(value):
        members = client.request("GET", base + "/members")["members"]

        def mention(match):
            name = match[1].strip().casefold()
            matches = [item["id"] for item in members if item["name"].casefold() == name]
            if len(matches) != 1:
                raise CliError("成员姓名不存在或重名，请用 comments members 查询，并使用 @[成员ID]")
            return "@[" + identifier(matches[0]) + "]"

        value = MEMBER_REFERENCE.sub(mention, value)
    return {"content": value}


def execute_comments(args, client: Client, base: str) -> dict | list:
    threads = base + "/comments"
    action = args.action
    if action == "members":
        return client.request("GET", base + "/members")["members"]
    if action == "list":
        query = {"status": args.status}
        if args.file is not None:
            query["path"] = clean_path(args.file)
        return client.request("GET", threads + "?" + urlencode(query))["threads"]
    if action in {"add", "create"}:
        if args.start_line is None and args.quote is None:
            raise CliError("请用 --from 指定起始行，或用 --quote 指定原文片段")
        if args.start_line is not None and args.start_line < 1:
            raise CliError("行号从 1 起算")
        if args.end_line is not None and (args.start_line is None or args.end_line < args.start_line):
            raise CliError("--to 必须与 --from 一起使用，且不小于起始行")
        path = clean_path(args.file)
        selection = {
            name: entry
            for name, entry in (("from", args.start_line), ("to", args.end_line), ("quote", args.quote))
            if entry is not None
        }
        if args.id is not None:
            selection["id"] = identifier(args.id)
        body = message_body(client, base, args.message)
        return client.request("POST", threads, {"path": path, **selection, **body})

    thread = threads + "/" + identifier(args.thread)
    if action == "show":
        return client.request("GET", thread)
    if action == "reply":
        return client.request("POST", thread + "/replies", message_body(client, base, args.message))
    if action == "edit":
        body = message_body(client, base, args.message)
        if args.message_id is not None:
            body["messageId"] = identifier(args.message_id)
        return client.request("PATCH", thread, body)
    if action == "delete":
        if args.message_id is not None:
            thread += "?" + urlencode({"messageId": identifier(args.message_id)})
        return client.request("DELETE", thread)
    return client.request("POST", thread + "/" + action)
