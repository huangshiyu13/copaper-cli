"""User-facing errors and credential-safe output."""

import re


class CliError(Exception):
    """An expected failure with a stable CLI exit status."""

    def __init__(self, message: str, exit_code: int = 1, status: int | None = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.status = status


def redact(value: object, secrets: tuple[str, ...] = ()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"sk-[A-Za-z0-9_-]{10,}", "[REDACTED]", text)
    return re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)
