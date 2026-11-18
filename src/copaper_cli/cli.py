"""The cp-cli command-line interface."""

import argparse
import difflib
import getpass
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import __version__
from .comments import execute_comments, register_comments
from .config import Store, normalize_url, validate_key
from .errors import CliError, redact
from .files import (
    Workspace,
    clean_path,
    file_hash,
    private_path,
    safe_name,
    validate_manifest,
)
from .sync import remote_files, synchronize
from .transport import MAX_DOWNLOAD, Client


class Parser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            (file or sys.stderr).write(redact(message))


def parser() -> Parser:
    root = Parser(prog="cp-cli", description="使用 API Key 管理 Copaper 项目、文件、编译和评论。")
    root.add_argument("--version", action="version", version=f"cp-cli {__version__}")
    root.add_argument("--base-url", help="Copaper 站点地址")
    root.add_argument("--timeout", type=float, help="请求超时秒数（1–600）")
    root.add_argument("--json", action="store_true", help="输出 JSON")
    root.add_argument("--verbose", action="store_true", help="输出经过脱敏的请求信息")
    commands = root.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", aliases=["auth"], help="使用网站创建的 API Key 登录")
    login.add_argument("action", nargs="?", choices=["status"])
    login.add_argument("--key", nargs="?", const="", help="API Key；省略值时隐藏输入，- 表示从标准输入读取")
    for name in ("logout", "whoami", "list"):
        command = commands.add_parser(name)
        if name == "list":
            command.add_argument("-n", "--names", action="store_true")
    config = commands.add_parser("config").add_subparsers(dest="action", required=True)
    for name in ("get-url", "get-timeout"):
        config.add_parser(name)
    for name in ("set-url", "set-timeout"):
        config.add_parser(name).add_argument("value")
    projects = commands.add_parser("project").add_subparsers(dest="action", required=True)
    create = projects.add_parser("create")
    create.add_argument("name")
    create.add_argument("--template", choices=["blank", "example"], default="blank")
    rename = projects.add_parser("rename")
    rename.add_argument("name")
    rename.add_argument("project", nargs="?")
    for name in ("info", "zip", "compile", "pdf"):
        command = commands.add_parser(name)
        command.add_argument("project", nargs="?")
        if name in {"zip", "pdf"}:
            command.add_argument("-o", "--output")
            command.add_argument("--force", action="store_true")
        if name in {"compile", "pdf"}:
            command.add_argument("-r", "--root")
            command.add_argument("--compiler", choices=["pdflatex", "xelatex", "lualatex", "latex"])
    pull = commands.add_parser("pull")
    pull.add_argument("project", nargs="?")
    pull.add_argument("directory", nargs="?")
    pull.add_argument("--force", action="store_true")
    pull.add_argument("--dry-run", action="store_true")
    for name in ("push", "sync"):
        command = commands.add_parser(name)
        command.add_argument("directory", nargs="?", default=".")
        command.add_argument("--dry-run", action="store_true")
        if name == "push":
            command.add_argument("--force", action="store_true")
            command.add_argument("--delete", action="store_true")
        else:
            command.add_argument("--no-delete", action="store_true")
    diff = commands.add_parser("diff")
    diff.add_argument("project", nargs="?")
    diff.add_argument("directory", nargs="?", default=".")
    diff.add_argument("--name-only", action="store_true")
    diff.add_argument("--exit-code", action="store_true")
    for name in ("upload", "download", "delete"):
        command = commands.add_parser(name, aliases=["rm"] if name == "delete" else [])
        command.add_argument("file")
        command.add_argument("project", nargs="?")
        if name == "upload":
            command.add_argument("--to")
        elif name == "download":
            command.add_argument("-o", "--output")
            command.add_argument("--force", action="store_true")
        else:
            command.add_argument("--dry-run", action="store_true")
    rename = commands.add_parser("rename", aliases=["mv"])
    rename.add_argument("old")
    rename.add_argument("new")
    rename.add_argument("project", nargs="?")
    rename.add_argument("--dry-run", action="store_true")
    output = commands.add_parser("output")
    output.add_argument("type", nargs="?", default="log")
    output.add_argument("--project")
    output.add_argument("--list", action="store_true")
    output.add_argument("-o", "--output")
    output.add_argument("--force", action="store_true")
    commands.add_parser("ignored").add_argument("directory", nargs="?", default=".")
    register_comments(commands)
    return root


def resolve_project(client: Client, value: str | None, workspace: Workspace) -> str:
    if value is None:
        state = workspace.state()
        if not state:
            raise CliError("请指定项目名称或 ID，或先在项目目录中运行 pull")
        if state["url"] != client.url:
            raise CliError("该项目绑定的站点与当前登录站点不同")
        return state["project"]
    if "://" in value:
        parsed, base = urlsplit(value), urlsplit(client.url)
        prefix = base.path.rstrip("/") + "/project/"
        if (
            (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc)
            or not parsed.path.startswith(prefix)
            or parsed.query
            or parsed.fragment
        ):
            raise CliError("项目链接与当前站点不匹配")
        identifier = parsed.path[len(prefix) :]
        client.project_path(identifier)
        return identifier
    try:
        projects = client.projects()
    except CliError as error:
        if error.status == 403 and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
            return value
        raise
    exact = [
        project
        for project in projects
        if project["id"] == value or project["name"].casefold() == value.casefold()
    ]
    choices = exact or [
        project for project in projects if project["name"].casefold().startswith(value.casefold())
    ]
    if len(choices) == 1:
        return choices[0]["id"]
    if len(choices) > 1:
        raise CliError(
            "项目名称不唯一，请使用 ID：\n" + "\n".join(f"{p['name']} ({p['id']})" for p in choices)
        )
    # Opaque IDs are also accepted for callers whose key cannot list projects.
    if re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
        return value
    raise CliError("没有找到匹配项目，请运行 cp-cli list")


def output_file(path: Path, data: bytes, force: bool, store: Store) -> None:
    workspace = Workspace(path.expanduser().absolute().parent, protected=store.directory)
    if workspace.path(path.name).exists() and not force:
        raise CliError("输出文件已存在；请选择其他文件名或显式使用 --force")
    workspace.write(path.name, data, workspace.current_hash(path.name))


def raw_source(path: Path, store: Store) -> bytes:
    full = path.expanduser().absolute()
    if any(private_path(part.name) for part in (full, *full.parents)):
        raise CliError("不允许上传凭据或私密配置")
    return Workspace(full.parent, protected=store.directory).read(full.name)


def compile_project(client: Client, project: str, args) -> dict:
    body = {}
    if args.root:
        body["root"] = clean_path(args.root)
    if args.compiler:
        body["compiler"] = args.compiler
    return client.request("POST", client.project_path(project) + "/compile", body)


def download_output(client: Client, project: str, build: dict, name: str) -> bytes:
    clean_path(name)
    if private_path(name) or not any(file["name"] == name for file in build["files"]):
        raise CliError("没有找到该编译输出，请使用 output --list")
    return client.request(
        "GET",
        client.project_path(project)
        + "/outputs/"
        + quote(str(build["id"]), safe="")
        + "/"
        + quote(name, safe=""),
        binary=True,
    )


def execute(args, store: Store, stdout, stderr, stdin) -> int:
    def emit(value):
        stdout.write(
            redact(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n"
                if args.json or isinstance(value, (dict, list))
                else str(value) + "\n"
            )
        )

    if args.command == "logout":
        store.logout()
        emit("已清除本机保存的登录凭据。若使用环境变量，请同时移除 COPAPER_API_KEY。")
        return 0
    if args.command == "config":
        if args.action == "set-url":
            store.set("url", normalize_url(args.value))
        elif args.action == "set-timeout":
            timeout = float(args.value)
            if not 1 <= timeout <= 600:
                raise CliError("超时秒数必须在 1–600 之间")
            store.set("timeout", timeout)
        emit(store.load().get("url" if args.action.endswith("url") else "timeout", 60))
        return 0
    if args.command == "ignored":
        workspace = Workspace(Path(args.directory), protected=store.directory)
        emit({"patterns": workspace.patterns, "protected": "凭据、私密配置、Git 元数据和项目绑定始终不上传"})
        return 0
    url = store.url(args.base_url)
    timeout = args.timeout if args.timeout is not None else store.load().get("timeout", 60)
    if not 1 <= timeout <= 600:
        raise CliError("超时秒数必须在 1–600 之间")
    verbose = (lambda message: stderr.write(redact(message) + "\n")) if args.verbose else None
    if args.command in {"login", "auth"} and args.action != "status":
        key = args.key
        if key == "-":
            key = stdin.readline().strip()
        elif not key:
            key = getpass.getpass("API Key: ")
        validate_key(key)
        client = Client(url, key, timeout, verbose)
        me = client.identity()
        store.login(url, key)
        emit({"url": url, "user": me["user"], "scopes": me["key"]["scopes"]})
        return 0
    client = Client(url, store.token(url), timeout, verbose)
    if args.command in {"whoami", "login", "auth"}:
        emit(client.identity())
        return 0
    if args.command == "list":
        projects = client.projects()
        if args.names and not args.json:
            emit("\n".join(project["name"] for project in projects))
        elif args.json:
            emit(projects)
        else:
            emit(
                "ID\t名称\t权限\n"
                + "\n".join(f"{p['id']}\t{p['name']}\t{p.get('access', '')}" for p in projects)
            )
        return 0
    if args.command == "project" and args.action == "create":
        emit(client.request("POST", "/projects", {"name": args.name, "template": args.template}))
        return 0
    workspace = Workspace(Path(getattr(args, "directory", None) or "."), protected=store.directory)
    project = resolve_project(client, getattr(args, "project", None), workspace)
    endpoint = client.project_path(project)
    if args.command in {"comments", "comment"}:
        emit(execute_comments(args, client, endpoint))
        return 0
    if args.command == "project":
        emit(client.request("PATCH", endpoint, {"name": args.name}))
        return 0
    if args.command == "info":
        emit(client.manifest(project))
        return 0
    if args.command in {"pull", "push", "sync"}:
        if args.command == "pull" and args.directory is None and getattr(args, "project", None):
            manifest = client.manifest(project)
            workspace = Workspace(Path(safe_name(manifest["name"])), protected=store.directory)
        if not workspace.root.exists():
            if args.dry_run:
                emit({"directory": str(workspace.root), "plan": "新建目录并下载项目文件"})
                return 0
            workspace.root.mkdir(parents=True)
        if (
            args.command == "pull"
            and not workspace.state()
            and any(workspace.root.iterdir())
            and not args.force
        ):
            raise CliError("目标目录非空且未绑定项目，请选择空目录或显式使用 --force")
        if args.command != "pull" and not workspace.state():
            raise CliError("该目录尚未绑定项目，请先运行 pull")
        changes = synchronize(
            client,
            workspace,
            project,
            args.command,
            dry_run=args.dry_run,
            force=getattr(args, "force", False),
            delete=getattr(args, "delete", False),
            no_delete=getattr(args, "no_delete", False),
        )
        emit(changes)
        return 0
    if args.command == "diff":
        manifest = client.manifest(project)
        remote, local = remote_files(workspace, manifest), workspace.scan()
        differences = []
        for name in sorted(set(local) | set(remote)):
            if local.get(name) == remote.get(name):
                continue
            item = {"path": name, "local": local.get(name), "remote": remote.get(name)}
            if not args.name_only and not args.json:
                before = client.download(project, name, remote[name]) if name in remote else b""
                after = workspace.read(name) if name in local else b""
                try:
                    if b"\0" in before or b"\0" in after:
                        raise UnicodeError
                    item["diff"] = "".join(
                        difflib.unified_diff(
                            before.decode("utf-8").splitlines(True),
                            after.decode("utf-8").splitlines(True),
                            fromfile="remote/" + name,
                            tofile="local/" + name,
                        )
                    )
                except UnicodeError:
                    item["diff"] = "二进制文件内容不同"
            differences.append(item)
        if args.json:
            emit(differences)
        elif args.name_only:
            emit("\n".join(item["path"] for item in differences))
        else:
            emit("\n".join(item.get("diff", item["path"]) for item in differences))
        return 1 if args.exit_code and differences else 0
    if args.command == "upload":
        name = clean_path(args.to or Path(args.file).name)
        workspace.path(name)
        data = raw_source(Path(args.file), store)
        if client.key.encode() in data:
            raise CliError("文件包含当前 API Key，已阻止上传")
        manifest = client.manifest(project)
        digest = file_hash(data)
        client.upload_blob(project, digest, data)
        emit(client.apply(project, manifest["revision"], [{"path": name, "hash": digest}]))
        return 0
    if args.command == "download":
        name = clean_path(args.file)
        workspace.path(name)
        manifest = client.manifest(project)
        files = validate_manifest(manifest)
        if name not in files:
            raise CliError("文件不存在")
        data = client.download(project, name, files[name]["hash"])
        if file_hash(data) != files[name]["hash"]:
            raise CliError("下载文件校验失败")
        destination = Path(args.output or PureName(name))
        output_file(destination, data, args.force, store)
        emit({"output": str(destination)})
        return 0
    if args.command in {"delete", "rm", "rename", "mv"}:
        old = clean_path(args.file if args.command in {"delete", "rm"} else args.old)
        workspace.path(old)
        manifest = client.manifest(project)
        files = validate_manifest(manifest)
        selected = [name for name in files if name == old or name.startswith(old + "/")]
        if not selected:
            raise CliError("文件或文件夹不存在")
        changes = [{"path": name, "hash": None} for name in selected]
        if args.command in {"rename", "mv"}:
            new = clean_path(args.new)
            workspace.path(new)
            for name in selected:
                target = new + name[len(old) :]
                workspace.path(target)
                if target in files and target not in selected:
                    raise CliError("目标文件已存在")
                changes.append({"path": target, "hash": files[name]["hash"]})
        if args.dry_run:
            emit(changes)
        else:
            emit(client.apply(project, manifest["revision"], changes))
        return 0
    if args.command == "zip":
        data = client.request("GET", endpoint + "/zip", binary=True, limit=MAX_DOWNLOAD)
        if not data.startswith(b"PK"):
            raise CliError("服务器未返回有效 ZIP 文件")
        destination = Path(args.output or (safe_name(project) + ".zip"))
        output_file(destination, data, args.force, store)
        emit({"output": str(destination)})
        return 0
    if args.command in {"compile", "pdf"}:
        build = compile_project(client, project, args)
        if args.command == "compile":
            emit(build)
        elif build["status"] == "success":
            data = download_output(client, project, build, "output.pdf")
            if not data.startswith(b"%PDF-"):
                raise CliError("服务器未返回有效 PDF 文件")
            destination = Path(args.output or (safe_name(project) + ".pdf"))
            output_file(destination, data, args.force, store)
            emit({"output": str(destination), "build": build["id"]})
        if build["status"] != "success":
            raise CliError("编译未成功。请运行 cp-cli output log 查看日志。", 2)
        return 0
    build = client.request("GET", endpoint + "/outputs/latest")
    if args.list:
        emit(build)
        return 0
    name = args.type if args.type.startswith("output.") else "output." + args.type
    data = download_output(client, project, build, name)
    destination = Path(args.output or safe_name(name))
    output_file(destination, data, args.force, store)
    emit({"output": str(destination)})
    return 0


def PureName(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def main(
    argv: list[str] | None = None, *, stdout=None, stderr=None, stdin=None, store: Store | None = None
) -> int:
    stdout, stderr, stdin = stdout or sys.stdout, stderr or sys.stderr, stdin or sys.stdin
    argv = list(sys.argv[1:] if argv is None else argv)
    # Accept global flags after a command as well as before it.
    flags, remainder = [], []
    index = 0
    while index < len(argv):
        word = argv[index]
        if word == "--":
            remainder.extend(argv[index:])
            break
        if word in {"--base-url", "--timeout"} and index + 1 < len(argv):
            flags.extend(argv[index : index + 2])
            index += 2
        elif word in {"--json", "--verbose"} or word.startswith(("--base-url=", "--timeout=")):
            flags.append(word)
            index += 1
        else:
            remainder.append(word)
            index += 1
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            arguments = parser().parse_args(flags + remainder)
        return execute(arguments, store or Store(), stdout, stderr, stdin)
    except SystemExit as error:
        return int(error.code)
    except CliError as error:
        stderr.write(redact(error) + "\n")
        return error.exit_code
    except (OSError, ValueError, KeyError, TypeError) as error:
        stderr.write("操作未完成：" + redact(error) + "\n")
        return 1
    except KeyboardInterrupt:
        stderr.write("操作已取消\n")
        return 130
