"""One-time setup token for the first admin account.

The token is written to a mode-600 file. It is not printed, and the setup
form does not read it from the address bar, so it does not land in logs.
"""

from __future__ import annotations

import os
from pathlib import Path


def startup_token_path() -> Path:
    data_dir = Path(os.environ.get("CPTR_DATA_DIR", str(Path.home() / ".cptr")))
    return data_dir / "startup-token"


def write_startup_token(token: str) -> None:
    path = startup_token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.encode())
        finally:
            os.close(fd)
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_startup_token() -> None:
    try:
        startup_token_path().unlink(missing_ok=True)
    except OSError:
        pass
