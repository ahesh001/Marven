from __future__ import annotations
import json
import pathlib as pl
import datetime as dt

class Audit:
    def __init__(self, root: pl.Path):
        self.dir = root / "audit"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "log.ndjson"
    def write(self, actor: str, action: str, ok: bool, details: dict):
        line = json.dumps({"time": dt.datetime.now().astimezone().isoformat(), "actor": actor, "action": action, "ok": ok, "details": details}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
