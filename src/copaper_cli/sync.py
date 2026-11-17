"""File synchronization using a common baseline and server preconditions."""

import tempfile
from pathlib import Path

from .errors import CliError
from .files import Workspace, file_hash, validate_manifest


def plan(
    mode: str, base: dict, local: dict, remote: dict, *, force=False, delete=False, no_delete=False
) -> dict:
    result = {"upload": [], "download": [], "remove_local": [], "remove_remote": [], "conflicts": []}
    for name in sorted(set(base) | set(local) | set(remote)):
        b, here, there = base.get(name), local.get(name), remote.get(name)
        if here == there:
            continue
        if mode == "pull":
            if not force and there == b:
                continue
            if here != b and not force:
                result["conflicts"].append(name)
            elif there is not None:
                result["download"].append(name)
            elif b is not None and here is not None:
                result["remove_local"].append(name)
        elif mode == "push":
            if here == b:
                continue
            if here is None and not delete:
                continue
            if there != b and not force:
                result["conflicts"].append(name)
            else:
                result["remove_remote" if here is None else "upload"].append(name)
        else:
            if here is None and no_delete and b is not None:
                if there != b and there is not None:
                    result["download"].append(name)
                continue
            if here == b:
                result["remove_local" if there is None else "download"].append(name)
            elif there == b:
                result["remove_remote" if here is None else "upload"].append(name)
            else:
                result["conflicts"].append(name)
    return result


def remote_files(workspace: Workspace, manifest: dict) -> dict[str, str]:
    return {
        name: item["hash"] for name, item in validate_manifest(manifest).items() if workspace.included(name)
    }


def synchronize(
    client,
    workspace: Workspace,
    project: str,
    mode: str,
    *,
    dry_run=False,
    force=False,
    delete=False,
    no_delete=False,
) -> dict:
    state = workspace.state()
    if state and (state["url"] != client.url or state["project"] != project):
        raise CliError("该目录已绑定到另一个站点或项目，请使用独立目录")
    manifest = client.manifest(project)
    remote = remote_files(workspace, manifest)
    local = workspace.scan()
    base = state["files"] if state else {}
    changes = plan(mode, base, local, remote, force=force, delete=delete, no_delete=no_delete)
    if dry_run:
        return changes
    if changes["conflicts"]:
        raise CliError("存在双方修改冲突，尚未覆盖任何文件：\n" + "\n".join(changes["conflicts"]), 3)
    with tempfile.TemporaryDirectory(prefix="copaper-download-") as directory:
        staged = {}
        for index, name in enumerate(changes["download"]):
            data = client.download(project, name, remote[name])
            if file_hash(data) != remote[name]:
                raise CliError("下载文件校验失败，请重新同步", 3)
            temporary = Path(directory) / str(index)
            temporary.write_bytes(data)
            staged[name] = temporary
        for name in changes["upload"]:
            data = workspace.read(name)
            if file_hash(data) != local[name]:
                raise CliError(f"本地文件在上传前发生变化：{name}", 3)
            if client.key.encode() in data:
                raise CliError(f"文件包含当前 API Key，已阻止上传：{name}")
            client.upload_blob(project, local[name], data)
        if workspace.scan() != local:
            raise CliError("本地项目在同步期间发生变化，请重新 diff", 3)
        updates = [{"path": name, "hash": local[name]} for name in changes["upload"]] + [
            {"path": name, "hash": None} for name in changes["remove_remote"]
        ]
        if updates:
            client.apply(project, manifest["revision"], updates)
        elif client.manifest(project)["revision"] != manifest["revision"]:
            raise CliError("远端项目在下载期间发生变化，请重新同步", 3)
        for name in changes["remove_local"]:
            workspace.remove(name, local.get(name))
        for name, filename in staged.items():
            workspace.write(name, filename.read_bytes(), local.get(name))
    if mode == "push":
        next_base = dict(base)
        for name in changes["upload"]:
            next_base[name] = local[name]
        for name in changes["remove_remote"]:
            next_base.pop(name, None)
        for name in set(local) & set(remote):
            if local[name] == remote[name]:
                next_base[name] = remote[name]
    else:
        next_base = dict(remote)
        for name in changes["upload"]:
            next_base[name] = local[name]
        for name in changes["remove_remote"]:
            next_base.pop(name, None)
    workspace.save(client.url, project, next_base)
    return changes
