from __future__ import annotations
import json
import pathlib as pl
import datetime as dt
from ..core.selfupdate import SelfUpdater
from ..core.caps import CapabilityManager

class Learner:
    def __init__(self, root: pl.Path, caps: CapabilityManager, su: SelfUpdater):
        self.root = root
        self.obsdir = root / "observations"
        self.caps = caps
        self.su = su
        self.obsdir.mkdir(parents=True, exist_ok=True)
    def run_once(self):
        for f in sorted(self.obsdir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                    target = pl.Path(o["target"]).resolve()
                    self.su.propose(target, o["search"], o["replace"], o.get("description", "auto"), dt.datetime.now().astimezone().isoformat())
                except Exception:
                    continue
