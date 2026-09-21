from __future__ import annotations
import pathlib as pl
from typing import List
from .audit import Audit
from .policy import Policy
from ..security import open_public_http_url

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
        p = self.policy.resolve_path("fs.read", path)
        self._log("fs.list", p is not None, path=str(path))
        if p is None:
            raise CapabilityError("fs.read not permitted")
        return [str(x) for x in p.iterdir()]
    def fs_read(self, path: str) -> str:
        p = self.policy.resolve_path("fs.read", path)
        self._log("fs.read", p is not None, path=str(path))
        if p is None:
            raise CapabilityError("fs.read not permitted")
        return p.read_text(encoding="utf-8")
    def fs_write(self, path: str, content: str) -> str:
        p = self.policy.resolve_path("fs.write", path)
        self._log("fs.write", p is not None, path=str(path), size=len(content))
        if p is None:
            raise CapabilityError("fs.write not permitted")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)
    def net_http_get(self, url: str) -> str:
        ok = self.policy.check("net.http")
        self._log("net.http.get", ok, url=url)
        if not ok:
            raise CapabilityError("net.http disabled")
        with open_public_http_url(url, timeout=5) as response:
            return response.read(1_000_001)[:1_000_000].decode("utf-8", errors="ignore")
    def register(self, name: str, func):
        self.plugins[name] = func
    def call(self, name: str, **kwargs):
        if name not in self.plugins:
            raise CapabilityError("plugin not found")
        return self.plugins[name](self, **kwargs)
