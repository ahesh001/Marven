import argparse
import os
import sys
import time
from pathlib import Path


def _import_marven():
    # Ensure project root is on path when launched from subfolders
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import marven  # type: ignore
        return marven
    except ModuleNotFoundError as exc:
        name = getattr(exc, 'name', '') or str(exc)
        if 'langchain_ollama' in name:
            print("[error] Missing dependency: langchain-ollama")
            print("Install into your venv, e.g.:")
            print("  C\\Marven\\.venv311\\Scripts\\python.exe -m pip install langchain-ollama langchain langchain-community langchain-core")
            raise SystemExit(1)
        raise


def delete_session_history(session_id: str) -> None:
    # marven.py stores histories under history/akeem/{session}.json
    history_dir = Path("history") / "akeem"
    p = history_dir / f"{session_id}.json"
    try:
        if p.exists():
            p.unlink()
            print(f"[ok] Cleared history for session '{session_id}' -> {p}")
        else:
            print(f"[info] No history file found for session '{session_id}'")
    except Exception as e:
        print(f"[warn] Could not clear history: {e}")


def main():
    parser = argparse.ArgumentParser(description="Simple text chat for Marven (no audio)")
    parser.add_argument("-s", "--session", default="chat", help="Session id (default: chat)")
    parser.add_argument("-m", "--model", default=None, help="Override model name (uses marven defaults if omitted)")
    args = parser.parse_args()

    marven = _import_marven()

    print("\nMarven Text Chat (no audio)")
    print("Commands: /exit, /quit, /session NEW_ID, /clearhistory")
    print(f"Session: {args.session} | Model: {args.model or '(marven default)'}\n")

    session_id = args.session

    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break

        if not user:
            continue
        if user.lower() in {"/exit", "/quit"}:
            print("bye.")
            break
        if user.lower().startswith("/session "):
            session_id = user.split(" ", 1)[1].strip() or session_id
            print(f"[ok] Switched session -> {session_id}")
            continue
        if user.lower() == "/clearhistory":
            delete_session_history(session_id)
            continue

        t0 = time.time()
        try:
            reply = marven.marven_response(user, session_id=session_id, model=args.model)
        except Exception as e:
            print(f"[error] marven_response failed: {e}")
            continue
        dt = time.time() - t0
        print(f"marven> {reply}")
        print(f"({dt:.2f}s)\n")


if __name__ == "__main__":
    main()
