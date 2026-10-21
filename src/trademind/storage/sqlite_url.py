"""Single source of truth for sqlite storage URL → file path semantics.

The path is the URL minus the literal ``sqlite:///`` prefix — exactly what
``Database`` opens: ``sqlite:///relative/path`` stays cwd-relative,
``sqlite:////absolute/path`` (four slashes) is absolute, and the Windows
drive form ``sqlite:///C:/...`` keeps its drive. The writer lease derives
its lock file through this same helper, so both always name the same file
for the same URL — including when relative and absolute spellings of one
database are mixed in one working directory (ISSUE-20261013-06).
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

_PREFIX = "sqlite:///"


def sqlite_url_to_path(url: str) -> Path:
    if not url.startswith(_PREFIX):
        parsed = urlparse(url)
        if parsed.scheme != 'sqlite':
            raise ValueError(f"V1 storage is local SQLite only: sqlite:///path "
                             f"(got scheme {parsed.scheme!r})")
        raise ValueError("local sqlite database path required, not a host")
    return Path(url[len(_PREFIX):])
