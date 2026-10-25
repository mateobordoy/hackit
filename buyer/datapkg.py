"""The data package (plus the Sunday release) seen through one case: the case's own `facts` win,
then the package's citable facts, then its tables. Every read is safe: a missing package, table
or row returns nothing instead of raising, so a hidden case with new IDs never breaks a move."""

import logging
import threading

from arena_kit.data import DataPackage

from . import config

log = logging.getLogger("buyer")
_PKG = None
_LOCK = threading.Lock()


def load_package(root=None):
    """The shared package (one per process), or None when it is not there."""
    global _PKG
    with _LOCK:
        if _PKG is None:
            path = root or config.data_dir()
            try:
                _PKG = DataPackage(path)
                _PKG.lookup("")  # build the ID index once, before threads share it
                log.info("data package %s (release: %s)", path, _PKG.release)
            except (FileNotFoundError, OSError) as e:
                log.warning("no data package at %s: %s", path, e)
                _PKG = False
        return _PKG or None


def reset_package():
    global _PKG
    with _LOCK:
        _PKG = None


class CaseData:
    def __init__(self, case, pkg=None):
        self.case, self.pkg = case, pkg
        self.facts = case.get("facts") or {}
        self.refs = list(case.get("refs") or [])

    def fact(self, ref):
        if not ref:
            return None
        if ref in self.facts:
            return self.facts[ref]
        if self.pkg is None:
            return None
        try:
            return self.pkg.lookup(ref)
        except Exception:  # noqa: BLE001
            return None

    def ids(self, prefix):
        out = {k for k in self.facts if k.startswith(prefix)}
        out |= {r for r in self.refs if r.startswith(prefix)}
        if self.pkg is not None:
            try:
                out |= set(self.pkg.ids(prefix))
            except Exception:  # noqa: BLE001
                pass
        return sorted(out)

    def query(self, sql, params=()):
        if self.pkg is None:
            return []
        try:
            return self.pkg.query(sql, params)
        except Exception:  # noqa: BLE001 - no SQLite, unknown table: nothing
            return []

    def document(self, ref):
        if self.pkg is None:
            return ""
        try:
            return self.pkg.document(ref) or ""
        except Exception:  # noqa: BLE001
            return ""

    def known_supplier(self, sid):
        return self.fact(sid) is not None or bool(self.query("SELECT id FROM suppliers WHERE id = ?", (sid,)))
