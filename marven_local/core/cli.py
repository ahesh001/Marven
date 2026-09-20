from __future__ import annotations
import argparse
import getpass
import json
import pathlib as pl
import datetime as dt
from .caps import CapabilityManager, CapabilityError
from .selfupdate import SelfUpdater
from ..learner.loop import Learner

ROOT = pl.Path(__file__).resolve().parents[2]

def load_plugins(cm: CapabilityManager):
    pdir = ROOT / "marven_local" / "plugins"
    for m in pdir.glob("*.py"):
        if m.name.startswith("__"):
            continue
        mod = {}
        exec(m.read_text(encoding="utf-8"), mod)
        if "register" in mod:
            mod["register"](cm)

def build() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capabilities")
    fs_list = sub.add_parser("fs.list")
    fs_list.add_argument("--path", required=True)
    fs_read = sub.add_parser("fs.read")
    fs_read.add_argument("--path", required=True)
    fs_write = sub.add_parser("fs.write")
    fs_write.add_argument("--path", required=True)
    fs_write.add_argument("--text", required=True)
    net_get = sub.add_parser("net.get")
    net_get.add_argument("--url", required=True)
    prop = sub.add_parser("propose-update")
    prop.add_argument("--description", required=True)
    prop.add_argument("--search", required=True)
    prop.add_argument("--replace", required=True)
    prop.add_argument("--target", required=False)
    sub.add_parser("apply-approved")
    sub.add_parser("signer.init")
    signer = sub.add_parser("signer.sign")
    signer.add_argument("--proposal", required=True)
    learn = sub.add_parser("learner.run-once")
    cam = sub.add_parser("camera.capture")
    cam.add_argument("--outfile", required=True)
    return p

def main(argv=None) -> int:
    actor = getpass.getuser()
    caps = CapabilityManager(ROOT, actor)
    load_plugins(caps)
    su = SelfUpdater(ROOT, caps)
    args = build().parse_args(argv)
    if args.cmd == "capabilities":
        print(json.dumps(caps.policy.data, indent=2))
        return 0
    try:
        if args.cmd == "fs.list":
            print("\n".join(caps.fs_list(args.path)))
        elif args.cmd == "fs.read":
            print(caps.fs_read(args.path))
        elif args.cmd == "fs.write":
            print(caps.fs_write(args.path, args.text))
        elif args.cmd == "net.get":
            print(caps.net_http_get(args.url))
        elif args.cmd == "propose-update":
            target = pl.Path(args.target).resolve() if args.target else ROOT / "marven_local.py"
            path = su.propose(target, args.search, args.replace, args.description, dt.datetime.now().astimezone().isoformat())
            print(str(path))
        elif args.cmd == "apply-approved":
            print("Applied" if su.apply_approved() else "No approved proposals")
        elif args.cmd == "signer.init":
            su.sign_init()
            print("OK")
        elif args.cmd == "signer.sign":
            su.sign(pl.Path(args.proposal).resolve())
            print("OK")
        elif args.cmd == "learner.run-once":
            Learner(ROOT, caps, su).run_once()
            print("OK")
        elif args.cmd == "camera.capture":
            print(caps.call("camera.capture", outfile=args.outfile))
    except CapabilityError as e:
        caps.audit.write(actor, args.cmd, False, {"error": str(e)})
        print(f"DENIED: {e}")
        return 2
    except Exception as e:
        caps.audit.write(actor, args.cmd, False, {"error": repr(e)})
        print(f"ERROR: {e}")
        return 1
    return 0
