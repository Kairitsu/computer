"""Workspace filesystem helpers."""

from __future__ import annotations


def auto_gitignore_cptr_enabled() -> bool:
    """Keep a repo's .cptr directory out of git.

    Writing chats, memory, or caches under .cptr appends that name to the
    repo .gitignore. This stays on; settings no longer offer a switch.
    """
    return True
