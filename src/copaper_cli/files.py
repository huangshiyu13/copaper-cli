"""Safe project files, ignore rules, hashes and local synchronization state."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath

import pathspec

from .config import atomic_json, normalize_url
from .errors import CliError
from .transport import MAX_FILE

MAX_FILES = 1500
MAX_PROJECT = 128 * 1024 * 1024
DEFAULT_IGNORES = [
    "*.aux",
    "*.log",
    "*.fls",
    "*.fdb_latexmk",
    "*.synctex.gz",
    "*.toc",
    "*.out",
    "*.bcf",
    "*.run.xml",
    "/output.pdf",
    "__pycache__/",
    "node_modules/",
    ".venv/",
    ".DS_Store",
    "._*",
]
PRIVATE_PARTS = {".git", ".copaper", ".ssh", ".aws", ".config", ".cp-cli"}
PRIVATE_NAMES = {
    "credentials.json",
    "secrets.json",
    ".cpignore.local",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
}


def clean_path(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 1000
        or "\\" in name
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise CliError("文件路径不合法")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts) or any(":" in part for part in parts):
        raise CliError("文件路径不能是绝对路径或包含上级目录")
    return name


def private_path(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return any(
        part.lower() in PRIVATE_PARTS
        or part.lower() in PRIVATE_NAMES
        or part.lower() == ".env"
        or part.lower().startswith(".env.")
        or part.lower().endswith((".pem", ".key", ".p12", ".pfx"))
        for part in parts
    )


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_manifest(value: dict) -> dict:
    if (
        not isinstance(value.get("revision"), str)
        or not re.fullmatch("[a-f0-9]{64}", value["revision"])
        or not isinstance(value.get("files"), list)
    ):
        raise CliError("项目清单不合法")
    if len(value["files"]) > MAX_FILES:
        raise CliError("项目超过文件数量限制")
    found, total = {}, 0
    for item in value["files"]:
        if not isinstance(item, dict):
            raise CliError("文件清单不合法")
        name = clean_path(item.get("path"))
        digest, size = item.get("hash"), item.get("size")
        if (
            name in found
            or not isinstance(digest, str)
            or not re.fullmatch("[a-f0-9]{64}", digest)
            or not isinstance(size, int)
            or size < 0
            or size > MAX_FILE
        ):
            raise CliError("文件清单包含重复路径、错误摘要或过大文件")
        found[name] = {"hash": digest, "size": size}
        total += size
    if total > MAX_PROJECT:
        raise CliError("项目超过 128 MiB 限制")
    for name in found:
        parent = PurePosixPath(name).parent
        while str(parent) != ".":
            if str(parent) in found:
                raise CliError("远端清单包含文件与文件夹重名")
            parent = parent.parent
    return found


class Workspace:
    def __init__(self, root: Path, protected: Path | None = None):
        self.root = root.expanduser().absolute()
        for part in (self.root, *self.root.parents):
            if part.is_symlink():
                raise CliError("项目目录不能包含符号链接")
        self.protected = protected.absolute() if protected else None
        if self.protected and (self.root == self.protected or self.protected in self.root.parents):
            raise CliError("不能在凭据目录中同步项目")
        self.state_path = self.root / ".copaper" / "project.json"
        self.patterns = list(DEFAULT_IGNORES)
        for name in (".cpignore", ".cpignore.local"):
            candidate = self.root / name
            if candidate.is_symlink():
                raise CliError("忽略文件不能是符号链接")
            if candidate.is_file():
                self.patterns.extend(candidate.read_text(encoding="utf-8").splitlines())
        self.ignore = pathspec.GitIgnoreSpec.from_lines(self.patterns)

    def path(self, name: str) -> Path:
        clean_path(name)
        target = self.root.joinpath(*name.split("/"))
        if private_path(name) or (
            self.protected and (target == self.protected or self.protected in target.parents)
        ):
            raise CliError("不允许访问凭据、Git 元数据或私密配置")
        for part in (target, *target.parents):
            if part == self.root.parent:
                break
            if part.is_symlink():
                raise CliError("项目文件不能包含符号链接")
        return target

    def included(self, name: str, directory: bool = False) -> bool:
        clean_path(name)
        target = self.root.joinpath(*name.split("/"))
        return (
            not private_path(name)
            and not (self.protected and (target == self.protected or self.protected in target.parents))
            and not self.ignore.match_file(name + ("/" if directory else ""))
        )

    def scan(self) -> dict[str, str]:
        result, total = {}, 0
        for directory, folders, files in os.walk(self.root, followlinks=False):
            base = Path(directory)
            retained = []
            for name in folders:
                relative = (base / name).relative_to(self.root).as_posix()
                if not self.included(relative, True):
                    continue
                self.path(relative)
                retained.append(name)
            folders[:] = retained
            for name in sorted(files):
                relative = (base / name).relative_to(self.root).as_posix()
                if not self.included(relative):
                    continue
                data = self.read(relative)
                result[relative] = file_hash(data)
                total += len(data)
                if len(result) > MAX_FILES or total > MAX_PROJECT:
                    raise CliError("本地项目超过 1500 个文件或 128 MiB 限制")
        return result

    def read(self, name: str) -> bytes:
        target = self.path(name)
        if not target.is_file():
            raise CliError(f"文件不存在：{name}")
        if target.stat().st_size > MAX_FILE:
            raise CliError(f"文件超过 32 MiB：{name}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(target, flags), "rb") as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise CliError(f"文件超过 32 MiB：{name}")
        return data

    def current_hash(self, name: str) -> str | None:
        target = self.path(name)
        return file_hash(self.read(name)) if target.is_file() else None

    def write(self, name: str, data: bytes, expected: str | None) -> None:
        target = self.path(name)
        if self.current_hash(name) != expected:
            raise CliError(f"本地文件在操作期间发生变化：{name}，请重新 diff", 3)
        if target.is_dir():
            for directory, folders, files in os.walk(target, topdown=False, followlinks=False):
                if files or any((Path(directory) / folder).is_symlink() for folder in folders):
                    raise CliError(f"目标目录仍有本地文件，无法替换：{name}", 3)
            for directory, _folders, _files in os.walk(target, topdown=False):
                Path(directory).rmdir()
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".cp-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.path(name)
            if self.current_hash(name) != expected:
                raise CliError(f"本地文件在操作期间发生变化：{name}，请重新 diff", 3)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def remove(self, name: str, expected: str | None) -> None:
        if self.current_hash(name) != expected:
            raise CliError(f"本地文件在操作期间发生变化：{name}，请重新 diff", 3)
        self.path(name).unlink(missing_ok=True)

    def state(self) -> dict | None:
        directory = self.state_path.parent
        if directory.is_symlink() or self.state_path.is_symlink():
            raise CliError("本地项目配置不能是符号链接")
        if not self.state_path.exists():
            return None
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or value.get("version") != 1
                or not isinstance(value.get("project"), str)
                or not isinstance(value.get("files"), dict)
            ):
                raise ValueError
            value["url"] = normalize_url(value["url"])
            for name, digest in value["files"].items():
                clean_path(name)
                if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
                    raise ValueError
            return value
        except (ValueError, KeyError, UnicodeError) as error:
            raise CliError("本地项目配置损坏，请从新的目录重新 pull") from error

    def save(self, url: str, project: str, files: dict[str, str]) -> None:
        atomic_json(self.state_path, {"version": 1, "url": url, "project": project, "files": files})


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "project"
    if name.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name[:180]
