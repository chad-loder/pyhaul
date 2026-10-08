"""Properdocs hook: every HTTP-client tab group covers the clients it applies to.

A tab group is a run of ``=== "label"`` blocks at one indent. When its labels
name HTTP clients (``"requests / niquests"`` names two), the group must have a
tab for each client in its scope, and each tab's code must mention the client
its label names. The scope is the ``to_thread`` sync-only clients when every
tab runs a sync client via ``asyncio.to_thread``, the async-capable clients
when every tab is async, and all supported clients otherwise.

Findings are warnings, which fail ``properdocs build`` under ``strict: true``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from properdocs.structure.pages import Page

log = logging.getLogger("properdocs.plugins.client_tabs")

ALL_CLIENTS = frozenset({"httpx", "niquests", "requests", "urllib3", "aiohttp", "wreq"})
ASYNC_CLIENTS = frozenset({"httpx", "niquests", "aiohttp", "wreq"})
SYNC_ONLY_CLIENTS = frozenset({"requests", "urllib3"})

_HEADER = re.compile(r'^(?P<indent>\s*)=== "(?P<label>[^"]+)"\s*$')
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class TabGroup:
    line: int
    indent: int
    tabs: list[tuple[str, list[str]]] = field(default_factory=list)

    @staticmethod
    def clients(label: str) -> set[str]:
        return {part.strip() for part in label.split("/")}

    @property
    def labeled(self) -> set[str]:
        return {c for label, _ in self.tabs for c in self.clients(label)}

    def scope(self) -> frozenset[str]:
        bodies = ["\n".join(body) for _, body in self.tabs]
        if all("to_thread" in b for b in bodies):
            return SYNC_ONLY_CLIENTS
        if all("async " in b or "await " in b for b in bodies):
            return ASYNC_CLIENTS
        return ALL_CLIENTS


def tab_groups(markdown: str) -> list[TabGroup]:
    groups: list[TabGroup] = []
    current: TabGroup | None = None
    fence: str | None = None
    for n, line in enumerate(markdown.splitlines(), 1):
        m = _HEADER.match(line) if fence is None else None
        if m:
            indent = len(m["indent"])
            if current is None or current.indent != indent:
                current = TabGroup(n, indent)
                groups.append(current)
            current.tabs.append((m["label"], []))
            continue
        if current is not None:
            if fence is None and line.strip() and len(line) - len(line.lstrip()) <= current.indent:
                current = None
            else:
                current.tabs[-1][1].append(line)
        if f := _FENCE.match(line):
            fence = None if fence == f[1] else (fence or f[1])
    return groups


def on_page_markdown(markdown: str, page: Page, **_: Any) -> None:
    src = page.file.src_uri
    for group in tab_groups(markdown):
        if not group.labeled <= ALL_CLIENTS:
            continue
        missing = group.scope() - group.labeled
        if missing:
            log.warning("%s:%d: client tab group has no tab for %s", src, group.line, ", ".join(sorted(missing)))
        for label, body in group.tabs:
            code = "\n".join(body)
            if "```python" not in code:
                continue
            for client in group.clients(label):
                if not re.search(rf"\b{re.escape(client)}\b", code):
                    log.warning("%s:%d: tab %r never mentions %s", src, group.line, label, client)
