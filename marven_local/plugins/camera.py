from __future__ import annotations
import pathlib as pl
from ..core.caps import CapabilityError

def register(cm):
    def capture(cm, outfile: str):
        p = pl.Path(outfile)
        ok = cm.policy.check("device.camera", path=p)
        cm._log("device.camera.capture", ok, path=str(p))
        if not ok:
            raise CapabilityError("device.camera disabled")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("frame", encoding="utf-8")
        return str(p)
    cm.register("camera.capture", lambda cm=cm, **kw: capture(cm, **kw))
