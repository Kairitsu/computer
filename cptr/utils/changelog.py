"""Parse CHANGELOG.md into a structured dict for the /api/changelog endpoint.

Parses the Keep a Changelog format without requiring any external dependencies
(no markdown or beautifulsoup). The result is a dict keyed by version number,
each containing a 'date' and section lists (added, changed, fixed, etc.).
"""

from __future__ import annotations

import re
from pathlib import Path


def parse_changelog(path: Path | None = None) -> dict:
    """Parse CHANGELOG.md and return structured JSON.

    Returns:
        {
            "0.1.0": {
                "date": "2026-06-06",
                "added": [
                    {"title": "Initial release.", "content": "First public version ...", "raw": "🚀 **Initial release.** First public ..."}
                ],
                ...
            }
        }
    """
    if path is None:
        # Look relative to project root (two levels up from this file) or inside the package
        path = Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"
        if not path.exists():
            path = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return parse_changelog_text(content)


def parse_changelog_text(content: str) -> dict:
    """Parse CHANGELOG.md text; see parse_changelog for the shape."""
    changelog: dict = {}
    current_version: str | None = None
    current_section: str | None = None
    current_date: str | None = None

    for line in content.splitlines():
        # Match version header: ## [0.1.0] - 2026-06-06
        version_match = re.match(r"^## \[(.+?)\]\s*-\s*(.+)$", line.strip())
        if version_match:
            current_version = version_match.group(1)
            current_date = version_match.group(2).strip()
            changelog[current_version] = {"date": current_date}
            current_section = None
            continue

        # Match section header: ### Added, ### Changed, ### Fixed, etc.
        section_match = re.match(r"^### (.+)$", line.strip())
        if section_match and current_version:
            current_section = section_match.group(1).strip().lower()
            changelog[current_version][current_section] = []
            continue

        # Match list item: - 🚀 **Title.** Description here.
        item_match = re.match(r"^- (.+)$", line.strip())
        if item_match and current_version and current_section:
            raw = item_match.group(1).strip()

            # Try to extract title from **Title.** pattern
            title_match = re.match(r".*?\*\*(.+?)\*\*\s*(.*)", raw)
            if title_match:
                title = title_match.group(1).strip().rstrip(".")
                content_text = title_match.group(2).strip()
            else:
                title = ""
                content_text = raw

            changelog[current_version][current_section].append(
                {"title": title, "content": content_text, "raw": raw}
            )

    return changelog


# Parse once at import time (like Open WebUI does)
CHANGELOG = parse_changelog()
