"""Check source and distribution contents without printing matched secrets."""

import argparse
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

PATTERNS = [
    re.compile(rb"sk-[a-f0-9]{64}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
]
PRIVATE = {"credentials.json", "secrets.json", ".env", ".netrc"}


def check(name: str, data: bytes) -> list[str]:
    parts = Path(name).parts
    if any(
        part in PRIVATE or part in {".venv", "__pycache__", ".copaper", ".cp-cli"} for part in parts
    ) or name.endswith((".log", ".pem", ".key", ".pyc")):
        return [f"Unexpected private/generated file: {name}"]
    if any(pattern.search(data) for pattern in PATTERNS):
        return [f"Sensitive content found in: {name} (value withheld)"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    problems = []
    paths = subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard", "-z"]).split(b"\0")
    for value in set(paths):
        if value:
            path = Path(value.decode())
            if path.is_file():
                problems.extend(check(path.as_posix(), path.read_bytes()))
    if args.artifacts:
        archives = list(args.artifacts.glob("*.whl")) + list(args.artifacts.glob("*.tar.gz"))
        if not archives:
            problems.append("No distribution archives found")
        for archive in archives:
            if archive.suffix == ".whl":
                with zipfile.ZipFile(archive) as package:
                    for name in package.namelist():
                        problems.extend(check(name, package.read(name)))
            else:
                with tarfile.open(archive) as package:
                    for member in package:
                        if member.isfile():
                            problems.extend(check(member.name, package.extractfile(member).read()))
    for problem in problems:
        print(problem)
    if not problems:
        print("Public source and distribution checks passed; no credentials or private machine paths found.")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
