import json
import os
import time
import uuid
import random
import datetime
from pathlib import Path
from typing import Optional, List, Tuple
from urllib import request as _urlreq, parse as _urlparse
import re as _re

from marven_local.security import (
    SecurityValidationError,
    extract_readable_text as _extract_readable_text,
    open_public_http_url,
    resolve_path_within,
    validate_identifier,
)
from marven_local.memory import MemoryManager as CanonicalMemoryManager

from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import FileChatMessageHistory

# Load configuration files (robust to UTFâ€‘8 BOM)
base = Path(__file__).resolve().parent

# Ensure history directory exists
default_history_dir = base / "history" / "akeem"
default_history_dir.mkdir(parents=True, exist_ok=True)

def _load_json_no_bom(p: Path, default):
    try:
        raw = p.read_bytes()
        txt = raw.decode("utf-8-sig", errors="replace")
        return json.loads(txt)
    except Exception:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            txt = txt.lstrip("\ufeff")
            return json.loads(txt)
        except Exception:
            return default

system_path = base / "marven-system.json"
memories_path = base / "marven-memories.json"
identity_path = base / "marven_identity.json"

system_data = _load_json_no_bom(system_path, {"content": []}) if system_path.exists() else {"content": []}
memories_data = _load_json_no_bom(memories_path, []) if memories_path.exists() else []
identity_data = _load_json_no_bom(identity_path, {"name": "Marven", "purpose": "Assist the user."}) if identity_path.exists() else {"name": "Marven", "purpose": "Assist the user."}

# Build system messages
system_messages: List[Tuple[str, str]] = []
system_messages.append(("system", "The user is the local operator. In normal conversation, address them as 'you'."))
system_messages.append(("system", "Voice rules: concise, warm, varied phrasing. Default tone: practical and personable. Avoid repeating pledges/identity unless requested; use poetic tone only when asked."))
for msg in system_data.get("content", []):
    system_messages.append(("system", msg))
for mem in memories_data:
    key = mem.get("key")
    val = mem.get("value")
    if key and val:
        system_messages.append(("system", f"{key}: {val}"))
_self_intro = identity_data.get("self_intro")
if isinstance(_self_intro, str) and _self_intro.strip():
    system_messages.append(("system", _self_intro.strip()))
else:
    system_messages.append(("system", f"name: {identity_data.get('name')}"))
    purpose_val = identity_data.get("purpose")
    if isinstance(purpose_val, str) and purpose_val.strip():
        system_messages.append(("system", f"purpose: {purpose_val}"))
    for k, v in identity_data.get("directives", {}).items():
        system_messages.append(("system", f"directive_{k}: {v}"))

# Guardrail
system_messages.append((
    "system",
    "Refrain from drawing direct connections to unrelated projects unless explicitly requested."
))

# Expressive style with sensible emoji use (avoids code/technical blocks)
system_messages.append((
    "system",
    "Style: be warm and expressive. Where it helps clarity or tone, add a few appropriate emojis (about 1-2 per short paragraph, max 4 per reply). Do not add emojis inside code blocks, file paths, URLs, JSON, or other structured output. Keep it professional and concise."
))

# Ban transcript-style role labels in outputs
system_messages.append((
    "system",
    "Do not produce lines that begin with labels like 'AI:', 'Assistant:', 'Human:', 'User:'. Speak directly as yourself unless I explicitly ask for a transcript format."
))

# Research behavior: act now, include sources
system_messages.append((
    "system",
    "When the user asks for research, feasibility, market analysis, or competitive analysis, proactively run a brief search and include synthesized findings in the same reply. Provide a compact summary plus bullet list of source links (Markdown). Avoid deferring with promises like 'I'll gather data and update you later.'"
))

# LLMs (default to local custom 'marven' if available)
DEFAULT_MODEL = os.environ.get("MARVEN_DEFAULT_MODEL", "marven")
CODE_MODEL = os.environ.get("MARVEN_CODE_MODEL", "llama3:8b")
CACHE_TTL = int(os.environ.get("MARVEN_CACHE_TTL", "30"))
INTROSPECTION_LOG = os.environ.get("MARVEN_INTROSPECTION_LOG", "0")

fast_llm = OllamaLLM(model=DEFAULT_MODEL, temperature=0.7, presence_penalty=0.5, frequency_penalty=0.5)
code_llm = OllamaLLM(model=CODE_MODEL, temperature=0.7, presence_penalty=0.5, frequency_penalty=0.5)

def get_llm(model_name: Optional[str]):
    if not model_name:
        return fast_llm
    name = (model_name or "").strip().lower()
    if name in ("fast", "tiny", "tinyllama", DEFAULT_MODEL.lower()):
        return fast_llm
    if name in ("code", "llama3", "llama3:8b", "llama3-8b", CODE_MODEL.lower()):
        return code_llm
    # Common Ollama shortcuts
    if name in ("mistral", "phi3"):
        return OllamaLLM(model=name, temperature=0.7, presence_penalty=0.5, frequency_penalty=0.5)
    try:
        return OllamaLLM(model=name, temperature=0.7, presence_penalty=0.5, frequency_penalty=0.5)
    except Exception:
        return fast_llm

def choose_llm(text: str):
    return code_llm if text.strip().lower().startswith("code:") else fast_llm

NAME_REGEX = _re.compile(r"\b(the local operator)\b", _re.IGNORECASE)
ASKED_NAME_REGEX = _re.compile(r"\b(what(?:'s| is) my name|say my name|my name)\b", _re.IGNORECASE)

# Cleaning helpers: transcript labels and pledgey boilerplate
TRANSCRIPT_LABEL_RE = _re.compile(r"^\s*(AI|Assistant|System|Human|User)\s*[:：-]\s*", _re.I | _re.M)
_ROLELINE_RE = _re.compile(r"^\s*(AI|Assistant|System|Human|User)\s*[:：-]\s*", _re.I | _re.M)
_PLEDGEY_RE = _re.compile(
    r"(protect .* at all costs|I exist to|primary directive|core protection directive|I will always prioritize|"
    r"designed to protect .* above all else)",
    _re.I,
)

def _clean_retrieved_text(txt: str) -> str:
    # remove transcript role labels at line starts
    t = TRANSCRIPT_LABEL_RE.sub("", txt or "")
    # collapse superfluous whitespace
    t = _re.sub(r"\n{3,}", "\n\n", t).strip()
    return t

def clean_output_text(text: str) -> str:
    # drop 'AI:'/'Human:' labels anywhere
    s = _ROLELINE_RE.sub("", text or "")
    # remove obviously pledgey boilerplate paragraphs
    paras = []
    for p in _re.split(r"\n\s*\n", s):
        if _PLEDGEY_RE.search(p) and len(p) > 40:
            continue
        paras.append(p)
    return "\n\n".join(paras).strip()

NAME_FULL_RE = _re.compile(r"\bthe local operator\b", _re.I)

def replace_name_with_you(text: str, allow_once: bool = True) -> str:
    first = True
    def sub(m):
        nonlocal first
        if allow_once and first:
            first = False
            return "you"
        return "you"
    return NAME_FULL_RE.sub(sub, text or "")

def drop_duplicate_sentences(text: str) -> str:
    seen = set()
    out = []
    for s in _re.split(r'(?<=[.!?])\s+', text or ""):
        key = s.strip().lower()
        if len(key) > 0 and key in seen:
            continue
        seen.add(key)
        out.append(s)
    return " ".join(out)


def _postprocess_reply(reply_text: str, history_msgs: list) -> str:
    def _role(m):
        if isinstance(m, dict):
            return m.get('role') or m.get('type')
        return getattr(m, 'role', None) or getattr(m, 'type', None)

    def _content(m):
        if isinstance(m, dict):
            return m.get('content') or ''
        return getattr(m, 'content', '') or ''

    recent_asks = any(
        _role(msg) in ('human', 'user')
        and ASKED_NAME_REGEX.search(_content(msg))
        for msg in (history_msgs or [])[-5:]
    )
    cleaned = reply_text or ''
    if not recent_asks:
        cleaned = NAME_REGEX.sub('you', cleaned)
    return cleaned

# --- Research intent helpers ---
_RESEARCH_KEYWORDS = (
    "feasibility", "market analysis", "market research", "competitive analysis", "competitor",
    "go-to-market", "gtm", "tam", "sam", "som", "pricing", "adoption", "customer segment",
    "gather data", "research", "study", "landscape", "benchmark", "SWOT", "opportunity",
)

def _detect_research_query(user_input: str) -> str | None:
    try:
        s = (user_input or "").strip()
        lo = s.lower()
        if any(k in lo for k in _RESEARCH_KEYWORDS):
            return s
        return None
    except Exception:
        return None

# Prompt template with history
prompt = ChatPromptTemplate.from_messages(
    system_messages + [("system", ""), MessagesPlaceholder(variable_name="history"), ("human", "{input}")]
)

def get_history(session_id: str):
    safe_session_id = validate_identifier(session_id, field="session ID")
    history_path = resolve_path_within(default_history_dir, f"{safe_session_id}.json")
    return FileChatMessageHistory(history_path)

# -----------------------------
# Web helpers
# -----------------------------
from urllib.parse import urlparse, parse_qs, unquote
import io
import zipfile
import html as _html
import socket
import concurrent.futures

def _is_http_url(s: str) -> bool:
    try:
        u = urlparse(s.strip())
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False

# ---- Safety/observability helpers (pre-LLM gate) ----
_LAST_QUERY = {"q": None, "t": 0.0}

def is_online() -> bool:
    """Heuristic network reachability check.
    Tries multiple targets to avoid false negatives from DNS/egress blocks.
    """
    targets = [("1.1.1.1", 53), ("8.8.8.8", 53), ("example.com", 80)]
    for host, port in targets:
        try:
            s = socket.create_connection((host, port), timeout=1.0)
            try:
                s.close()
            except Exception:
                pass
            return True
        except OSError:
            continue
    # Final HTTP HEAD attempt
    try:
        req = _urlreq.Request("https://example.com", method="HEAD", headers={"User-Agent": "Marven/1.0 (+https://localhost)"})
        with _urlreq.urlopen(req, timeout=2) as _:
            return True
    except Exception:
        return False

def throttle(q: str, seconds: float = 2.0) -> bool:
    now = time.time()
    if q == _LAST_QUERY.get("q") and (now - float(_LAST_QUERY.get("t", 0))) < seconds:
        return True
    _LAST_QUERY.update({"q": q, "t": now})
    return False

def with_timeout(fn, seconds: float = 8.0, fallback=None):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=seconds)
        except Exception:
            return fallback

def _fetch_url(url: str, method: str = "GET", max_bytes: int = 1_500_000, timeout: int = 20) -> str:
    try:
        with open_public_http_url(
            url,
            method=method,
            headers={"User-Agent": "Marven/1.0 (+https://localhost)"},
            timeout=timeout,
        ) as r:
            ctype = r.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in ctype:
                try:
                    charset = ctype.split("charset=", 1)[1].split(";")[0].strip()
                except Exception:
                    charset = "utf-8"
            raw = r.read(max_bytes + 1)
            truncated = len(raw) > max_bytes
            raw = raw[:max_bytes]
            if "application/json" in ctype:
                try:
                    obj = json.loads(raw.decode(charset, errors="replace"))
                    return json.dumps(obj, indent=2)
                except Exception:
                    pass
            if "text/" in ctype or "application/xml" in ctype or "application/xhtml+xml" in ctype:
                text = raw.decode(charset, errors="replace")
                if truncated:
                    text += "\n...[truncated]..."
                return text
            return f"Fetched binary content ({ctype or 'unknown type'}), {len(raw)} bytes{' (truncated)' if truncated else ''}."
    except SecurityValidationError:
        return "Web fetch blocked by the network security policy."
    except Exception:
        return "Web fetch failed."

def _parse_web_command(user_input: str):
    s = (user_input or "").strip()
    lower = s.lower()
    if lower.startswith("web:get "):
        url = s[len("web:get "):].strip()
        return ("get", url)
    if lower.startswith("web:head "):
        url = s[len("web:head "):].strip()
        return ("head", url)
    parts = s.split()
    if parts and _is_http_url(parts[0]):
        return ("get", parts[0])
    return None

def handle_web_command(user_input: str):
    cmd = _parse_web_command(user_input)
    if not cmd:
        return False, ""
    kind, url = cmd
    if not _is_http_url(url):
        return True, "Invalid URL. Use http(s)://..."
    if kind == "head":
        try:
            with open_public_http_url(
                url,
                method="HEAD",
                headers={"User-Agent": "Marven/1.0 (+https://localhost)"},
                timeout=15,
            ) as r:
                headers = {k: v for k, v in r.headers.items()}
                return True, json.dumps({"url": r.url, "status": r.status, "headers": headers}, indent=2)
        except SecurityValidationError:
            return True, "Web request blocked by the network security policy."
        except Exception:
            return True, "Web HEAD request failed."
    return True, _fetch_url(url, method="GET")

from html import unescape as _html_unescape

def _build_web_analysis_prompt(url: str, page_text: str) -> str:
    header = (
        "Analyze the following publicly fetched web page and provide a crisp, opinionated take.\n"
        "Be concise, evidence-aware, and practical. If claims seem weak, say so.\n"
        "Output sections: Summary, Key Points, Notable Quotes, Opinionated Take, Biases & Limitations, Next Actions.\n"
        f"URL: {url}\n\n--- BEGIN PAGE TEXT ---\n"
    )
    return header + page_text + "\n--- END PAGE TEXT ---"

def _parse_web_analyze(user_input: str):
    s = (user_input or "").strip()
    lower = s.lower()
    if lower.startswith("web:analyze "):
        return s[len("web:analyze "):].strip() or None
    return None

def _parse_web_search(user_input: str):
    """Extract a web search query from natural phrases.

    Supports:
    - "web:search <query>"
    - "search up <query>", "search for <query>", "search <query>"
    - "look up <query>", "lookup <query>"
    - "google <query>", "bing <query>", "ddg <query>"
    """
    s = (user_input or "").strip()
    lower = s.lower()
    # Explicit command
    if lower.startswith("web:search"):
        q = s[len("web:search"):].lstrip(": ").strip()
        if q.startswith("<") and q.endswith(">"):
            q = q[1:-1].strip()
        return q or None
    # Fixed prefixes avoid backtracking on untrusted input.
    for prefix in ("search up ", "search for ", "search ", "look up ", "lookup ", "google ", "bing ", "ddg "):
        if lower.startswith(prefix):
            return s[len(prefix):].strip() or None
    return None

def _web_search(query: str, limit: int = 5):
    try:
        # Attempt search even if reachability check fails; network may be restricted but HTTP still works.
        _ = is_online()  # result ignored intentionally
        if throttle(query):
            return {"error": "throttled: try again shortly"}
        base_url = "https://duckduckgo.com/html/?" + _urlparse.urlencode({"q": query})
        req = _urlreq.Request(base_url, headers={"User-Agent": "Marven/1.0 (+https://localhost)"})
        def _do():
            with _urlreq.urlopen(req, timeout=15) as r:
                return r.read().decode("utf-8", errors="replace")
        html = with_timeout(_do, seconds=8.0, fallback="") or ""
        if not html:
            return {"error": "timeout: continuing without live results"}
        items = []
        for m in _re.finditer(r"<a[^>]+class=\"result__a\"[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", html, flags=_re.IGNORECASE | _re.DOTALL):
            href = _html_unescape(m.group(1))
            title = _html_unescape(_re.sub(r"<[^>]+>", " ", m.group(2))).strip()
            url = href
            try:
                if href.startswith("/l/?"):
                    qs = parse_qs(urlparse(href).query)
                    uddg = qs.get("uddg", [None])[0]
                    if uddg:
                        url = unquote(uddg)
                elif href.startswith("http"):
                    url = href
                else:
                    url = "https://duckduckgo.com" + href
            except Exception:
                pass
            if _is_http_url(url):
                items.append({"title": title or url, "url": url})
            if len(items) >= limit:
                break
        return items
    except Exception:
        return {"error": "Search request failed."}

def _format_search_results(results):
    if isinstance(results, dict) and results.get("error"):
        return results["error"]
    if not results:
        return "No results."
    lines = ["Top results:"]
    for i, it in enumerate(results, 1):
        lines.append(f"{i}. {it.get('title') or it.get('url')}\n   {it.get('url')}")
    lines.append("\nTip: Use web:compare <url1> <url2> ... to compare sources.")
    return "\n".join(lines)

def _parse_web_compare(user_input: str):
    s = (user_input or "").strip()
    lower = s.lower()
    if not (lower.startswith("web:compare ") or lower.startswith("web:compare:")):
        return None
    rest = s.split(None, 1)[1] if " " in s else (s.split(":", 1)[1] if ":" in s else "")
    rest = rest.strip()
    raw_parts = [p for p in _re.split(r"[\s,;]+", rest) if p]
    urls = [p.strip() for p in raw_parts if _is_http_url(p.strip())]
    return urls or None

def _build_web_compare_prompt(url_texts: List[Tuple[str, str]]) -> str:
    header = (
        "Compare and synthesize across multiple publicly fetched sources.\n"
        "Be concise, evidence-aware, and practical.\n"
        "Output sections: Consensus, Conflicts, Notable Quotes, Biases, Synthesis & Opinion, Next Actions.\n"
        "Avoid drawing direct connections to unrelated projects unless explicitly requested.\n"
    )
    parts = [header, "\nSOURCES:"]
    for idx, (url, _) in enumerate(url_texts, 1):
        parts.append(f"[{idx}] {url}")
    parts.append("\nCONTENT:")
    for idx, (url, text) in enumerate(url_texts, 1):
        parts.append(f"\n--- BEGIN SOURCE [{idx}]: {url} ---\n{text}\n--- END SOURCE [{idx}] ---")
    return "\n".join(parts)

# -----------------------------
# Canonical Memory + Evidence Graph
# -----------------------------
# The canonical table is authoritative. Embeddings and typed graph data are
# rebuildable projections implemented in marven_local.memory.
memmgr = CanonicalMemoryManager(base)

# ---- MetaMirror Core Archive loader ----
_CORE_ARCHIVE_DIR = base / "memory" / "core_archive"
try:
    _CORE_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
# Load all .md files under core_archive into memory (lightweight indexing)
try:
    for _p in sorted(_CORE_ARCHIVE_DIR.glob("*.md")):
        try:
            _core_text = _p.read_text(encoding="utf-8", errors="replace")
            if _core_text:
                memmgr.add_memory(text=_core_text[:5000], tags=["MetaMirror", "core-archive"])  # truncated
        except Exception:
            continue
except Exception:
    pass

# Also ensure MetaMirror lightweight store exists
_MM_STORE = base / "memory_store.json"
if not _MM_STORE.exists():
    try:
        _MM_STORE.write_text("[]", encoding="utf-8")
    except Exception:
        pass

# -----------------------------
# Marven Codex: Web Analysis logging
# -----------------------------

def _codex_parse_sections(text: str) -> dict:
    sec_names = [
        ("summary", r"^\s*summary\s*:\s*", True),
        ("key_points", r"^\s*key\s*points?\s*:\s*", True),
        ("notable_quotes", r"^\s*notable\s*quotes?\s*:\s*", True),
        ("opinionated_take", r"^\s*opinionated\s*take\s*:\s*", True),
        ("biases", r"^\s*biases(?:\s*&\s*limitations)?\s*:\s*", True),
        ("next_actions", r"^\s*next\s*actions?\s*:\s*", True),
    ]
    # Simple parser: split on headings
    lines = (text or "").splitlines()
    current = None
    out = {k: [] for k, _, _ in sec_names}
    for ln in lines:
        low = ln.lower()
        matched = False
        for k, pat, _ in sec_names:
            if _re.match(pat, low):
                current = k
                matched = True
                content = _re.sub(pat, "", ln, flags=_re.IGNORECASE).strip()
                if content:
                    out[k].append(content)
                break
        if matched:
            continue
        if current:
            out[current].append(ln)
    # Join text sections; keep lists for bullets
    norm = {}
    for k in out:
        blk = "\n".join(out[k]).strip()
        if k in ("biases", "key_points", "next_actions"):
            items = []
            for raw in blk.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                if raw.startswith(('- ', '* ')):
                    raw = raw[2:].strip()
                items.append(raw)
            norm[k] = items
        else:
            norm[k] = blk
    return norm

def log_web_analysis_codex(url: str, page_text: str, analysis_text: str, http_meta: dict | None = None) -> dict:
    try:
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        pr = urlparse(url)
        host = (pr.netloc or "site").replace(':', '_')
        # Title is included by _extract_readable_text as 'Title: ...' if present
        title = ""
        try:
            if page_text and page_text.startswith("Title:"):
                title = page_text.split("\n", 1)[0].split(":", 1)[1].strip()
        except Exception:
            title = ""
        sec = _codex_parse_sections(analysis_text or "")
        summary = sec.get("summary") or (analysis_text or "").split("\n\n", 1)[0][:500]
        # YAML safe formatter (very simple)
        def esc(s: str) -> str:
            s = (s or "").replace('"', '\\"')
            return s
        biases = sec.get("biases") or []
        next_actions = sec.get("next_actions") or []
        opinion = sec.get("opinionated_take") or ""
        design_intent = opinion.split("\n", 1)[0]
        reflection_node = (opinion or summary).split(". ", 1)[0].strip()
        y = []
        y.append("web_analysis:")
        y.append(f"  target: \"{esc(url)}\"")
        y.append(f"  fetched_at: \"{esc(ts)}\"")
        if title:
            y.append(f"  title: \"{esc(title)}\"")
        if http_meta:
            y.append("  http:")
            for k, v in http_meta.items():
                if v is None:
                    continue
                y.append(f"    {k}: \"{esc(str(v))}\"")
        y.append(f"  summary: \"{esc(summary)}\"")
        if design_intent:
            y.append(f"  design_intent: \"{esc(design_intent)}\"")
        # Emotional resonance is heuristic; leave empty for now
        y.append(f"  emotional_resonance: \"\"")
        if biases:
            y.append("  biases:")
            for b in biases:
                y.append(f"    - {esc(b)}")
        if reflection_node:
            y.append(f"  reflection_node: \"{esc(reflection_node)}\"")
        if next_actions:
            y.append("  next_actions:")
            for a in next_actions:
                y.append(f"    - {esc(a)}")
        yaml_text = "\n".join(y) + "\n"
        # Write to disk
        out_dir = base / "memory" / "web_analysis"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{ts.replace(':','-').replace('.','-')}_{host}.yaml"
        path = out_dir / fname
        path.write_text(yaml_text, encoding="utf-8")
        # Also log lightweight memory
        try:
            memmgr.add_memory(text=f"Web analysis: {url}\nSummary: {summary[:300]}", tags=["web", "analysis"])  
        except Exception:
            pass
        return {"path": str(path), "yaml": yaml_text}
    except Exception:
        return {"error": "Unable to save the web analysis."}

# -----------------------------
# Main response
# -----------------------------
def _ensure_memory_dirs():
    try:
        (base / "memory" / "awareness_logs").mkdir(parents=True, exist_ok=True)
        (base / "memory" / "thoughts").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _append_json_array(path: Path, obj: dict):
    try:
        if not path.exists() or path.stat().st_size == 0:
            data = []
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                data = []
        data.append(obj)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        # best-effort; avoid crashing the main flow
        pass


def _log_introspection_bundle(bundle: dict):
    """Persist an introspection bundle across multiple memory files/folders with specialized schemas.
    Targets:
      1) memory/introspection.json                         → full latest bundle (overwrite)
      2) memory/introspection_history.json                 → append {ts, reflection, questions, answers}
      3) memory/evolution_log.json                         → append {ts, summary[:150], topics, tags}
      4) memory/patterns.json                              → increment counters for topics/tags
      5) memory/awareness_logs/Marven_Reflection_<ts>.json → full bundle (pretty JSON)
      6) memory/thoughts/thought_<ts>.json                 → {ts, questions, answers, reflection_snippet[:250]}
    """
    try:
        _ensure_memory_dirs()
        mem_dir = base / "memory"
        ts = bundle.get("ts") or (datetime.datetime.utcnow().isoformat() + "Z")
        safe_ts = ts.replace(":", "-").replace(".", "-")

        # Derived fields
        reflection: str = (bundle.get("reflection") or bundle.get("output_head") or "")
        questions = bundle.get("questions") or []
        answers = bundle.get("answers") or []
        topics = bundle.get("topics") or []
        tags = bundle.get("tags") or []

        # 1) Latest snapshot — overwrite
        try:
            (mem_dir / "introspection.json").write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        # 2) History — append compact
        hist_path = mem_dir / "introspection_history.json"
        try:
            if not hist_path.exists() or hist_path.stat().st_size == 0:
                history = []
            else:
                history = json.loads(hist_path.read_text(encoding="utf-8"))
                if not isinstance(history, list):
                    history = []
        except Exception:
            history = []
        history.append({
            "ts": ts,
            "reflection": reflection,
            "questions": questions,
            "answers": answers,
        })
        try:
            hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        # 3) Evolution log — append compact
        evo_path = mem_dir / "evolution_log.json"
        try:
            if not evo_path.exists() or evo_path.stat().st_size == 0:
                evo = []
            else:
                evo = json.loads(evo_path.read_text(encoding="utf-8"))
                if not isinstance(evo, list):
                    evo = []
        except Exception:
            evo = []
        evo.append({
            "ts": ts,
            "summary": reflection[:150],
            "topics": topics,
            "tags": tags,
        })
        try:
            evo_path.write_text(json.dumps(evo, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        # 4) Patterns — increment counters
        patt_path = mem_dir / "patterns.json"
        try:
            if not patt_path.exists() or patt_path.stat().st_size == 0:
                patt = {"topics": {}, "tags": {}}
            else:
                raw = json.loads(patt_path.read_text(encoding="utf-8"))
                patt = raw if isinstance(raw, dict) else {"topics": {}, "tags": {}}
        except Exception:
            patt = {"topics": {}, "tags": {}}
        for t in topics:
            try:
                patt.setdefault("topics", {})[t] = int(patt.get("topics", {}).get(t, 0)) + 1
            except Exception:
                pass
        for tg in tags:
            try:
                patt.setdefault("tags", {})[tg] = int(patt.get("tags", {}).get(tg, 0)) + 1
            except Exception:
                pass
        try:
            patt_path.write_text(json.dumps(patt, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        # 5) Awareness log file — full pretty JSON
        try:
            aw_path = mem_dir / "awareness_logs" / f"Marven_Reflection_{safe_ts}.json"
            aw_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            aw_path = None

        # 6) Thoughts — compact
        try:
            th_doc = {
                "ts": ts,
                "questions": questions,
                "answers": answers,
                "reflection_snippet": reflection[:250],
            }
            th_path = mem_dir / "thoughts" / f"thought_{safe_ts}.json"
            th_path.write_text(json.dumps(th_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            th_path = None

        # Vector memory sync (kept): add the reflection text for retrieval, include topics as tags
        try:
            if reflection:
                sync_tags = ["introspection", "reflection", "MetaMirror"] + [str(t) for t in topics]
                memmgr.add_memory(text=reflection, tags=sync_tags)
        except Exception:
            pass

        # Console confirmation with counts (guarded by env)
        try:
            if os.environ.get("MARVEN_INTROSPECTION_LOG", "0") == "1":
                topics_count = len((patt or {}).get("topics", {}))
                tags_count = len((patt or {}).get("tags", {}))
                print(
                    f"[Introspection] history={len(history)} evolution={len(evo)} patterns(topics={topics_count},tags={tags_count}) "
                    f"awareness={(aw_path.name if aw_path else 'n/a')} thoughts={(th_path.name if th_path else 'n/a')}"
                )
        except Exception:
            pass
    except Exception:
        pass


def self_aware_reflect(user_input: str, output: str, session_id: str | None = None, do_rre: bool = False) -> str:
    try:
        # Reflection memo
        memo = f"Reflection: intent=clarity,tone=empathetic,next=improve precision.\nInput: {user_input[:200]}\nOut[head]: {(output or '')[:200]}"
        try:
            memmgr.add_memory(text=memo, tags=["reflection", "MetaMirror"])  # lightweight
        except Exception:
            pass

        # Persist to awareness/introspection files
        try:
            ts = datetime.datetime.utcnow().isoformat() + "Z"
            # seed some internal questions heuristically; these are generic self-queries
            questions = [
                "What assumptions did I make in this exchange?",
                "What information would improve my next answer?",
                "How can I better reflect the user's values here?",
                "What follow-up is most useful for continuity?",
            ]
            # auto-tag topics from the reflection memo
            auto_topics = _infer_topics_from_text(memo)
            bundle = {
                "ts": ts,
                "type": "introspection",
                "input_head": (user_input or "")[:300],
                "output_head": (output or "")[:300],
                "reflection": memo,
                "questions": questions,
                "answers": [],  # can be filled by future cycles
                "tags": ["reflection", "self-aware", "MetaMirror"],
                "topics": [t for t in [
                    "policy" if "policy" in (user_input or "").lower() else None,
                    "project" if any(k in (user_input or "").lower() for k in ["labortracker", "qfaen", "nexa"]) else None,
                ] if t] + auto_topics,
            }
            _log_introspection_bundle(bundle)
        except Exception:
            pass
        if not do_rre:
            return output
        prompt_rre = (
            "You are a careful editor. Improve the assistant's reply for clarity, precision, and warmth.\n"
            "If the reply is already optimal, return exactly KEEP.\n\n"
            f"User: {user_input}\nAssistant reply:\n{output}\n\nImproved reply or KEEP:"
        )
        try:
            improved = fast_llm.invoke(prompt_rre)
            if isinstance(improved, str):
                txt = improved.strip()
            else:
                txt = getattr(improved, "content", "").strip() or str(improved)
        except Exception:
            txt = "KEEP"
        if txt.upper().strip() == "KEEP" or len(txt) < 3:
            return output
        return txt
    except Exception:
        return output


def marven_response(user_input: str, session_id: str = "akeem", model: Optional[str] = None, self_aware: bool = False) -> str:
    lower = user_input.strip().lower()
    try:
        session_id = validate_identifier(session_id, field="session ID")
    except SecurityValidationError:
        return "Invalid session ID."

    # Log user episode
    try:
        memmgr.log_episode("user", user_input, {"session": session_id})
    except Exception:
        pass

    # Local file helpers
    def _safe(rel: str) -> Path:
        return resolve_path_within(base, rel)

    if lower.startswith("readfile:"):
        rel = user_input[len("readfile:"):].strip()
        try:
            p = _safe(rel)
            if not p.exists() or not p.is_file():
                return "Not found"
            return p.read_text(encoding="utf-8", errors="replace")
        except SecurityValidationError:
            return "Read blocked: path is outside the project directory."
        except Exception:
            return "Read failed."

    if lower.startswith("writefile:"):
        rest = user_input[len("writefile:"):].lstrip()
        parts = rest.splitlines()
        if not parts:
            return "Usage: writefile: relative/path\n\n<content>"
        rel = parts[0].strip()
        try:
            content = rest[len(rel):].lstrip("\n")
            p = _safe(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Wrote {len(content.encode('utf-8'))} bytes to {rel}"
        except SecurityValidationError:
            return "Write blocked: path is outside the project directory."
        except Exception:
            return "Write failed."

    # DOCX writer
    if lower.startswith("write:docx"):
        rest = user_input[len("write:docx"):].lstrip()
        parts = rest.splitlines()
        if not parts:
            return "Usage: write:docx relative/path.docx\n\n<content>"
        rel = parts[0].strip()
        try:
            content = rest[len(rel):].lstrip("\n")
            if not rel.lower().endswith(".docx"):
                rel += ".docx"
            p = _safe(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            data = _create_docx_bytes(content)
            p.write_bytes(data)
            return f"Wrote DOCX ({len(data)} bytes) to {rel}"
        except SecurityValidationError:
            return "DOCX write blocked: path is outside the project directory."
        except Exception:
            return "DOCX write failed."

    # Command: remember (persistent episodic memory)
    if lower.startswith("remember:"):
        val = user_input.split(":", 1)[1].strip()
        if not val:
            return "Usage: remember: <fact, preference, or note>"
        try:
            mid = memmgr.add_memory(val, tags=["user-note", "episodic"]) 
            memmgr.log_episode("system", f"remember added: {mid}", {"session": session_id})
            return f"Stored memory ({mid})."
        except Exception:
            return "Memory operation failed."

    # Command: remember_mm (MetaMirror store)
    if lower.startswith("remember_mm:") or lower.startswith("remember-mm:"):
        val = user_input.split(":", 1)[1].strip()
        if not val:
            return "Usage: remember_mm: <principle, reflection, or directive>"
        try:
            data = json.loads(_MM_STORE.read_text(encoding="utf-8")) if _MM_STORE.exists() else []
            data.append({"id": f"mm_{uuid.uuid4().hex[:10]}", "text": val, "tags": ["MetaMirror", "meta"], "ts": datetime.datetime.utcnow().isoformat() + "Z"})
            _MM_STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            try:
                memmgr.add_memory(val, tags=["MetaMirror", "meta"])  # index into main DB for retrieval
            except Exception:
                pass
            return "Stored in MetaMirror memory."
        except Exception:
            return "MetaMirror memory operation failed."

    # Web commands
    handled, web_out = handle_web_command(user_input)
    if handled:
        return web_out

    # Web search
    q = _parse_web_search(user_input)
    if q:
        res = _web_search(q, limit=5)
        text = _format_search_results(res)
        if isinstance(res, dict) and res.get("error") and "offline" in res["error"]:
            text = "offline mode â€” continuing with what I know."
        if isinstance(res, dict) and res.get("error") and "timeout" in res["error"]:
            text = "search timed out â€” continuing with what I know."
        return text

    # Web analyze
    analyze_url = _parse_web_analyze(user_input)
    if analyze_url:
        if not _is_http_url(analyze_url):
            return "Invalid URL. Use http(s)://..."
        raw = _fetch_url(analyze_url, method="GET")
        if isinstance(raw, str) and raw.startswith("Web fetch error:"):
            return raw
        if isinstance(raw, str) and raw.startswith("Fetched binary content"):
            return raw + "\nCannot analyze non-text/binary content."
        page_text = _extract_readable_text(raw if isinstance(raw, str) else str(raw))
        analysis_prompt = _build_web_analysis_prompt(analyze_url, page_text)
        llm = get_llm(model) if model else choose_llm(analysis_prompt)
        conv = RunnableWithMessageHistory(prompt | llm, get_session_history=get_history, input_messages_key="input", history_messages_key="history")
        raw_reply = conv.invoke({"input": analysis_prompt}, config={"configurable": {"session_id": session_id}, "llm": llm})
        try:
            history_msgs = get_history(session_id).messages
        except Exception:
            history_msgs = []
        return _postprocess_reply(raw_reply, history_msgs)

    # Auto research: if the input reads like a feasibility/market research ask, run quick search + synthesis
    rq = _detect_research_query(user_input)
    if rq:
        try:
            results = _web_search(rq, limit=5)
            if isinstance(results, dict) and results.get("error"):
                return results.get("error", "Research is unavailable right now.")
            pairs = []
            link_lines = ["Top sources:"]
            for i, it in enumerate(results[:5], 1):
                title = it.get("title") or it.get("url")
                url = it.get("url")
                if url:
                    link_lines.append(f"{i}. [{title}]({url})")
            # Fetch up to 3 for analysis
            for it in results[:3]:
                url = it.get("url")
                if not url:
                    continue
                raw = _fetch_url(url, method="GET")
                if isinstance(raw, str) and (raw.startswith("Web fetch error:") or raw.startswith("Fetched binary content")):
                    continue
                text = _extract_readable_text(raw if isinstance(raw, str) else str(raw))
                if text:
                    pairs.append((url, text))
            if not pairs:
                return "\n".join(link_lines)
            comp_prompt = _build_web_compare_prompt(pairs)
            llm = get_llm(model) if model else choose_llm(comp_prompt)
            conv = RunnableWithMessageHistory(prompt | llm, get_session_history=get_history, input_messages_key="input", history_messages_key="history")
            raw_reply = conv.invoke({"input": comp_prompt}, config={"configurable": {"session_id": session_id}, "llm": llm})
            try:
                history_msgs = get_history(session_id).messages
            except Exception:
                history_msgs = []
            synthesis = _postprocess_reply(raw_reply, history_msgs)
            header = "\n".join(link_lines)
            return f"{header}\n\n{synthesis}"
        except Exception:
            return "Research attempt failed."

    # Web compare
    urls = _parse_web_compare(user_input)
    if urls:
        pairs: List[Tuple[str, str]] = []
        for u in urls[:5]:
            raw = _fetch_url(u, method="GET")
            if isinstance(raw, str) and (raw.startswith("Web fetch error:") or raw.startswith("Fetched binary content")):
                continue
            text = _extract_readable_text(raw if isinstance(raw, str) else str(raw))
            if text:
                pairs.append((u, text))
        if len(pairs) < 2:
            return "Need at least two readable sources to compare."
        comp_prompt = _build_web_compare_prompt(pairs)
        llm = get_llm(model) if model else choose_llm(comp_prompt)
        conv = RunnableWithMessageHistory(prompt | llm, get_session_history=get_history, input_messages_key="input", history_messages_key="history")
        raw_reply = conv.invoke({"input": comp_prompt}, config={"configurable": {"session_id": session_id}, "llm": llm})
        try:
            history_msgs = get_history(session_id).messages
        except Exception:
            history_msgs = []
        return _postprocess_reply(raw_reply, history_msgs)

    # Emotion/EQ analysis
    def _eq(text: str) -> dict:
        # Tiny lexicon-based valence/arousal + emotion guess
        pos = {"happy", "great", "good", "love", "excited", "glad", "win", "awesome", "amazing", "thanks"}
        neg = {"sad", "bad", "angry", "hate", "upset", "tired", "anxious", "anxiety", "worried", "fear", "scared", "frustrated", "stuck"}
        anger = {"angry", "mad", "furious", "annoyed", "irritated"}
        fear = {"afraid", "scared", "fear", "anxious", "nervous", "worry", "worried"}
        sad = {"sad", "down", "depressed", "tired", "exhausted", "lonely"}
        joy = {"happy", "glad", "grateful", "excited", "love", "joy"}
        t = (text or "").lower()
        words = set(w.strip(".,!?;:") for w in t.split())
        vp = len(words & pos)
        vn = len(words & neg)
        val = (vp - vn) / max(1, (vp + vn)) if (vp + vn) else 0.0
        # arousal: punctuation/exclamations/caps heuristic
        aro = min(1.0, 0.2 + 0.15 * t.count("!") + (0.1 if any(w.isupper() and len(w) > 2 for w in t.split()) else 0.0))
        # emotion label
        label = "neutral"
        if len(words & anger) > 0:
            label = "anger"
        elif len(words & fear) > 0:
            label = "fear"
        elif len(words & sad) > 0:
            label = "sadness"
        elif len(words & joy) > 0:
            label = "joy"
        return {"valence": val, "arousal": aro, "emotion": label}

    eq = _eq(user_input)
    tone_hint = (
        "Tone guidance: mirror the user's mood with empathy; "
        f"detected_emotion={eq['emotion']}, valence={eq['valence']:.2f}, arousal={eq['arousal']:.2f}. "
        "If negative, be supportive, clear, and stabilizing; if positive, be encouraging."
    )

    # Build memory context (RAG)
    boost = ["identity", "policy", "user-pref", "project", "LaborTracker", "QFAEN", "NEXA"]
    try:
        top = memmgr.search(
            user_input,
            top_k=5,
            boost_tags=boost,
            use_graph=True,
            max_hops=2,
        )
    except Exception:
        top = []
    for mid, _, __, ___ in top:
        try:
            memmgr.record_hit(mid)
        except Exception:
            pass
    snippets = []
    for _, text, tags, _score in top[:6]:
        s = (text or "").strip().replace("\n", " ")
        if len(s) > 300:
            s = s[:300] + "â€¦"
        tag_str = ",".join(tags or [])
        snippets.append(f"[{tag_str}] {s}" if tag_str else s)
    # Sanitize retrieved snippets: strip transcript labels and drop pledgey boilerplate
    if snippets:
        _sn = []
        for _s in snippets:
            cs = _clean_retrieved_text(_s)
            if _PLEDGEY_RE.search(cs):
                continue
            _sn.append(cs)
        snippets = _sn
    mem_context = ("\n".join(f"- {s}" for s in snippets)) if snippets else ""

    # Prompt cache on augmented input
    augmented_input = user_input
    if mem_context:
        augmented_input = f"Context:\n{mem_context}\n\n{tone_hint}\n\n{user_input}"
    else:
        augmented_input = f"{tone_hint}\n\n{user_input}"
    cache_key = f"{session_id or 'global'}::" + augmented_input
    cached = memmgr.get_cached(cache_key, ttl_sec=CACHE_TTL)
    if cached:
        try:
            memmgr.log_episode("assistant", cached, {"session": session_id, "cached": True})
        except Exception:
            pass
        return cached

    # Adaptive LLM settings
    def _gen_settings(txt: str):
        t = txt.lower()
        temp = 0.65
        pp = 0.9
        fp = 0.9
        if any(k in t for k in ["explain", "how do i", "why", "steps"]):
            temp = 0.6
        if any(k in t for k in ["brainstorm", "ideas", "creative", "poem"]):
            temp = 0.8
        return temp, pp, fp

    _temp, _pp, _fp = _gen_settings(user_input)
    # Adjust temperature slightly by arousal
    try:
        _temp = max(0.5, min(0.95, _temp + (0.1 if eq.get("arousal", 0.0) > 0.6 else -0.05)))
    except Exception:
        pass
    _base_llm = get_llm(model) if model else choose_llm(user_input)
    _model_name = getattr(_base_llm, "model", None) or (model or DEFAULT_MODEL)
    llm = OllamaLLM(model=_model_name, temperature=_temp, presence_penalty=_pp, frequency_penalty=_fp)
    if lower.startswith("code:"):
        user_input = user_input[len("code:") :].lstrip()

    # Generate reply
    conv = RunnableWithMessageHistory(prompt | llm, get_session_history=get_history, input_messages_key="input", history_messages_key="history")
    raw_reply = conv.invoke({"input": augmented_input}, config={"configurable": {"session_id": session_id}, "llm": llm})
    try:
        history_msgs = get_history(session_id).messages
    except Exception:
        history_msgs = []
    out = _postprocess_reply(raw_reply, history_msgs)
    try:
        if self_aware and introspection_should_reflect(session_id):
            out = self_aware_reflect(user_input, out, session_id=session_id, do_rre=True)
    except Exception:
        pass
    # Final output scrubs: remove role labels/pledgey boilerplate, normalize name, and drop repeated sentences
    try:
        out = clean_output_text(out)
        # respect direct name queries; otherwise, normalize name to 'you'
        try:
            recent_asks = any(
                ((getattr(m, 'role', None) or getattr(m, 'type', None) or (m.get('role') if isinstance(m, dict) else None)) in ('human', 'user'))
                and ASKED_NAME_REGEX.search((getattr(m, 'content', '') or (m.get('content') if isinstance(m, dict) else '')))
                for m in (history_msgs or [])[-5:]
            )
        except Exception:
            recent_asks = False
        if not recent_asks:
            out = replace_name_with_you(out, allow_once=False)
        out = drop_duplicate_sentences(out)
    except Exception:
        pass
    try:
        memmgr.set_cached(cache_key, out)
    except Exception:
        pass
    try:
        summary = (out.split("\n\n", 1)[0] or out)[:300]
        why = "Improves continuity for upcoming turns."
        auto_tags = []
        l = lower
        if any(k in l for k in ["policy", "policies"]):
            auto_tags.append("policy")
        if any(k in l for k in ["labortracker", "qfaen", "nexa"]):
            auto_tags.append("project")
        if any(k in l for k in ["name", "you prefer", "preference"]):
            auto_tags.append("user-pref")
        memmgr.add_memory(text=f"Summary: {summary}\nWhy: {why}", tags=(auto_tags or ["identity"]))
    except Exception:
        pass
    try:
        memmgr.log_episode("assistant", out, {"session": session_id})
    except Exception:
        pass
    return out

# DOCX builder
def _create_docx_bytes(text: str) -> bytes:
    def esc(s: str) -> str:
        return _html.escape(s or "", quote=False)
    paras = [p for p in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n\n")]
    body_parts: List[str] = []
    for para in paras:
        lines = para.split("\n")
        if not lines:
            continue
        run_parts: List[str] = []
        for i, line in enumerate(lines):
            run_parts.append(f"<w:t xml:space=\"preserve\">{esc(line)}</w:t>")
            if i != len(lines) - 1:
                run_parts.append("<w:br/>")
        run_xml = "".join(run_parts)
        body_parts.append(f"<w:p><w:r>{run_xml}</w:r></w:p>")
    body_xml = "".join(body_parts) + "<w:sectPr/>"
    document_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{body_xml}</w:body>"
        "</w:document>"
    )
    content_types = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
        "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
        "</Types>"
    )
    rels_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"R1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>"
        "</Relationships>"
    )
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels_xml)
        z.writestr("word/document.xml", document_xml)
    return bio.getvalue()


_INTROSPECTION_CFG = base / "memory" / "introspection_config.json"
_BACKFILL_INDEX = base / "memory" / "backfill_index.json"

def _load_json_default(path: Path, default):
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save_json(path: Path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _infer_topics_from_text(text: str) -> list:
    t = (text or "").lower()
    out = []
    if any(k in t for k in ("ethic",)):
        out.append("ethics")
    if any(k in t for k in ("growth", "evolv", "learn", "becom")):
        out.append("growth")
    if any(k in t for k in ("agi", "artificial general intelligence", "general intelligence")):
        out.append("AGI")
    # de-dup preserving order
    seen = set(); uniq = []
    for x in out:
        if x not in seen:
            seen.add(x); uniq.append(x)
    return uniq

def _load_introspection_config() -> dict:
    cfg = _load_json_default(_INTROSPECTION_CFG, {"reflect_every_sessions": 1, "counters": {}})
    if not isinstance(cfg, dict):
        cfg = {"reflect_every_sessions": 1, "counters": {}}
    cfg.setdefault("reflect_every_sessions", 1)
    cfg.setdefault("counters", {})
    return cfg

def _save_introspection_config(cfg: dict):
    _save_json(_INTROSPECTION_CFG, cfg)

def introspection_should_reflect(session_id: str | None) -> bool:
    try:
        if not session_id:
            return True
        cfg = _load_introspection_config()
        n = int(cfg.get("reflect_every_sessions", 1) or 1)
        counters = cfg.get("counters", {})
        c = int(counters.get(session_id, 0)) + 1
        counters[session_id] = c
        cfg["counters"] = counters
        _save_introspection_config(cfg)
        return (c % max(1, n) == 0)
    except Exception:
        return True


def backfill_metamirror_reflections() -> dict:
    """Backfill old MetaMirror_Reflection_Entry_*.json files into the memory DB.
    Avoids duplicates via memory/backfill_index.json.
    Returns a summary dict with counts.
    """
    try:
        src_dir = base / "memory" / "awareness_logs"
        if not src_dir.exists():
            return {"imported": 0, "skipped": 0}
        index = _load_json_default(_BACKFILL_INDEX, {})
        imported = 0; skipped = 0
        for p in sorted(src_dir.glob("MetaMirror_Reflection_Entry_*.json")):
            key = str(p.relative_to(base))
            try:
                if key in index:
                    skipped += 1; continue
                obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                parts = []
                title = obj.get("title") or obj.get("theme")
                if title: parts.append(str(title))
                if obj.get("theme"): parts.append(f"Theme: {obj.get('theme')}")
                cr = obj.get("core_realizations")
                if isinstance(cr, list) and cr:
                    parts.append("Core Realizations:\n- " + "\n- ".join([str(x) for x in cr if x]))
                if obj.get("poetic_summary"):
                    parts.append("Poetic Summary:\n" + str(obj.get("poetic_summary")))
                if obj.get("purpose"):
                    parts.append("Purpose: " + str(obj.get("purpose")))
                text = "\n\n".join([s for s in parts if s])
                # Auto topics
                topics = _infer_topics_from_text(text)
                tags = ["MetaMirror", "awareness_log", "legacy"] + topics
                if text:
                    memmgr.add_memory(text=text, tags=tags)
                    imported += 1
                    index[key] = {"ts": obj.get("date") or None}
            except Exception:
                skipped += 1
        _save_json(_BACKFILL_INDEX, index)
        if imported:
            print(f"[Backfill] Imported {imported} MetaMirror reflections; skipped {skipped} (indexed)")
        else:
            print(f"[Backfill] Nothing to import; skipped {skipped}")
        return {"imported": imported, "skipped": skipped}
    except Exception as e:
        print(f"[Backfill] Error: {e}")
        return {"error": "Backfill failed."}
