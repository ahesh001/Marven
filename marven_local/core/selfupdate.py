from __future__ import annotations
import base64
import dataclasses as dc
import getpass
import hashlib
import json
import pathlib as pl
from datetime import datetime, timezone
from .caps import CapabilityError, CapabilityManager
from ..security import resolve_path_within
from ..utils.crypto import load_or_create_key, hmac_hex, verify_hmac

@dc.dataclass
class Proposal:
    id: str
    time: str
    author: str
    description: str
    target_file: str
    sha256: str
    diff: str
    new_content_b64: str

class SelfUpdater:
    def __init__(self, root: pl.Path, caps: CapabilityManager):
        self.root = root
        self.caps = caps
        self.policy = caps.policy
        self.updates = root / "updates"
        self.props = self.updates / "proposals"
        self.applied = self.updates / "applied"
        self.props.mkdir(parents=True, exist_ok=True)
        self.applied.mkdir(parents=True, exist_ok=True)
        self.key_path = self.updates / "approve.key"
    def propose(self, target: pl.Path, search: str, replace: str, description: str, now_iso: str) -> pl.Path:
        if not self.policy.check("self.update"):
            raise CapabilityError("self.update disabled")
        target = resolve_path_within(self.root, target)
        src = target.read_text(encoding="utf-8")
        if search not in src:
            raise ValueError("search not found")
        updated = src.replace(search, replace)
        import difflib
        diff = "\n".join(difflib.unified_diff(src.splitlines(), updated.splitlines(), fromfile=str(target), tofile=str(target)+".new", lineterm="")) + "\n"
        sha = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        pid = hashlib.sha256((description+diff).encode("utf-8")).hexdigest()[:12]
        prop = Proposal(id=pid, time=now_iso, author=getpass.getuser(), description=description, target_file=str(target), sha256=sha, diff=diff, new_content_b64=base64.b64encode(updated.encode("utf-8")).decode("ascii"))
        timestamp_slug = "".join(ch if ch.isalnum() else "-" for ch in now_iso).strip("-")
        fname = f"{timestamp_slug}_{pid}.patch"
        path = self.props / fname
        content = {"id": prop.id, "time": prop.time, "author": prop.author, "description": prop.description, "target_file": prop.target_file, "sha256": prop.sha256, "new_content_b64": prop.new_content_b64, "diff": prop.diff}
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        approve = path.with_suffix(".APPROVE.json")
        approve.write_text(json.dumps({"proposal": path.name, "expected_sha256": prop.sha256, "approved": False, "signature": ""}, indent=2), encoding="utf-8")
        self.caps.audit.write(self.caps.actor, "self.update.propose", True, {"proposal": str(path)})
        return path
    def propose_edit_in_file(self, target: pl.Path, search: str, replace: str, description: str) -> pl.Path:
        now_iso = datetime.now(timezone.utc).isoformat()
        return self.propose(target, search, replace, description, now_iso)
    def sign_init(self):
        load_or_create_key(self.key_path)
    def sign(self, proposal_path: pl.Path):
        key = load_or_create_key(self.key_path)
        data = json.loads(proposal_path.read_text(encoding="utf-8"))
        msg = (data["id"]+data["sha256"]).encode("utf-8")
        sig = hmac_hex(key, msg)
        ap = proposal_path.with_suffix(".APPROVE.json")
        ap.write_text(json.dumps({"proposal": proposal_path.name, "expected_sha256": data["sha256"], "approved": True, "signature": sig}, indent=2), encoding="utf-8")
    def apply_approved(self) -> bool:
        changed = False
        for p in self.props.glob("*.patch"):
            a = p.with_suffix(".APPROVE.json")
            if not a.exists():
                continue
            try:
                ad = json.loads(a.read_text(encoding="utf-8"))
                if not ad.get("approved"):
                    continue
                pd = json.loads(p.read_text(encoding="utf-8"))
                if ad.get("expected_sha256") != pd.get("sha256"):
                    self.caps.audit.write(self.caps.actor, "self.update.apply", False, {"proposal": p.name, "error": "sha mismatch"})
                    continue
                key = load_or_create_key(self.key_path)
                msg = (pd["id"]+pd["sha256"]).encode("utf-8")
                if not verify_hmac(key, msg, ad.get("signature", "")):
                    self.caps.audit.write(self.caps.actor, "self.update.apply", False, {"proposal": p.name, "error": "bad signature"})
                    continue
                target = self.policy.resolve_path("fs.write", pd["target_file"])
                if target is None:
                    self.caps.audit.write(self.caps.actor, "self.update.apply", False, {"proposal": p.name, "error": "write not allowed"})
                    continue
                content = pd.get("new_content_b64", "")
                target.write_text(base64.b64decode(content).decode("utf-8"), encoding="utf-8")
                self.caps.audit.write(self.caps.actor, "self.update.apply", True, {"target": str(target)})
                apdst = (self.applied / p.name)
                apdst.parent.mkdir(parents=True, exist_ok=True)
                p.rename(apdst)
                a.rename(self.applied / a.name)
                changed = True
            except Exception as e:
                self.caps.audit.write(self.caps.actor, "self.update.apply", False, {"proposal": p.name, "error": repr(e)})
        return changed
