#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

import marven
from marven import marven_response


def cmd_say(args):
    out = marven_response(args.text, session_id=args.session, model=args.model)
    print(out)


def cmd_remember(args):
    text = args.text.strip()
    if not text:
        print("Usage: remember <text>")
        return 2
    mid = marven.memmgr.add_memory(text, tags=["user-note", "episodic"])
    print(f"Stored memory ({mid})")


def cmd_remember_mm(args):
    text = args.text.strip()
    if not text:
        print("Usage: remember-mm <text>")
        return 2
    store = Path(__file__).parent / "memory_store.json"
    try:
        data = json.loads(store.read_text(encoding="utf-8")) if store.exists() else []
    except Exception:
        data = []
    data.append({"id": f"mm_cli_{len(data)+1}", "text": text, "tags": ["MetaMirror", "meta"]})
    store.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        marven.memmgr.add_memory(text, tags=["MetaMirror", "meta"])
    except Exception:
        pass
    print("Stored in MetaMirror memory")


def cmd_search(args):
    q = args.query
    res = marven._web_search(q, limit=args.limit)
    text = marven._format_search_results(res)
    print(text)


def build():
    p = argparse.ArgumentParser(prog="marven")
    sub = p.add_subparsers(dest="cmd", required=True)

    say = sub.add_parser("say", help="Ask Marven for a response")
    say.add_argument("text")
    say.add_argument("-s", "--session", default="cli")
    say.add_argument("-m", "--model")
    say.set_defaults(func=cmd_say)

    r = sub.add_parser("remember", help="Store a user memory")
    r.add_argument("text")
    r.set_defaults(func=cmd_remember)

    rmm = sub.add_parser("remember-mm", help="Store in MetaMirror memory")
    rmm.add_argument("text")
    rmm.set_defaults(func=cmd_remember_mm)

    sea = sub.add_parser("search", help="DuckDuckGo search via backend helper")
    sea.add_argument("query")
    sea.add_argument("-k", "--limit", type=int, default=5)
    sea.set_defaults(func=cmd_search)

    return p


def main(argv=None) -> int:
    args = build().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

