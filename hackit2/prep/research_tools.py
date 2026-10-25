"""Bounded, read-only discovery over the arena data package, with source provenance.

SQL runs on a separate query-only connection, never the package's shared connection. Deadlines,
an SQLite authorizer and a progress handler bound recursive/expensive queries. Documents must be
citable IDs from the package; arbitrary filesystem paths are never accepted.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

from .. import settings, telemetry


class ResearchTools:
    def __init__(self, pkg, deadline, supplier_id):
        self.pkg, self.deadline, self.supplier_id = pkg, deadline, supplier_id
        self.sources, self.calls, self._cache = {}, [], {}
        self._lock = threading.Lock()
        self.db_path = None
        root = getattr(pkg, "root", None)
        if root is not None:
            # Never run PRAGMA on the shared connection while other cases query it.
            # Match DataPackage's deterministic database selection using its public root.
            databases = sorted(Path(root).rglob("*.sqlite")) + sorted(Path(root).rglob("*.db"))
            self.db_path = str(databases[0]) if databases else None
        elif pkg is not None and getattr(pkg, "db", None) is not None:
            row = pkg.db.execute("PRAGMA database_list").fetchone()
            self.db_path = row[2] if row else None

    def source(self, label, value, **provenance):
        """Register exact retrieved data; a source ID proves retrieval, not an analyst's inference."""
        with self._lock:
            ref = f"source:{len(self.sources) + 1}"
            self.sources[ref] = {"source_id": ref, "label": label, **provenance, "data": value}
        return ref

    def query(self, sql, params=(), limit=12):
        if (
            not isinstance(sql, str)
            or not sql.strip()
            or sql.lstrip().split(None, 1)[0].lower() not in {"select", "with"}
        ):
            raise ValueError("SELECT or WITH only")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("research deadline reached")
        limit = min(20, max(1, int(limit)))
        if self.pkg is None:
            return []
        if not self.db_path:
            return self.pkg.query(sql, tuple(params), limit)
        until = min(self.deadline, time.monotonic() + settings.ALPHA_SQL_TIMEOUT_S)
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

            def authorize(action, arg1, arg2, database, trigger):
                if action not in allowed or (arg2 or "").lower() in {"load_extension", "readfile", "writefile"}:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            connection.set_progress_handler(lambda: int(time.monotonic() >= until), 500)
            return [dict(row) for row in connection.execute(sql, tuple(params)).fetchmany(limit)]
        finally:
            connection.close()

    def schema(self):
        return self.pkg.tables() if self.pkg is not None and hasattr(self.pkg, "tables") else {}

    def document(self, ref, offset=0):
        if not isinstance(ref, str) or ":" not in ref or self.pkg is None or not self.pkg.lookup(ref):
            raise ValueError("document requires a known citable package ID")
        text = self.pkg.document(ref)
        offset = min(max(0, int(offset)), len(text))
        return {
            "ref": ref,
            "offset": offset,
            "text": text[offset : offset + 7000],
            "total_chars": len(text),
            "truncated": len(text) > offset + 7000,
        }

    def search(self, query):
        """Literal terms over metadata and document bodies, including release-only records."""
        words = [w.lower() for w in str(query).split() if len(w) > 2][:8]
        if not words or self.pkg is None:
            return []
        hits, bodies = [], 0
        refs = list(self.pkg.ids(""))[:12000]
        # Supplier-local records first; the researcher may also discover rivals and related parts.
        rows = [(ref, self.pkg.lookup(ref) or {}) for ref in refs]
        rows.sort(key=lambda item: str(item[1].get("supplier") or item[1].get("supplier_id")) != self.supplier_id)
        for ref, row in rows:
            if time.monotonic() >= self.deadline:
                break
            text = json.dumps(row, ensure_ascii=False)
            if ref.startswith(("doc:", "news:")) and bodies < 100:
                try:
                    text += "\n" + self.pkg.document(ref)[:16000]
                    bodies += 1
                except (OSError, ValueError, TypeError):
                    pass
            lowered = text.lower()
            score = sum(word in lowered for word in words)
            if score:
                position = min(lowered.find(word) for word in words if word in lowered)
                hits.append({"ref": ref, "matches": score, "excerpt": text[max(0, position - 150) : position + 750]})
        return sorted(hits, key=lambda hit: hit["matches"], reverse=True)[:8]

    def call(self, request):
        """Run one model-selected lookup; errors are observations, never fatal to preparation."""
        if len(self.calls) >= settings.ALPHA_TOOL_CALLS or time.monotonic() >= self.deadline:
            return {"error": "research tool budget exhausted"}
        if not isinstance(request, dict):
            return {"error": "tool request must be an object"}
        key = json.dumps(request, sort_keys=True)
        if key in self._cache:
            return self._cache[key]
        name, args = request.get("tool"), request.get("args") or {}
        started = time.monotonic()
        try:
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
            if name == "sql":
                data = self.query(args.get("query", ""), args.get("params", []), args.get("limit", 12))
            elif name == "search":
                data = self.search(args.get("query", ""))
            elif name == "document":
                data = self.document(args.get("ref"), args.get("offset", 0))
            elif name == "schema":
                data = self.schema()
            else:
                raise ValueError("unknown read-only tool")
            ref = self.source(f"discovery:{name}", data, request=request)
            # Keep the source exact, and explicitly mark a shortened tool result in the prompt.
            encoded = json.dumps(data, ensure_ascii=False)
            result = {"source_id": ref, "data": encoded[:10000], "truncated": len(encoded) > 10000}
        except Exception as exc:  # noqa: BLE001 - malformed SQL or missing data must not lose preparation
            result = {"error": str(exc)[:250]}
        event = {"tool": name, "request": request, "result": result, "seconds": round(time.monotonic() - started, 3)}
        self.calls.append(event)
        self._cache[key] = result
        telemetry.emit("research_tool", supplier=self.supplier_id, **event)
        return result
