from __future__ import annotations
import pathlib as pl
import urllib.request
from typing import List
from .audit import Audit
from .policy import Policy

class CapabilityError(Exception):
    pass

class CapabilityManager:
    def __init__(self, root: pl.Path, actor: str):
        self.root = root
        self.actor = actor
        self.audit = Audit(root)
        self.policy = Policy(root)
        self.plugins = {}
    def _log(self, action: str, ok: bool, **details):
        self.audit.write(self.actor, action, ok, details)
    def fs_list(self, path: str) -> List[str]:
        p = pl.Path(path)
        ok = self.policy.check("fs.read", path=p)
        self._log("fs.list", ok, path=str(p))
        if not ok:
            raise CapabilityError("fs.read not permitted")
        return [str(x) for x in p.iterdir()]
    def fs_read(self, path: str) -> str:
        p = pl.Path(path)
        ok = self.policy.check("fs.read", path=p)
        self._log("fs.read", ok, path=str(p))
        if not ok:
            raise CapabilityError("fs.read not permitted")
        return p.read_text(encoding="utf-8")
    def fs_write(self, path: str, content: str) -> str:
        p = pl.Path(path)
        ok = self.policy.check("fs.write", path=p)
        self._log("fs.write", ok, path=str(p), size=len(content))
        if not ok:
            raise CapabilityError("fs.write not permitted")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)
    def net_http_get(self, url: str) -> str:
        ok = self.policy.check("net.http")
        self._log("net.http.get", ok, url=url)
        if not ok:
            raise CapabilityError("net.http disabled")
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.read().decode("utf-8", errors="ignore")
    def register(self, name: str, func):
        self.plugins[name] = func
    def call(self, name: str, **kwargs):
        if name not in self.plugins:
            raise CapabilityError("plugin not found")
        return self.plugins[name](self, **kwargs)
