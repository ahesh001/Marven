from __future__ import annotations
import pathlib as pl
from typing import Any, Dict, Optional, List

from ..security import resolve_path_within

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

DEFAULT_POLICY = """capabilities:
  fs.read:
    enabled: true
    allow_paths:
      - ./
  fs.write:
    enabled: false
    allow_paths:
      - ./sandbox
  net.http:
    enabled: false
  device.camera:
    enabled: false
  device.microphone:
    enabled: false
  self.update:
    enabled: true
"""

class Policy:
    def __init__(self, root: pl.Path):
        self.root = root
        self.path = root / "policy.yaml"
        if not self.path.exists():
            self.path.write_text(DEFAULT_POLICY, encoding="utf-8")
        if yaml is not None:
            self.data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        else:
            data: Dict[str, Any] = {"capabilities": {}}
            current = None
            allow: List[str] = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.endswith(":") and not s.startswith("capabilities") and not s.startswith("allow_paths"):
                    current = s[:-1]
                    data["capabilities"][current] = {}
                    allow = []
                elif s.startswith("enabled:") and current:
                    data["capabilities"][current]["enabled"] = s.split(":")[1].strip().lower() == "true"
                elif s.startswith("- ") and current:
                    allow.append(s[2:])
                    data["capabilities"][current]["allow_paths"] = allow
            self.data = data
    def resolve_path(self, cap: str, path: pl.Path) -> Optional[pl.Path]:
        """Return an authorized, resolved path or ``None`` when access is denied."""
        cfg = self.data.get("capabilities", {}).get(cap, {})
        if not cfg or not cfg.get("enabled", False):
            return None

        supplied = pl.Path(path)
        candidate = supplied.resolve() if supplied.is_absolute() else (self.root / supplied).resolve()
        for allowed_path in cfg.get("allow_paths", []):
            allowed_root = pl.Path(allowed_path)
            if not allowed_root.is_absolute():
                allowed_root = self.root / allowed_root
            try:
                return resolve_path_within(allowed_root, candidate)
            except ValueError:
                continue
        return None

    def check(self, cap: str, path: Optional[pl.Path] = None) -> bool:
        cfg = self.data.get("capabilities", {}).get(cap, {})
        if not cfg or not cfg.get("enabled", False):
            return False
        if path is None:
            return True
        return self.resolve_path(cap, path) is not None
