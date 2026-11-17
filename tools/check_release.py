"""Validate release versions without reading credentials or service settings."""

import argparse
import ast
import os
import re
from importlib.metadata import version as installed_version
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    module = ast.parse((root / "src/copaper_cli/__init__.py").read_text(encoding="utf-8"))
    values = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
    ]
    if len(values) != 1 or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", values[0]):
        raise SystemExit("Source must declare one stable semantic version")
    source_version = values[0]
    if installed_version("copaper-cli") != source_version:
        raise SystemExit("Installed package differs from source; reinstall the current checkout")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$", changelog, re.MULTILINE)
    if not versions or versions[0] != source_version or len(set(versions)) != len(versions):
        raise SystemExit("Latest changelog version must match source, without duplicate versions")
    if f"**{source_version}**" not in (root / "README.md").read_text(encoding="utf-8"):
        raise SystemExit("README version differs from source")
    tag = args.tag or (os.environ.get("GITHUB_REF_NAME") if os.environ.get("GITHUB_REF_TYPE") == "tag" else None)
    if tag and tag != f"v{source_version}":
        raise SystemExit("Release tag differs from source")
    suffix = " and tag" if tag else ""
    print(f"Copaper CLI {source_version}: source, installed package, README, changelog{suffix} agree.")


if __name__ == "__main__":
    main()
