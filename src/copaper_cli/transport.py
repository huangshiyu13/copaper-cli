"""HTTPS transport for the public Copaper API; redirects never carry a key."""

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from .config import normalize_url, validate_key
from .errors import CliError, redact

MAX_FILE = 32 * 1024 * 1024
MAX_DOWNLOAD = 256 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, url: str, key: str, timeout: float = 60, verbose=None):
        self.url, self.key = normalize_url(url), validate_key(key)
        self.timeout, self.verbose = timeout, verbose
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(
        self,
        method: str,
        endpoint: str,
        body=None,
        *,
        raw: bytes | None = None,
        headers=None,
        binary=False,
        limit=MAX_DOWNLOAD,
    ):
        if not endpoint.startswith("/") or endpoint.startswith("//") or "://" in endpoint:
            raise CliError("API 请求路径不合法")
        request_headers = {
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/octet-stream" if binary else "application/json",
            "User-Agent": "copaper-cli/0.1.0",
        }
        if headers:
            request_headers.update(headers)
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif raw is not None:
            request_headers["Content-Type"] = "application/octet-stream"
        if self.verbose:
            self.verbose(f"{method} /api/v1{endpoint.split('?')[0]}")
        request = urllib.request.Request(
            self.url + "/api/v1" + endpoint, data=data, method=method, headers=request_headers
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                maximum = limit if binary else 4 * 1024 * 1024
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdigit() or int(length) > maximum):
                    raise CliError("服务器返回的数据过大或长度不合法")
                content = response.read(maximum + 1)
                if len(content) > maximum:
                    raise CliError("服务器返回的数据超过下载限制")
                if binary:
                    return content
                try:
                    value = json.loads(content)
                    if not isinstance(value, dict):
                        raise ValueError
                    return value
                except (ValueError, UnicodeError) as error:
                    raise CliError("服务器返回了无效的 API 响应") from error
        except urllib.error.HTTPError as error:
            message = ""
            try:
                payload = json.loads(error.read(65536))
                message = payload.get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                pass
            if 300 <= error.code < 400:
                message = "服务器重定向已被阻止，请确认站点地址；Key 未被转发"
            elif error.code == 401:
                message = "API Key 无效、已过期或已撤销，请重新登录"
            elif error.code in {409, 412}:
                message = message or "项目已被修改，请重新 diff 或同步"
            else:
                message = str(message or f"API 请求失败（HTTP {error.code}）")[:500]
            raise CliError(
                redact(message, (self.key,)), 3 if error.code in {409, 412} else 1, error.code
            ) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
            raise CliError("无法连接 Copaper，或请求超时；请检查网络、站点地址和 --timeout 设置") from error

    def identity(self) -> dict:
        value = self.request("GET", "/me")
        user, key = value.get("user"), value.get("key")
        if (
            value.get("apiVersion") != 1
            or not isinstance(user, dict)
            or not isinstance(key, dict)
            or not isinstance(user.get("id"), str)
            or not isinstance(user.get("email"), str)
            or not isinstance(key.get("scopes"), list)
        ):
            raise CliError("该站点未返回兼容的 Copaper 账号信息；登录凭据未保存")
        return value

    def projects(self) -> list[dict]:
        result, seen, cursor = [], set(), None
        while True:
            query = "?" + urllib.parse.urlencode({"cursor": cursor}) if cursor else ""
            page = self.request("GET", "/projects" + query)
            if not isinstance(page.get("projects"), list):
                raise CliError("项目列表响应不合法")
            result.extend(page["projects"])
            cursor = page.get("nextCursor")
            if not cursor:
                return result
            if not isinstance(cursor, str) or cursor in seen or len(seen) >= 1000:
                raise CliError("项目分页响应不合法")
            seen.add(cursor)

    def project_path(self, project_id: str) -> str:
        if (
            not isinstance(project_id, str)
            or not project_id
            or project_id.startswith("sk-")
            or any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for char in project_id
            )
        ):
            raise CliError("项目 ID 不合法")
        return "/projects/" + urllib.parse.quote(project_id, safe="")

    def manifest(self, project_id: str) -> dict:
        return self.request("GET", self.project_path(project_id))

    def download(self, project_id: str, name: str, expected_hash: str) -> bytes:
        return self.request(
            "GET",
            self.project_path(project_id) + "/files?" + urllib.parse.urlencode({"path": name}),
            headers={"If-Match": f'"{expected_hash}"'},
            binary=True,
            limit=MAX_FILE,
        )

    def upload_blob(self, project_id: str, digest: str, data: bytes) -> None:
        self.request("PUT", self.project_path(project_id) + "/blobs/" + digest, raw=data)

    def apply(self, project_id: str, revision: str, changes: list[dict]) -> dict:
        return self.request(
            "POST", self.project_path(project_id) + "/apply", {"revision": revision, "changes": changes}
        )
