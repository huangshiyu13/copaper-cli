"""User-local configuration, with credentials bound to one service URL."""

import json
import os
import stat
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from .errors import CliError

DEFAULT_URL = "https://paper.mm-lab.cn"


def normalize_url(value: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) <= 32 for char in value) or "\\" in value:
        raise CliError("站点地址不合法")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (
            not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        if parsed.scheme not in {"https", "http"}:
            raise ValueError
        import ipaddress

        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if parsed.scheme == "http" and not loopback:
            raise CliError("站点必须使用 HTTPS（本机回环地址除外）")
        if any(part in {".", ".."} for part in unquote(parsed.path).split("/")):
            raise ValueError
        host = host.lower().encode("idna").decode("ascii")
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            authority += f":{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))
    except (ValueError, UnicodeError) as error:
        raise CliError("站点地址不合法") from error


def check_directory(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise CliError("配置目录不能包含符号链接")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        if path.stat().st_uid != os.getuid():
            raise CliError("配置目录不属于当前用户")
        path.chmod(0o700)


def atomic_json(path: Path, value: dict) -> None:
    check_directory(path.parent)
    if path.is_symlink():
        raise CliError("配置文件不能是符号链接")
    descriptor, name = tempfile.mkstemp(prefix=".cp-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Store:
    def __init__(self, directory: Path | None = None, environ: dict | None = None):
        self.environ = os.environ if environ is None else environ
        configured = self.environ.get("COPAPER_CONFIG_DIR")
        base = self.environ.get("XDG_CONFIG_HOME") or self.environ.get("LOCALAPPDATA")
        self.directory = (
            Path(
                directory
                or configured
                or (Path(base) / "copaper" if base else Path.home() / ".config" / "copaper")
            )
            .expanduser()
            .absolute()
        )
        self.path = self.directory / "credentials.json"

    def load(self) -> dict:
        for path in (self.path, self.directory, *self.directory.parents):
            if path.is_symlink():
                raise CliError("配置路径不能包含符号链接")
        if not self.path.exists():
            return {"url": DEFAULT_URL, "timeout": 60}
        details = self.path.stat()
        if os.name != "nt" and (details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077):
            raise CliError("凭据文件必须属于当前用户且权限为 600；请修正权限后重试")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("timeout", 60), (int, float))
                or not 1 <= value.get("timeout", 60) <= 600
            ):
                raise ValueError
            value["url"] = normalize_url(value.get("url", DEFAULT_URL))
            return value
        except (ValueError, TypeError, UnicodeError) as error:
            raise CliError("配置文件损坏，请重新登录") from error

    def url(self, override: str | None = None) -> str:
        return normalize_url(override or self.environ.get("COPAPER_BASE_URL") or self.load()["url"])

    def token(self, url: str) -> str:
        value = self.load()
        environment_key = self.environ.get("COPAPER_API_KEY")
        if environment_key:
            intended = normalize_url(self.environ.get("COPAPER_BASE_URL") or value["url"])
            if intended != url:
                raise CliError("Key 绑定的站点与目标地址不同，请显式重新登录目标站点")
            return validate_key(environment_key)
        credential = value.get("credential", {})
        if not isinstance(credential, dict) or credential.get("url") != url or not credential.get("key"):
            raise CliError("尚未登录该站点，请运行 cp-cli login --key 或使用 --key - 从标准输入读取")
        return validate_key(credential["key"])

    def login(self, url: str, key: str) -> None:
        value = self.load()
        value.update(url=url, credential={"url": url, "key": validate_key(key)})
        atomic_json(self.path, value)

    def set(self, name: str, value: str | float) -> None:
        data = self.load()
        data[name] = value
        atomic_json(self.path, data)

    def logout(self) -> None:
        if self.path.is_symlink():
            raise CliError("凭据文件不能是符号链接")
        if self.path.exists():
            data = self.load()
            data.pop("credential", None)
            atomic_json(self.path, data)


def validate_key(key: str) -> str:
    import re

    if not isinstance(key, str) or not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,200}", key):
        raise CliError("API Key 格式不合法，请从网站 API Keys 页面复制完整 Key")
    return key
