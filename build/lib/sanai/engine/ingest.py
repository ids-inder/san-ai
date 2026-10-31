"""Stage 1: gather raw material about a concept.

Nothing here interprets the material - it only collects and stores it, so the
comprehension exam later has a source of truth independent of the model's own
summary.
"""
from __future__ import annotations

import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

from .. import db
from ..config import Settings
from ..executor import Executor

MAX_SOURCE_CHARS = 200_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tox"}
TEXT_SUFFIXES = {
    ".md", ".txt", ".rst", ".py", ".sh", ".bash", ".conf", ".cfg", ".ini",
    ".toml", ".yaml", ".yml", ".json", ".service", ".sql", ".js", ".ts",
    ".go", ".rs", ".java", ".rb", ".php", ".c", ".h", ".cpp", ".tf", "",
}


class _Text(HTMLParser):
    """HTML to readable text, stdlib only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in ("script", "style", "nav", "footer", "svg"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "pre"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "footer", "svg") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "pre"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> str:
    parser = _Text()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML must not abort a lesson
        pass
    return parser.text()


class Ingestor:
    def __init__(
        self,
        conn: sqlite3.Connection,
        cfg: Settings,
        executor: Executor,
    ) -> None:
        self.conn = conn
        self.cfg = cfg
        self.executor = executor

    # -- individual collectors --------------------------------------------
    def from_text(self, module_id: int, module: str, note: str, origin: str = "operator-note") -> bool:
        return self._store(module_id, module, "note", origin, note)

    def from_file(self, module_id: int, module: str, path: str | Path) -> bool:
        p = Path(path).expanduser()
        if not p.is_file():
            return self._store(module_id, module, "file", str(p), f"[missing file: {p}]")
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            content = f"[unreadable: {exc}]"
        return self._store(module_id, module, "file", str(p), content)

    def from_dir(self, module_id: int, module: str, path: str | Path, max_files: int = 60) -> int:
        root = Path(path).expanduser()
        if not root.is_dir():
            self._store(module_id, module, "repo", str(root), f"[missing directory: {root}]")
            return 0
        count = 0
        for p in sorted(root.rglob("*")):
            if count >= max_files:
                break
            if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
                continue
            if p.suffix.lower() not in TEXT_SUFFIXES or p.stat().st_size > 400_000:
                continue
            if self.from_file(module_id, module, p):
                count += 1
        return count

    def from_url(self, module_id: int, module: str, url: str) -> bool:
        try:
            import httpx2 as httpx
        except ImportError:
            try:
                import httpx  # type: ignore
            except ImportError:
                return self._store(
                    module_id, module, "url", url, "[no HTTP client installed]"
                )
        try:
            resp = httpx.get(url, timeout=45.0, follow_redirects=True)
            resp.raise_for_status()
            body = resp.text
        except Exception as exc:  # noqa: BLE001 - any fetch failure is just a missing source
            return self._store(module_id, module, "url", url, f"[fetch failed: {exc}]")
        ctype = resp.headers.get("content-type", "")
        content = html_to_text(body) if "html" in ctype or body.lstrip().startswith("<") else body
        return self._store(module_id, module, "url", url, content)

    def from_command(self, module_id: int, module: str, command: str) -> bool:
        """Read local documentation: `man nginx`, `systemctl --help`, ..."""
        res = self.executor.run(
            f"{command} 2>&1 | head -c 120000",
            module_id=module_id,
            rationale="ingest: read local documentation",
            timeout=60,
        )
        body = res.stdout if res.ran else res.summary()
        return self._store(module_id, module, "command", command, body)

    def probe(self, module_id: int, module: str, topic: str) -> int:
        """Discover what the server itself can tell us about a topic."""
        tool = re.sub(r"[^a-zA-Z0-9._-]", "", topic.split()[0]) if topic.split() else ""
        if not tool:
            return 0
        stored = 0
        for cmd in (
            f"command -v {tool} && {tool} --version",
            f"man -P cat {tool}",
            f"{tool} --help",
        ):
            if self.from_command(module_id, module, cmd):
                stored += 1
        return stored

    # -- storage -----------------------------------------------------------
    def _store(self, module_id: int, module: str, kind: str, origin: str, content: str) -> bool:
        content = (content or "").strip()
        if len(content) < 40:  # nothing worth examining on
            return False
        db.add_source(
            self.conn, module_id, module, kind, origin, content[:MAX_SOURCE_CHARS]
        )
        db.audit(
            self.conn,
            "ingest.source",
            {"module": module, "kind": kind, "origin": origin, "chars": len(content)},
        )
        return True


def collect(
    ingestor: Ingestor,
    module_id: int,
    module: str,
    *,
    topic: str,
    urls: list[str] | None = None,
    files: list[str] | None = None,
    dirs: list[str] | None = None,
    notes: list[str] | None = None,
    probe_local: bool = True,
) -> int:
    """Run every requested collector. Returns the number of sources stored."""
    n = 0
    for note in notes or []:
        n += int(ingestor.from_text(module_id, module, note))
    for url in urls or []:
        n += int(ingestor.from_url(module_id, module, url))
    for f in files or []:
        n += int(ingestor.from_file(module_id, module, f))
    for d in dirs or []:
        n += ingestor.from_dir(module_id, module, d)
    if probe_local:
        n += ingestor.probe(module_id, module, topic)
    return n
