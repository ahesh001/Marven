import json
import subprocess
import time
import uuid
from pathlib import Path
import os
from flask import Flask, request, jsonify, send_file
from flask import Response, stream_with_context
from flask_cors import CORS
import csv
from urllib import request as _urlreq
from marven_local.security import (
    SecurityValidationError,
    extract_fenced_updates as _extract_fenced_updates,
    resolve_path_within,
    validate_identifier,
)
try:
    import marven_local as marven_local
except Exception:
    marven_local = None
import marven
from marven import marven_response  # make sure marven.py is in the same folder


BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "history" / "akeem"

# ---- vLLM (local Marven model) integration ----
# Configure via env vars; talks directly to vLLM's OpenAI-compatible HTTP endpoints without SDKs.
VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8001/v1")
_VLLM_MODELS_ENV = os.environ.get("VLLM_MODELS", "")
VLLM_MODELS = {m.strip() for m in _VLLM_MODELS_ENV.split(",") if m.strip()}

def _use_vllm_model(model: str | None) -> bool:
    try:
        name = (model or "").strip()
        if not name:
            return False
        return name in VLLM_MODELS or name.lower().startswith("marven-")
    except Exception:
        return False

def _history_as_openai_messages(session_id: str) -> list:
    try:
        msgs = marven.get_history(session_id).messages
    except Exception:
        msgs = []
    out = []
    for m in msgs or []:
        try:
            role = getattr(m, "role", None) or getattr(m, "type", None)
            if isinstance(m, dict):
                role = m.get("role")
                content = m.get("content", "")
            else:
                content = getattr(m, "content", "")
            if role in ("human", "user"):
                role = "user"
            elif role in ("ai", "assistant"):
                role = "assistant"
            elif role == "system":
                role = "system"
            else:
                continue
            if content:
                out.append({"role": role, "content": content})
        except Exception:
            continue
    return out

def _vllm_complete_sync(user_input: str, session_id: str, model: str, self_aware: bool = False) -> tuple[bool, str]:
    """Call vLLM chat/completions via raw HTTP, no SDKs."""
    try:
        url = VLLM_BASE_URL.rstrip("/") + "/chat/completions"
        messages = []
        try:
            for role, text in getattr(marven, "system_messages", []) or []:
                if role == "system" and (text or "").strip():
                    messages.append({"role": "system", "content": text})
        except Exception:
            pass
        if self_aware:
            messages.append({"role": "system", "content": "Self-aware mode: reflect briefly where helpful."})
        messages.extend(_history_as_openai_messages(session_id))
        messages.append({"role": "user", "content": user_input})
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": 0.7,
        }).encode("utf-8")
        req = _urlreq.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with _urlreq.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return True, text.strip()
    except Exception:
        _report_exception("vLLM request failed")
        return False, "Model request failed."

def _vllm_complete_stream(user_input: str, session_id: str, model: str, message_id: str, self_aware: bool = False):
    """Stream chunks from vLLM via raw HTTP SSE (data: lines)."""
    try:
        url = VLLM_BASE_URL.rstrip("/") + "/chat/completions"
        messages = []
        try:
            for role, text in getattr(marven, "system_messages", []) or []:
                if role == "system" and (text or "").strip():
                    messages.append({"role": "system", "content": text})
        except Exception:
            pass
        if self_aware:
            messages.append({"role": "system", "content": "Self-aware mode: reflect briefly where helpful."})
        messages.extend(_history_as_openai_messages(session_id))
        messages.append({"role": "user", "content": user_input})
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "stream": True,
        }).encode("utf-8")
        req = _urlreq.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with _urlreq.urlopen(req, timeout=60) as resp:
            buf = ""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                try:
                    buf += chunk.decode("utf-8", errors="replace")
                except Exception:
                    continue
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        yield {"type": "done", "id": message_id}
                        return
                    try:
                        obj = json.loads(data)
                        chs = obj.get("choices") or []
                        if chs:
                            delta = (chs[0].get("delta") or {}).get("content")
                            if delta:
                                yield {"type": "text", "data": delta, "id": message_id}
                    except Exception:
                        continue
    except Exception:
        _report_exception("vLLM stream failed")
        yield {"type": "error", "error": "Model stream failed.", "id": message_id}
        yield {"type": "done", "id": message_id}


# /health route is defined after app initialization (see below)

app = Flask(__name__)
CORS(app)


def _report_exception(context: str) -> str:
    """Log server-side details and return an opaque correlation ID."""
    error_id = uuid.uuid4().hex[:12]
    app.logger.exception("%s [error_id=%s]", context, error_id)
    return error_id


def _json_failure(message: str, status: int, context: str):
    error_id = _report_exception(context)
    return jsonify({"error": message, "errorId": error_id}), status


def _safe_session_id(value: object) -> str:
    return validate_identifier(value, field="session ID")


def _history_path(value: object) -> Path:
    session_id = _safe_session_id(value)
    return resolve_path_within(HISTORY_DIR, f"{session_id}.json")

@app.route("/health", methods=["GET"])
def api_health():
    info = {"ok": False, "vllm": {}, "ollama": {}}
    # vLLM check
    vllm = {"base_url": VLLM_BASE_URL, "configured_models": sorted(list(VLLM_MODELS)), "reachable": False, "models": []}
    try:
        url = VLLM_BASE_URL.rstrip("/") + "/models"
        req = _urlreq.Request(url, method="GET")
        with _urlreq.urlopen(req, timeout=2) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
            ms = []
            for it in (data.get("data") or []):
                _id = it.get("id")
                if _id:
                    ms.append(_id)
            vllm.update({"reachable": True, "models": ms})
        except Exception:
            vllm.update({"reachable": True})
    except Exception:
        pass
    info["vllm"] = vllm
    # Ollama check
    oll = {"installed": False, "running": False, "models": []}
    try:
        ver = subprocess.run(["ollama", "--version"], capture_output=True, text=True)
        if ver.returncode == 0:
            oll["installed"] = True
        ls = subprocess.run(["ollama", "list"], capture_output=True, text=True)
        if ls.returncode == 0:
            oll["running"] = True
            names = []
            for line in (ls.stdout or "").splitlines():
                if not line.strip() or line.lower().startswith("name"):
                    continue
                parts = line.split()
                if parts:
                    names.append(parts[0])
            oll["models"] = names
    except Exception:
        pass
    info["ollama"] = oll
    info["ok"] = bool(vllm.get("reachable") or oll.get("running"))
    return jsonify(info)

# ---- Lightweight task log ----
TASKS = {}

def log_task(task_id: str, status: str, **meta):
    try:
        TASKS[task_id] = {"status": status, "meta": meta, "ts": time.time()}
        log_dir = Path("logs"); log_dir.mkdir(exist_ok=True)
        (log_dir / "marven_tasks.json").write_text(json.dumps(TASKS, indent=2), encoding="utf-8")
    except Exception:
        pass

# ---- Helpers for analyzing file inputs ----
import io
import base64
import zipfile
import xml.etree.ElementTree as ET
def _is_text_extension(name: str) -> bool:
    name = (name or "").lower()
    TEXT_EXTS = (
        ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".html", ".xml",
        ".c", ".h", ".cpp", ".hpp", ".rs", ".go", ".rb", ".java", ".kt", ".sh", ".csv",
    )
    return any(name.endswith(ext) for ext in TEXT_EXTS)


def _is_text_mime(ftype: str) -> bool:
    ftype = (ftype or "").lower()
    return (
        ftype.startswith("text/")
        or ftype in (
            "application/json",
            "application/xml",
            "application/xhtml+xml",
            "application/javascript",
            "application/x-yaml",
            "application/x-sh",
            "application/x-python",
            "application/rtf",
            "text/rtf",
            "application/vnd.oasis.opendocument.text",
        )
    )


def _decode_b64_textlike(name: str, ftype: str, b64: str, limit: int = 100000, csv_style: bool = False):
    try:
        raw = base64.b64decode(b64, validate=False)
        # Specialized parsers first
        lname = (name or "").lower()
        ltype = (ftype or "").lower()
        if lname.endswith(".docx") or "officedocument.wordprocessingml.document" in ltype:
            text = _extract_text_from_docx_bytes(raw, limit=limit)
            if text:
                return text
        if lname.endswith(".pptx") or "officedocument.presentationml.presentation" in ltype:
            text = _extract_text_from_pptx_bytes(raw, limit=limit)
            if text:
                return text
        if lname.endswith(".xlsx") or "officedocument.spreadsheetml.sheet" in ltype:
            text = _extract_text_from_xlsx_bytes(raw, limit=limit, csv_style=csv_style)
            if text:
                return text
        if lname.endswith(".pdf") or ltype == "application/pdf":
            text = _extract_text_from_pdf_bytes(raw, limit=limit)
            if text:
                return text
        if lname.endswith(".odt") or ltype == "application/vnd.oasis.opendocument.text":
            text = _extract_text_from_odt_bytes(raw, limit=limit)
            if text:
                return text
        if lname.endswith(".rtf") or ltype in ("application/rtf", "text/rtf"):
            text = _extract_text_from_rtf_bytes(raw, limit=limit)
            if text:
                return text

        # quick heuristic: treat as text if enough bytes are printable/whitespace
        printable = sum(1 for b in raw if b in (9, 10, 13) or 32 <= b <= 126 or b >= 160)
        ratio = printable / max(1, len(raw))
        textlike_hint = _is_text_mime(ftype) or _is_text_extension(name) or ratio >= 0.85
        if not textlike_hint:
            return None
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = raw.decode("latin-1", errors="replace")
        if len(text) > limit:
            text = text[:limit] + "\n...[truncated]..."
        return text
    except Exception:
        return None

def _extract_text_from_docx_bytes(data: bytes, limit: int = 100000) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            texts = []
            def read_xml(path):
                try:
                    with z.open(path) as f:
                        return f.read()
                except KeyError:
                    return None
            candidates = ["word/document.xml"]
            candidates += [p for p in z.namelist() if p.startswith("word/header") and p.endswith(".xml")]
            candidates += [p for p in z.namelist() if p.startswith("word/footer") and p.endswith(".xml")]
            candidates += [p for p in ("word/footnotes.xml", "word/endnotes.xml")]
            for path in candidates:
                raw = read_xml(path)
                if not raw:
                    continue
                try:
                    root = ET.fromstring(raw)
                except Exception:
                    continue
                # collect text runs
                for t in root.findall('.//w:t', ns):
                    if t.text:
                        texts.append(t.text)
                texts.append("\n\n")
            text = "".join(texts).strip()
            if len(text) > limit:
                text = text[:limit] + "\n...[truncated]..."
            return text
    except Exception:
        return ""

def _extract_text_from_pptx_bytes(data: bytes, limit: int = 100000) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            texts = []
            slide_paths = [p for p in z.namelist() if p.startswith("ppt/slides/slide") and p.endswith(".xml")]
            for sp in sorted(slide_paths):
                try:
                    raw = z.read(sp)
                    root = ET.fromstring(raw)
                    for t in root.findall('.//a:t', ns):
                        if t.text:
                            texts.append(t.text)
                    texts.append("\n\n")
                except Exception:
                    continue
            text = "".join(texts).strip()
            if len(text) > limit:
                text = text[:limit] + "\n...[truncated]..."
            return text
    except Exception:
        return ""

def _extract_text_from_xlsx_bytes(data: bytes, limit: int = 100000, csv_style: bool = False) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            # shared strings
            shared = []
            try:
                root = ET.fromstring(z.read('xl/sharedStrings.xml'))
                for si in root.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si'):
                    texts = [t.text or '' for t in si.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')]
                    shared.append(''.join(texts))
            except Exception:
                shared = []
            # workbook rels
            sheet_names = {}
            try:
                wb = ET.fromstring(z.read('xl/workbook.xml'))
                for s in wb.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet'):
                    name = s.attrib.get('name')
                    rid = s.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                    sheet_names[rid] = name
            except Exception:
                pass
            rels = {}
            try:
                wb_rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
                for r in wb_rels.findall('.//{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'):
                    rid = r.attrib.get('Id')
                    target = r.attrib.get('Target')
                    if rid and target:
                        rels[rid] = target
            except Exception:
                pass
            out = []
            import re as _re
            for rid, target in rels.items():
                if not target.startswith('worksheets/'):
                    continue
                path = 'xl/' + target
                try:
                    root = ET.fromstring(z.read(path))
                except Exception:
                    continue
                sheet_title = sheet_names.get(rid) or path.split('/')[-1]
                out.append(f"Sheet: {sheet_title}")
                rows = {}
                for c in root.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
                    r = c.attrib.get('r', '')
                    v = c.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
                    if v is None or v.text is None:
                        val = ''
                    else:
                        if c.attrib.get('t') == 's':
                            try:
                                idx = int(v.text)
                                val = shared[idx] if 0 <= idx < len(shared) else ''
                            except Exception:
                                val = ''
                        else:
                            val = v.text
                    m = _re.match(r'([A-Z]+)([0-9]+)', r)
                    if not m:
                        continue
                    col, row = m.group(1), int(m.group(2))
                    rows.setdefault(row, {})[col] = val
                if not rows:
                    continue
                all_cols = sorted({col for cols in rows.values() for col in cols.keys()})
                header_row_index = 1 if 1 in rows else min(rows.keys())
                header_vals = [rows.get(header_row_index, {}).get(c, '') for c in all_cols]
                if csv_style:
                    buf = io.StringIO()
                    w = csv.writer(buf)
                    if any(hv for hv in header_vals):
                        w.writerow(header_vals)
                    else:
                        w.writerow(all_cols)
                    for i in sorted(rows.keys()):
                        if i == header_row_index:
                            continue
                        cols = rows[i]
                        w.writerow([cols.get(k, '') for k in all_cols])
                    out.append(buf.getvalue().strip())
                else:
                    out.append('Headers:\t' + '\t'.join(header_vals) if any(hv for hv in header_vals) else 'Columns:\t' + '\t'.join(all_cols))
                    for i in sorted(rows.keys()):
                        if i == header_row_index:
                            continue
                        cols = rows[i]
                        line = [cols.get(k, '') for k in all_cols]
                        out.append('\t'.join(line))
                    out.append('')
            text = '\n'.join(out).strip()
            if len(text) > limit:
                text = text[:limit] + "\n...[truncated]..."
            return text
    except Exception:
        return ""

def _extract_text_from_pdf_bytes(data: bytes, limit: int = 200000) -> str:
    try:
        try:
            import PyPDF2 as pypdf
        except Exception:
            import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        parts = []
        for page in getattr(reader, 'pages', []):
            try:
                txt = page.extract_text() or ''
            except Exception:
                txt = ''
            if txt:
                parts.append(txt.strip())
        text = '\n\n'.join(parts).strip()
        if len(text) > limit:
            text = text[:limit] + "\n...[truncated]..."
        return text
    except Exception:
        return ""


def _extract_text_from_odt_bytes(data: bytes, limit: int = 100000) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            raw = z.read('content.xml')
            root = ET.fromstring(raw)
            ns = {
                'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
            }
            texts = []
            for tag in ('text:h', 'text:p'):
                for el in root.findall('.//' + tag, ns):
                    piece = ''.join(el.itertext())
                    if piece:
                        texts.append(piece)
                        texts.append('\n\n')
            content = ''.join(texts).strip()
            if len(content) > limit:
                content = content[:limit] + "\n...[truncated]..."
            return content
    except Exception:
        return ''


def _extract_text_from_rtf_bytes(data: bytes, limit: int = 100000) -> str:
    try:
        import re as _re
        s = data.decode('latin-1', errors='ignore')
        # Convert hex escapes \'hh to actual characters (cp1252)
        def _hex_sub(m):
            try:
                return bytes([int(m.group(1), 16)]).decode('cp1252', errors='replace')
            except Exception:
                return ''
        s = _re.sub(r"\\'([0-9a-fA-F]{2})", _hex_sub, s)
        # Newlines for paragraph and line breaks
        s = _re.sub(r"\\par[d]?", "\n", s)
        s = _re.sub(r"\\line", "\n", s)
        # Remove other control words and their optional numeric args
        s = _re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", s)
        # Unescape special characters and remove braces
        s = s.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
        s = s.replace("{", "").replace("}", "")
        # Normalize whitespace
        s = _re.sub(r"\r\n?|\f", "\n", s)
        s = _re.sub(r"\n{3,}", "\n\n", s)
        s = _re.sub(r"[ \t]{2,}", " ", s)
        s = s.strip()
        if len(s) > limit:
            s = s[:limit] + "\n...[truncated]..."
        return s
    except Exception:
        return ''

@app.route("/api/respond", methods=["POST"])
def respond():
    data = request.get_json() or {}
    user_input = data.get("input", "")
    model = data.get("model")
    session_id = data.get("sessionId", "akeem")
    auto_apply = bool(data.get("autoApply")); self_aware = bool(data.get("selfAware"))
    message_id = data.get("messageId") or str(uuid.uuid4())
    print("USER INPUT RECEIVED:", {"input": user_input, "model": model, "session": session_id})

    # === Phase3 Hook Start ===
    auto_msg = None
    try:
        hook = getattr(marven, "autoreview_if_needed", None)
        if callable(hook):
            auto_msg = hook()
    except Exception as e:
        print("AUTO-REVIEW hook error:", e)
    if auto_msg:
        print("AUTO-REVIEW:", auto_msg)
    # === Phase3 Hook End ===

    tid = str(uuid.uuid4())
    log_task(tid, "running", type="respond", session=session_id)

    # If using vLLM model, bypass Ollama path and call OpenAI-compatible server
    if _use_vllm_model(model):
        ok, out = _vllm_complete_sync(user_input, session_id=session_id, model=model, self_aware=self_aware)
        if not ok:
            log_task(tid, "done", output_len=len(out or ""))
            print("REPLY (vLLM error):", out)
            return jsonify({"output": out, "applied": []}), 502
        applied = []
        if auto_apply:
            try:
                for path, content in _extract_fenced_updates(out):
                    info = _write_file_safe(path, content)
                    applied.append(info)
            except Exception as e:
                print("AUTO-APPLY ERROR:", e)
        log_task(tid, "done", output_len=len(out or ""))
        print("REPLY (vLLM):", out)
        return jsonify({"output": out, "applied": applied})
    # Preflight Ollama availability if this path will use LLM (not web/docx commands)
    try:
        lower = (user_input or "").strip().lower()
        non_llm = False
        try:
            if lower.startswith("write:docx"):
                non_llm = True
            elif marven._parse_web_search(user_input) or marven._parse_web_analyze(user_input):
                non_llm = True
            else:
                cmd = marven._parse_web_command(user_input)
                if cmd or (getattr(marven, "_parse_web_compare", None) and marven._parse_web_compare(user_input)):
                    non_llm = True
        except Exception:
            pass
        # Skip Ollama preflight if using vLLM model
        if not non_llm and not _use_vllm_model(model):
            model_to_check = (model or ("llama3:8b" if lower.startswith("code:") else "tinyllama"))
            ok, msg = ensure_ollama_model(model_to_check)
            if not ok:
                out = _offline_fallback_reply(user_input, model_to_check, msg)
                log_task(tid, "done", output_len=len(out))
                print("REPLY:", out)
                return jsonify({"output": out, "applied": []})
    except Exception:
        pass
    reply = marven_response(user_input, session_id=session_id, model=model, self_aware=self_aware)
    log_task(tid, "done", output_len=len(reply or ""))
    applied = []
    if auto_apply:
        try:
            applied = []
            for path, content in _extract_fenced_updates(reply):
                info = _write_file_safe(path, content)
                applied.append(info)
        except Exception as e:
            print("AUTO-APPLY ERROR:", e)
    print("REPLY:", reply)
    return jsonify({"output": reply, "applied": applied})


@app.route("/api/respond_stream", methods=["POST"])
def respond_stream():
    data = request.get_json() or {}
    user_input = data.get("input", "")
    model = data.get("model")
    session_id = data.get("sessionId", "akeem")
    auto_apply = bool(data.get("autoApply")); self_aware = bool(data.get("selfAware"))
    message_id = data.get("messageId") or str(uuid.uuid4())

    def generate():
        try:
            def _ev(payload):
                try:
                    d = dict(payload)
                except Exception:
                    d = {"type": "text", "data": str(payload)}
                d.setdefault("id", message_id)
                return json.dumps(d) + "\n"
            # Emit initial status so clients can show activity right away
            try:
                yield _ev({"type": "status", "data": "thinking"})
            except Exception:
                pass
            # Handle write:docx path\n\n<content>
            try:
                s = (user_input or "")
                if s.strip().lower().startswith("write:docx"):
                    rest = s[len("write:docx"):].lstrip()
                    parts = rest.splitlines()
                    if not parts:
                        yield _ev({"type": "text", "data": "Usage: write:docx relative/path.docx\\n\\n<content>"})
                        yield _ev({"type": "done"})
                        return
                    rel = parts[0].strip()
                    content = rest[len(rel):].lstrip("\n")
                    if not rel.lower().endswith(".docx"):
                        rel = rel + ".docx"
                    try:
                        data_bytes = marven._create_docx_bytes(content)
                        p = _safe_path(rel)
                        p.parent.mkdir(parents=True, exist_ok=True)
                        with open(p, 'wb') as f:
                            f.write(data_bytes)
                        yield _ev({"type": "text", "data": f"Wrote DOCX ({len(data_bytes)} bytes) to {rel}"})
                    except SecurityValidationError:
                        yield _ev({"type": "error", "error": "DOCX path is outside the project directory."})
                    except Exception:
                        _report_exception("DOCX write failed")
                        yield _ev({"type": "error", "error": "DOCX write failed."})
                    yield _ev({"type": "done"})
                    return
            except Exception:
                pass
            # Handle web commands (non-LLM) for immediate output
            try:
                handled, out = marven.handle_web_command(user_input)
            except Exception:
                handled, out = (False, "")
            if handled:
                if out:
                    yield _ev({"type": "text", "data": out})
                yield _ev({"type": "done"})
                return
            # vLLM streaming path (bypass Ollama flow)
            if _use_vllm_model(model):
                for evt in _vllm_complete_stream(user_input, session_id=session_id, model=(model or "marven-8b"), message_id=message_id, self_aware=self_aware):
                    try:
                        yield _ev(evt)
                    except Exception:
                        continue
                return
            # Auto research intent: detect feasibility/market research prompts and include sources + synthesis
            try:
                rq = marven._detect_research_query(user_input)
            except Exception:
                rq = None
            if rq:
                try:
                    yield _ev({"type": "status", "data": f"searching: {rq}"})
                except Exception:
                    pass
                results = marven._web_search(rq, limit=5)
                if isinstance(results, dict) and results.get("error"):
                    yield _ev({"type": "text", "data": results.get("error")})
                    yield _ev({"type": "done"})
                    return
                # Preflight model before we fetch/stream synthesis
                try:
                    lower_in = (user_input or "").strip().lower()
                    model_to_check = (model or ("llama3:8b" if lower_in.startswith("code:") else "tinyllama"))
                    ok, msg = ensure_ollama_model(model_to_check)
                    if not ok:
                        fb = _offline_fallback_reply(user_input, model_to_check, msg)
                        for line in fb.split("\n\n"):
                            yield _ev({"type": "text", "data": line})
                        yield _ev({"type": "done"})
                        return
                except Exception:
                    pass
                links = ["Top sources:"]
                pairs = []
                for i, it in enumerate(results[:5], 1):
                    title = it.get("title") or it.get("url")
                    url = it.get("url")
                    if url:
                        links.append(f"{i}. [{title}]({url})")
                yield _ev({"type": "text", "data": "\n".join(links) + "\n\n"})
                for it in results[:3]:
                    url = it.get("url")
                    if not url:
                        continue
                    raw = marven._fetch_url(url, method="GET")
                    if isinstance(raw, str) and (raw.startswith("Web fetch error:") or raw.startswith("Fetched binary content")):
                        continue
                    text = marven._extract_readable_text(raw if isinstance(raw, str) else str(raw))
                    if text:
                        pairs.append((url, text))
                if not pairs:
                    yield _ev({"type": "done"})
                    return
                comp_prompt = marven._build_web_compare_prompt(pairs)
                current_llm = marven.get_llm(model) if model else marven.choose_llm(comp_prompt)
                conv = marven.RunnableWithMessageHistory(
                    marven.prompt | current_llm,
                    get_session_history=marven.get_history,
                    input_messages_key="input",
                    history_messages_key="history",
                )
                acc = ""
                for rawc in conv.stream({"input": comp_prompt}, config={"configurable": {"session_id": session_id}}):
                    text = getattr(rawc, "content", None)
                    if not isinstance(text, str):
                        text = str(rawc)
                    delta = text[len(acc):] if text.startswith(acc) else text
                    if delta.strip():
                        yield _ev({"type": "text", "data": delta})
                    if text.startswith(acc) and len(text) >= len(acc):
                        acc = text
                yield _ev({"type": "done"})
                return
            # Handle web:search <query> (non-LLM)
            try:
                q = marven._parse_web_search(user_input)
            except Exception:
                q = None
            if q:
                tid = str(uuid.uuid4())
                try:
                    log_task(tid, "running", type="web_search", query=q)
                    yield _ev({"type": "status", "data": f"searching: {q}"})
                except Exception:
                    pass
                res = marven._web_search(q, limit=5)
                text = marven._format_search_results(res)
                if text:
                    yield _ev({"type": "text", "data": text})
                try:
                    log_task(tid, "done", results=(len(res) if isinstance(res, list) else 0))
                    yield _ev({"type": "status", "data": "streaming"})
                except Exception:
                    pass
                yield _ev({"type": "done"})
                return
            # Handle web:analyze <url> by fetching and then streaming analysis via LLM
            try:
                analyze_url = marven._parse_web_analyze(user_input)
            except Exception:
                analyze_url = None
            if analyze_url:
                if not marven._is_http_url(analyze_url):
                    yield _ev({"type": "text", "data": "Invalid URL. Use http(s)://..."})
                    yield _ev({"type": "done"})
                    return
                tid = str(uuid.uuid4())
                try:
                    yield _ev({"type": "status", "data": f"fetching: {analyze_url}"})
                    log_task(tid, "running", type="web_analyze", url=analyze_url)
                except Exception:
                    pass
                raw = marven._fetch_url(analyze_url, method="GET")
                if isinstance(raw, str) and raw.startswith("Web fetch error:"):
                    yield _ev({"type": "text", "data": raw})
                    yield _ev({"type": "done"})
                    return
                if isinstance(raw, str) and raw.startswith("Fetched binary content"):
                    yield _ev({"type": "text", "data": raw + "\nCannot analyze non-text/binary content."})
                    yield _ev({"type": "done"})
                    return
                try:
                    yield _ev({"type": "status", "data": "analyzing"})
                except Exception:
                    pass
                page_text = marven._extract_readable_text(raw if isinstance(raw, str) else str(raw))
                analysis_prompt = marven._build_web_analysis_prompt(analyze_url, page_text)
                # Switch user_input to analysis prompt for streaming
                user_prompt_for_stream = analysis_prompt
                current_llm = marven.get_llm(model) if model else marven.choose_llm(user_prompt_for_stream)
                conv = marven.RunnableWithMessageHistory(
                    marven.prompt | current_llm,
                    get_session_history=marven.get_history,
                    input_messages_key="input",
                    history_messages_key="history",
                )

                def extract_text(chunk):
                    try:
                        if isinstance(chunk, str):
                            return chunk
                        text = getattr(chunk, "content", None)
                        if isinstance(text, str):
                            return text
                        if isinstance(chunk, dict):
                            if isinstance(chunk.get("content"), str):
                                return chunk["content"]
                            if isinstance(chunk.get("data"), str):
                                return chunk["data"]
                        return str(chunk)
                    except Exception:
                        return ""

                acc = ""
                full_text = ""
                for rawc in conv.stream({"input": user_prompt_for_stream}, config={"configurable": {"session_id": session_id}}):
                    text = extract_text(rawc)
                    if not text:
                        continue
                    delta = text[len(acc):] if text.startswith(acc) else text
                    if delta.startswith("AIMessageChunk(") and "content='" in delta:
                        try:
                            delta = delta.split("content='", 1)[1]
                            delta = delta.rsplit("'", 1)[0]
                        except Exception:
                            pass
                    if delta.strip():
                        yield _ev({"type": "text", "data": delta})
                        full_text += delta
                    if text.startswith(acc) and len(text) >= len(acc):
                        acc = text
                yield _ev({"type": "done"})
                try:
                    log_task(tid, "done", analyzed_chars=len(page_text or ""))
                except Exception:
                    pass
                if auto_apply and full_text:
                    try:
                        files = []
                        for path, content in _extract_fenced_updates(full_text):
                            info = _write_file_safe(path, content)
                            files.append(info)
                        if files:
                            yield _ev({"type": "applied", "files": files})
                    except Exception:
                        _report_exception("Automatic file update failed")
                        yield _ev({"type": "applied_error", "error": "Automatic file update failed."})
                return
            # Handle web:compare <url1> <url2> ... by fetching all and streaming a comparative analysis
            try:
                urls = marven._parse_web_compare(user_input)
            except Exception:
                urls = None
            if urls:
                try:
                    yield _ev({"type": "status", "data": "comparing"})
                except Exception:
                    pass
                pairs = []
                for u in urls[:5]:
                    raw = marven._fetch_url(u, method="GET")
                    if isinstance(raw, str) and (raw.startswith("Web fetch error:") or raw.startswith("Fetched binary content")):
                        continue
                    text = marven._extract_readable_text(raw if isinstance(raw, str) else str(raw))
                    if text:
                        pairs.append((u, text))
                if len(pairs) < 2:
                    yield _ev({"type": "text", "data": "Need at least two readable sources to compare."})
                    yield _ev({"type": "done"})
                    return
                comp_prompt = marven._build_web_compare_prompt(pairs)
                current_llm = marven.get_llm(model) if model else marven.choose_llm(comp_prompt)
                conv = marven.RunnableWithMessageHistory(
                    marven.prompt | current_llm,
                    get_session_history=marven.get_history,
                    input_messages_key="input",
                    history_messages_key="history",
                )
                acc = ""
                for rawc in conv.stream({"input": comp_prompt}, config={"configurable": {"session_id": session_id}}):
                    text = getattr(rawc, "content", None)
                    if not isinstance(text, str):
                        text = str(rawc)
                    delta = text[len(acc):] if text.startswith(acc) else text
                    if delta.strip():
                        yield _ev({"type": "text", "data": delta})
                    if text.startswith(acc) and len(text) >= len(acc):
                        acc = text
                yield _ev({"type": "done"})
                return
            # Build a streaming conversation pipeline
            # Preflight model availability
            try:
                lower_in = (user_input or "").strip().lower()
                model_to_check = (model or ("llama3:8b" if lower_in.startswith("code:") else "tinyllama"))
                ok, msg = ensure_ollama_model(model_to_check)
                if not ok:
                    fb = _offline_fallback_reply(user_input, model_to_check, msg)
                    for line in fb.split("\n\n"):
                        yield _ev({"type": "text", "data": line})
                    yield _ev({"type": "done"})
                    return
            except Exception:
                pass
            current_llm = marven.get_llm(model) if model else marven.choose_llm(user_input)
            conv = marven.RunnableWithMessageHistory(
                marven.prompt | current_llm,
                get_session_history=marven.get_history,
                input_messages_key="input",
                history_messages_key="history",
            )

            def extract_text(chunk):
                try:
                    if isinstance(chunk, str):
                        return chunk
                    text = getattr(chunk, "content", None)
                    if isinstance(text, str):
                        return text
                    if isinstance(chunk, dict):
                        if isinstance(chunk.get("content"), str):
                            return chunk["content"]
                        if isinstance(chunk.get("data"), str):
                            return chunk["data"]
                    return str(chunk)
                except Exception:
                    return ""

            acc = ""
            full_text = ""
            try:
                yield _ev({"type": "status", "data": "generating"})
            except Exception:
                pass
            for raw in conv.stream({"input": user_input}, config={"configurable": {"session_id": session_id}}):
                text = extract_text(raw)
                if not text:
                    continue
                delta = text[len(acc):] if text.startswith(acc) else text
                if delta.startswith("AIMessageChunk(") and "content='" in delta:
                    try:
                        delta = delta.split("content='", 1)[1]
                        delta = delta.rsplit("'", 1)[0]
                    except Exception:
                        pass
                if delta.strip():
                    yield _ev({"type": "text", "data": delta})
                    full_text += delta
                if text.startswith(acc) and len(text) >= len(acc):
                    acc = text
            yield _ev({"type": "done"})
            try:
                if self_aware and full_text and marven.introspection_should_reflect(session_id):
                    marven.self_aware_reflect(user_input, full_text, session_id=session_id, do_rre=False)
            except Exception:
                pass
            if auto_apply and full_text:
                try:
                    files = []
                    for path, content in _extract_fenced_updates(full_text):
                        info = _write_file_safe(path, content)
                        files.append(info)
                    if files:
                        yield _ev({"type": "applied", "files": files})
                except Exception:
                    _report_exception("Automatic file update failed")
                    yield _ev({"type": "applied_error", "error": "Automatic file update failed."})
        except Exception:
            _report_exception("Response stream failed")
            yield _ev({"type": "error", "error": "Response stream failed."})

    return Response(stream_with_context(generate()), mimetype="text/plain")

@app.route("/")
def index():
    return "Marven Flask API is running."


# ---- Endpoints ----
@app.post("/api/chat")
def chat():
    data = request.get_json() or {}
    text = data.get("input", "")
    model = data.get("model")
    session_id = data.get("sessionId", "akeem")
    reply = marven_response(text, session_id=session_id, model=model)
    return jsonify({"output": reply})

@app.get("/api/history")
def history():
    """Return recent messages (limit param optional)."""
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
        path = _history_path(request.args.get("sessionId", "akeem"))
        if not path.exists():
            return jsonify([])
        with path.open(encoding="utf-8") as history_file:
            entries = json.load(history_file)[-limit:]
    except (SecurityValidationError, ValueError):
        return jsonify({"error": "Invalid history request."}), 400
    except Exception:
        return _json_failure("Unable to load history.", 500, "History read failed")
    flat = [
        {
            "content": e.get("data", {}).get("content", ""),
            "role": "ai" if e.get("type") == "ai" else "human",
        }
        for e in entries
    ]
    return jsonify(flat)

@app.get("/api/memories")
def memories():
    try:
        path = _history_path(request.args.get("sessionId", "akeem"))
        if not path.exists():
            return jsonify([])
        with path.open(encoding="utf-8") as history_file:
            return jsonify(json.load(history_file))
    except SecurityValidationError:
        return jsonify({"error": "Invalid session ID."}), 400
    except Exception:
        return _json_failure("Unable to load memories.", 500, "Memory history read failed")

@app.get("/api/brain/meta")
def brain_meta():
    base_dir = Path(__file__).resolve().parent
    bdir = base_dir / "assets" / "brain"
    available = []
    if (bdir / "marven_galaxy_brain_full_offline_tour_v2.mp3").exists():
        available.append("full")
    if (bdir / "marven_galaxy_brain_full_compact.mp3").exists():
        available.append("compact")
    online = bool(os.environ.get("MARVEN_BRAIN_BASE_URL"))
    return jsonify({"available": available, "online": online})

@app.get("/api/brain/tour")
def brain_tour():
    base_dir = Path(__file__).resolve().parent
    bdir = base_dir / "assets" / "brain"
    variant = (request.args.get("variant", "full") or "full").lower()
    fname = "marven_galaxy_brain_full_offline_tour_v2.mp3" if variant == "full" else "marven_galaxy_brain_full_compact.mp3"
    p = bdir / fname
    if p.exists():
        return send_file(str(p), mimetype="audio/mpeg")
    url = os.environ.get("MARVEN_BRAIN_BASE_URL")
    if url:
        return jsonify({"redirect": f"{url.rstrip('/')}/{fname}"})
    return jsonify({"error": "Brain tour asset not found (offline + no online URL)."}), 404

@app.get("/api/tasks")
def api_tasks():
    try:
        return jsonify(TASKS)
    except Exception:
        return _json_failure("Unable to load tasks.", 500, "Task listing failed")

# --- Additional APIs: Vision + Filesystem ---

def ensure_ollama_model(model: str):
    """Ensure the Ollama model is available; attempt pull if missing.
    Returns (ok: bool, message: str).
    """
    try:
        show = subprocess.run(["ollama", "show", model], capture_output=True, text=True)
        if show.returncode == 0:
            return True, "available"
    except Exception:
        pass
    try:
        pull = subprocess.run(["ollama", "pull", model], capture_output=True, text=True, timeout=600)
        if pull.returncode == 0:
            return True, pull.stdout[-4000:]
        return False, pull.stderr or pull.stdout
    except Exception:
        _report_exception("Ollama model check failed")
        return False, "Model backend unavailable."


def _offline_fallback_reply(user_input: str, model_name: str, detail: str) -> str:
    ui = (user_input or "").strip()
    header = (
        "Model backend is currently unavailable, so I can’t generate a full answer.\n"
        f"Requested model: {model_name}\n"
    )
    tips = (
        "Do now:\n"
        "- Start Ollama: `ollama serve`\n"
        f"- Pull model: `ollama pull {model_name}`\n\n"
        "Available without the model:\n"
        "- `web:get <url>` fetches a page\n"
        "- `web:search <query>` runs a quick search\n"
        "- `write:docx path.docx` writes a docx from content\n"
    )
    hint = (f"Input: {ui}\n" if ui else "")
    return "\n".join([header, tips, hint]).strip()


@app.post("/api/ollama/pull")
def api_ollama_pull():
    data = request.get_json() or {}
    model = data.get("model", "llava")
    ok, msg = ensure_ollama_model(model)
    if ok:
        return jsonify({"status": "ok", "model": model, "message": msg})
    return jsonify({"status": "error", "model": model, "error": "Model pull failed."}), 500


@app.post("/api/vision")
def vision_describe():
    data = request.get_json() or {}
    prompt = data.get("input", "Describe the image in detail")
    images_b64 = data.get("images", [])
    model = data.get("model", "llava")
    session_id = data.get("sessionId", "akeem")
    _ok, _msg = ensure_ollama_model(model)
    try:
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage
        llm = ChatOllama(model=model, temperature=0.2)
        blocks = [{"type": "text", "text": prompt}]
        for b64 in images_b64:
            if "," in b64 and b64.strip().startswith("data:"):
                b64 = b64.split(",", 1)[1]
            blocks.append({"type": "image_url", "image_url": f"data:image/png;base64,{b64}"})
        msg = HumanMessage(content=blocks)
        resp = llm.invoke([msg])
        text = getattr(resp, "content", None) or str(resp)
        _ = marven.marven_response(f"Image analysis requested: {prompt}", session_id=session_id)
        return jsonify({"output": text})
    except Exception:
        return _json_failure("Vision model is unavailable.", 503, "Vision request failed")


def _safe_path(rel_path: str) -> Path:
    return resolve_path_within(BASE_DIR, rel_path)


def _write_file_safe(rel: str, content: str):
    p = _safe_path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": rel, "bytes": len(content.encode("utf-8"))}


@app.get("/api/fs/read")
def fs_read():
    rel = request.args.get("path")
    if not rel:
        return jsonify({"error": "Missing path"}), 400
    try:
        p = _safe_path(rel)
        if not p.exists() or not p.is_file():
            return jsonify({"error": "Not found"}), 404
        return jsonify({"path": rel, "content": p.read_text(encoding="utf-8", errors="replace")})
    except SecurityValidationError:
        return jsonify({"error": "Path is outside the project directory."}), 400
    except Exception:
        return _json_failure("Unable to read file.", 500, "Filesystem read failed")


@app.post("/api/fs/write")
def fs_write():
    data = request.get_json() or {}
    rel = data.get("path")
    content = data.get("content", "")
    if not rel:
        return jsonify({"error": "Missing path"}), 400
    try:
        p = _safe_path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return jsonify({"status": "ok", "path": rel, "bytes": len(content.encode("utf-8"))})
    except SecurityValidationError:
        return jsonify({"error": "Path is outside the project directory."}), 400
    except Exception:
        return _json_failure("Unable to write file.", 500, "Filesystem write failed")


@app.get("/api/fs/list")
def fs_list():
    rel = request.args.get("path", ".")
    try:
        p = _safe_path(rel)
        if not p.exists() or not p.is_dir():
            return jsonify({"error": "Not a directory"}), 400
        entries = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                size = child.stat().st_size if child.is_file() else 0
            except Exception:
                size = 0
            entries.append({
                "name": child.name,
                "isDir": child.is_dir(),
                "size": size,
                "path": str(child.relative_to(BASE_DIR))
            })
        return jsonify({"path": str(p.relative_to(BASE_DIR)), "entries": entries})
    except SecurityValidationError:
        return jsonify({"error": "Path is outside the project directory."}), 400
    except Exception:
        return _json_failure("Unable to list directory.", 500, "Filesystem listing failed")


@app.post("/api/vision_stream")
def vision_stream():
    data = request.get_json() or {}
    prompt = data.get("input", "Describe the image in detail")
    images_b64 = data.get("images", [])
    model = data.get("model", "llava")
    session_id = data.get("sessionId", "akeem")
    message_id = data.get("messageId") or str(uuid.uuid4())
    _ok, _msg = ensure_ollama_model(model)

    def generate():
        try:
            def _ev(payload):
                try:
                    d = dict(payload)
                except Exception:
                    d = {"type": "text", "data": str(payload)}
                d.setdefault("id", message_id)
                return json.dumps(d) + "\n"
            from langchain_ollama import ChatOllama
            from langchain_core.messages import HumanMessage
            llm = ChatOllama(model=model, temperature=0.2)
            blocks = [{"type": "text", "text": prompt}]
            for b64 in images_b64:
                if "," in b64 and b64.strip().startswith("data:"):
                    b64 = b64.split(",", 1)[1]
                blocks.append({"type": "image_url", "image_url": f"data:image/png;base64,{b64}"})
            msg = HumanMessage(content=blocks)

            acc = ""
            for raw in llm.stream([msg]):
                text = getattr(raw, "content", None)
                if not isinstance(text, str):
                    text = str(raw)
                delta = text[len(acc):] if text.startswith(acc) else text
                if delta.strip():
                    yield _ev({"type": "text", "data": delta})
                if text.startswith(acc) and len(text) >= len(acc):
                    acc = text
            yield _ev({"type": "done"})
        except Exception:
            _report_exception("Vision stream failed")
            yield _ev({"type": "error", "error": "Vision stream failed."})

    return Response(stream_with_context(generate()), mimetype="text/plain")


# --- Local capability + self-update bridge (scaffold) ---
def _local_caps_and_updater():
    if marven_local is None:
        raise RuntimeError("Local capability scaffold not available")
    root = marven_local.ROOT
    caps = marven_local.CapabilityManager(root, actor="api")
    updater = marven_local.SelfUpdater(root, caps)
    return caps.policy, caps, updater


def _proposal_path(name: object) -> Path:
    if not isinstance(name, str) or not name.endswith(".patch") or Path(name).name != name:
        raise SecurityValidationError("Invalid proposal name")
    return resolve_path_within(marven_local.PROPOSALS_DIR, name)  # type: ignore[union-attr]


@app.get("/api/local/capabilities")
def local_capabilities():
    try:
        if marven_local is None:
            return jsonify({})
        policy, _, _ = _local_caps_and_updater()
        return jsonify(policy.data)
    except Exception:
        return _json_failure("Unable to load capabilities.", 500, "Capability listing failed")


@app.route("/api/local/fs/list", methods=["GET", "POST"])
def local_fs_list():
    try:
        _, caps, _ = _local_caps_and_updater()
        if request.method == "GET":
            path = request.args.get("path", ".")
        else:
            path = (request.get_json() or {}).get("path", ".")
        return jsonify({"path": path, "entries": caps.fs_list(path)})
    except marven_local.CapabilityError:  # type: ignore[attr-defined]
        return jsonify({"error": "Operation not permitted."}), 403
    except Exception:
        return _json_failure("Unable to list directory.", 500, "Local filesystem listing failed")


@app.route("/api/local/fs/read", methods=["GET", "POST"])
def local_fs_read():
    try:
        _, caps, _ = _local_caps_and_updater()
        if request.method == "GET":
            path = request.args.get("path")
        else:
            path = (request.get_json() or {}).get("path")
        if not path:
            return jsonify({"error": "Missing path", "usage": "/api/local/fs/read?path=relative/or/absolute/path"}), 400
        return jsonify({"path": path, "content": caps.fs_read(path)})
    except marven_local.CapabilityError:  # type: ignore[attr-defined]
        return jsonify({"error": "Operation not permitted."}), 403
    except Exception:
        return _json_failure("Unable to read file.", 500, "Local filesystem read failed")


@app.route("/api/local/fs/write", methods=["GET", "POST"])
def local_fs_write():
    try:
        _, caps, _ = _local_caps_and_updater()
        if request.method == "GET":
            path = request.args.get("path")
            content = request.args.get("content", "")
        else:
            data = request.get_json() or {}
            path = data.get("path")
            content = data.get("content", "")
        if not path:
            return jsonify({"error": "Missing path"}), 400
        out = caps.fs_write(path, content)
        return jsonify({"status": "ok", "path": out, "bytes": len(content.encode("utf-8"))})
    except marven_local.CapabilityError:  # type: ignore[attr-defined]
        return jsonify({"error": "Operation not permitted."}), 403
    except Exception:
        return _json_failure("Unable to write file.", 500, "Local filesystem write failed")


@app.post("/api/local/self_update/propose")
def local_self_update_propose():
    try:
        _, caps, updater = _local_caps_and_updater()
        data = request.get_json() or {}
        target = data.get("target")
        search = data.get("search")
        replace = data.get("replace")
        description = data.get("description") or "Marven proposed update"
        if not target or search is None or replace is None:
            return jsonify({"error": "Missing target/search/replace"}), 400
        safe_target = resolve_path_within(BASE_DIR, target)
        path = updater.propose_edit_in_file(safe_target, search, replace, description)
        approval = Path(str(path)).with_suffix(".APPROVE.json")
        return jsonify({"proposal": str(path), "approval": str(approval)})
    except (marven_local.CapabilityError, SecurityValidationError):  # type: ignore[attr-defined]
        return jsonify({"error": "Operation not permitted."}), 403
    except Exception:
        return _json_failure("Unable to create update proposal.", 500, "Update proposal failed")


@app.post("/api/local/self_update/apply")
def local_self_update_apply():
    try:
        _, caps, updater = _local_caps_and_updater()
        changed = updater.apply_approved()
        return jsonify({"applied": bool(changed)})
    except marven_local.CapabilityError:  # type: ignore[attr-defined]
        return jsonify({"error": "Operation not permitted."}), 403
    except Exception:
        return _json_failure("Unable to apply updates.", 500, "Update application failed")


@app.get("/api/local/self_update/proposals")
def local_self_update_proposals():
    try:
        if marven_local is None:
            return jsonify([])
        items = []
        for patch in marven_local.PROPOSALS_DIR.glob("*.patch"):
            raw = patch.read_text(encoding="utf-8", errors="replace")
            manifest_json = None
            # New format: full JSON
            try:
                manifest_json = json.loads(raw)
            except Exception:
                # Legacy fallback: attempt to locate JSON chunk
                if "\n\n" in raw:
                    header, rest = raw.split("\n\n", 1)
                    try:
                        man_line = header.splitlines()[-1]
                        manifest_json = json.loads(man_line) if man_line.strip().startswith("{") else json.loads(rest.splitlines()[0])
                    except Exception:
                        manifest_json = None
            approval = patch.with_suffix(".APPROVE.json")
            approved = False
            if approval.exists():
                try:
                    j = json.loads(approval.read_text(encoding="utf-8"))
                    approved = bool(j.get("approved"))
                except Exception:
                    approved = False
            items.append({
                "name": patch.name,
                "approval": approval.name,
                "approved": approved,
                "manifest": manifest_json,
            })
        return jsonify(items)
    except Exception:
        return _json_failure("Unable to list proposals.", 500, "Proposal listing failed")


@app.get("/api/local/self_update/proposal")
def local_self_update_proposal():
    try:
        if marven_local is None:
            return jsonify({"error": "Local scaffold not available"}), 404
        name = request.args.get("name")
        if not name:
            return jsonify({"error": "Missing name"}), 400
        patch = _proposal_path(name)
        if not patch.exists():
            return jsonify({"error": "Not found"}), 404
        raw = patch.read_text(encoding="utf-8", errors="replace")
        approval = patch.with_suffix(".APPROVE.json")
        appr = None
        if approval.exists():
            try:
                appr = json.loads(approval.read_text(encoding="utf-8"))
            except Exception:
                appr = None
        return jsonify({"patch": name, "content": raw, "approval": approval.name, "approval_content": appr})
    except SecurityValidationError:
        return jsonify({"error": "Invalid proposal name."}), 400
    except Exception:
        return _json_failure("Unable to load proposal.", 500, "Proposal read failed")


@app.post("/api/local/self_update/approve")
def local_self_update_approve():
    try:
        if marven_local is None:
            return jsonify({"status": "noop", "message": "Local scaffold not available"})
        data = request.get_json() or {}
        name = data.get("proposal")
        approved = bool(data.get("approved", True))
        if not name:
            return jsonify({"error": "Missing proposal"}), 400
        patch = _proposal_path(name)
        if not patch.exists():
            return jsonify({"error": "Proposal not found"}), 404
        approval = patch.with_suffix(".APPROVE.json")
        if not approval.exists():
            return jsonify({"error": "Approval stub not found"}), 404
        j = json.loads(approval.read_text(encoding="utf-8"))
        j["approved"] = approved
        approval.write_text(json.dumps(j, indent=2), encoding="utf-8")
        return jsonify({"status": "ok", "approval": approval.name, "approved": approved})
    except SecurityValidationError:
        return jsonify({"error": "Invalid proposal name."}), 400
    except Exception:
        return _json_failure("Unable to approve proposal.", 500, "Proposal approval failed")


@app.post("/api/analyze_files")
def analyze_files():
    data = request.get_json() or {}
    files = data.get("files", [])
    user_prompt = data.get("input", "Analyze the following files and summarize findings.")
    model = data.get("model")
    session_id = data.get("sessionId", "akeem")
    csv_style = isinstance(user_prompt, str) and ("--csv" in user_prompt.lower())

    def _truncate(s: str, limit: int = 100000):
        return s if len(s) <= limit else (s[:limit] + "\n...[truncated]...")

    # Compose analysis prompt
    parts = [user_prompt.strip(), "\n\nFILES:"]
    for f in files:
        name = f.get("name") or "untitled"
        ftype = f.get("type") or "unknown"
        text = f.get("textContent")
        b64 = f.get("base64")
        if isinstance(text, str) and text.strip():
            parts.append(f"\n--- BEGIN FILE: {name} ({ftype}) ---\n{_truncate(text)}\n--- END FILE: {name} ---")
        elif isinstance(b64, str) and b64:
            decoded = _decode_b64_textlike(name, ftype, b64, csv_style=csv_style)
            if isinstance(decoded, str) and decoded.strip():
                parts.append(f"\n--- BEGIN FILE (decoded from base64): {name} ({ftype}) ---\n{_truncate(decoded)}\n--- END FILE: {name} ---")
            else:
                parts.append(f"\n--- FILE: {name} ({ftype}) has binary/base64 content (length {len(b64)}). Provide a high-level analysis based on filename and type. ---")
        else:
            parts.append(f"\n--- FILE: {name} ({ftype}) has no readable content provided. ---")
    parts.append("\nWhen proposing fixes, include fenced code blocks with 'file:' paths for auto-apply.")
    analysis_prompt = "\n".join(parts)

    reply = marven_response(analysis_prompt, session_id=session_id, model=model)
    return jsonify({"output": reply})

@app.post("/api/analyze_files_stream")
def analyze_files_stream():
    data = request.get_json() or {}
    files = data.get("files", [])
    user_prompt = data.get("input", "Analyze the following files and summarize findings.")
    model = data.get("model")
    session_id = data.get("sessionId", "akeem")
    message_id = data.get("messageId") or str(uuid.uuid4())
    csv_style = isinstance(user_prompt, str) and ("--csv" in user_prompt.lower())

    def _truncate(s: str, limit: int = 100000):
        return s if len(s) <= limit else (s[:limit] + "\n...[truncated]...")

    parts = [user_prompt.strip(), "\n\nFILES:"]
    for f in files:
        name = f.get("name") or "untitled"
        ftype = f.get("type") or "unknown"
        text = f.get("textContent")
        b64 = f.get("base64")
        if isinstance(text, str) and text.strip():
            parts.append(f"\n--- BEGIN FILE: {name} ({ftype}) ---\n{_truncate(text)}\n--- END FILE: {name} ---")
        elif isinstance(b64, str) and b64:
            decoded = _decode_b64_textlike(name, ftype, b64, csv_style=csv_style)
            if isinstance(decoded, str) and decoded.strip():
                parts.append(f"\n--- BEGIN FILE (decoded from base64): {name} ({ftype}) ---\n{_truncate(decoded)}\n--- END FILE: {name} ---")
            else:
                parts.append(f"\n--- FILE: {name} ({ftype}) has binary/base64 content (length {len(b64)}). Provide a high-level analysis based on filename and type. ---")
        else:
            parts.append(f"\n--- FILE: {name} ({ftype}) has no readable content provided. ---")
    parts.append("\nWhen proposing fixes, include fenced code blocks with 'file:' paths for auto-apply.")
    analysis_prompt = "\n".join(parts)

    def generate():
        try:
            def _ev(payload):
                try:
                    d = dict(payload)
                except Exception:
                    d = {"type": "text", "data": str(payload)}
                d.setdefault("id", message_id)
                return json.dumps(d) + "\n"
            # Preflight model availability
            try:
                lower_in = (user_prompt or "").strip().lower()
                model_to_check = (model or ("llama3:8b" if lower_in.startswith("code:") else "tinyllama"))
                ok, msg = ensure_ollama_model(model_to_check)
                if not ok:
                    fb = _offline_fallback_reply(user_prompt, model_to_check, msg)
                    for line in fb.split("\n\n"):
                        yield _ev({"type": "text", "data": line})
                    yield _ev({"type": "done"})
                    return
            except Exception:
                pass
            current_llm = marven.get_llm(model) if model else marven.choose_llm(analysis_prompt)
            conv = marven.RunnableWithMessageHistory(
                marven.prompt | current_llm,
                get_session_history=marven.get_history,
                input_messages_key="input",
                history_messages_key="history",
            )
            acc = ""
            for raw in conv.stream({"input": analysis_prompt}, config={"configurable": {"session_id": session_id}}):
                text = getattr(raw, "content", None)
                if not isinstance(text, str):
                    text = str(raw)
                delta = text[len(acc):] if text.startswith(acc) else text
                if delta.strip():
                    yield _ev({"type": "text", "data": delta})
                if text.startswith(acc) and len(text) >= len(acc):
                    acc = text
            yield _ev({"type": "done"})
        except Exception:
            _report_exception("File analysis stream failed")
            yield _ev({"type": "error", "error": "File analysis stream failed."})

    return Response(stream_with_context(generate()), mimetype="text/plain")

@app.route("/api/local/policy", methods=["GET", "POST"])
def local_policy():
    try:
        if marven_local is None:
            # Fallback to project root policy.yaml
            fallback = (Path(__file__).resolve().parent / "policy.yaml")
            if request.method == "GET":
                content = fallback.read_text(encoding="utf-8", errors="replace") if fallback.exists() else ""
                return jsonify({"path": str(fallback), "content": content})
            else:
                data = request.get_json() or {}
                content = data.get("content", "")
                fallback.write_text(content, encoding="utf-8")
                return jsonify({"status": "ok", "bytes": len(content.encode("utf-8"))})
        # Normal path with local scaffold
        policy, _, _ = _local_caps_and_updater()
        path = policy.path if hasattr(policy, "path") else (marven_local.ROOT / "policy.yaml")
        if request.method == "GET":
            return jsonify({"path": str(path), "content": Path(path).read_text(encoding="utf-8", errors="replace")})
        else:
            data = request.get_json() or {}
            content = data.get("content", "")
            Path(path).write_text(content, encoding="utf-8")
            return jsonify({"status": "ok", "bytes": len(content.encode("utf-8"))})
    except Exception:
        return _json_failure("Unable to update policy.", 500, "Policy operation failed")
# ---- Memory inspection endpoints ----
@app.get("/api/memory/top")
def api_memory_top():
    try:
        q = request.args.get("q") or request.args.get("query") or ""
        k = int(request.args.get("k", 5))
        boost = request.args.get("boost", "").split(",") if request.args.get("boost") else None
        rows = marven.memmgr.search(q, top_k=max(1, min(k, 20)), boost_tags=boost)
        out = [{"id": mid, "text": text, "tags": tags, "score": float(score)} for (mid, text, tags, score) in rows]
        return jsonify({"query": q, "top": out})
    except Exception:
        return _json_failure("Unable to search memory.", 500, "Memory search failed")


@app.get("/api/memory/hot")
def api_memory_hot():
    try:
        items = marven.memmgr.list_hot(50)
        return jsonify({"hot": items})
    except Exception:
        return _json_failure("Unable to load memory.", 500, "Hot memory listing failed")


@app.post("/api/memory/compress")
def api_memory_compress():
    try:
        res = marven.memmgr.compress_old()
        return jsonify({"status": "ok", "result": res})
    except Exception:
        return _json_failure("Unable to compress memory.", 500, "Memory compression failed")


# ---- Optional embedding endpoint (uses sentence-transformers if available) ----
@app.post("/api/embed")
def api_embed():
    try:
        data = request.get_json() or {}
        text = data.get("text", "")
        model_name = data.get("model", "intfloat/e5-small-v2")
        if not text.strip():
            return jsonify({"error": "Missing text"}), 400
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            model = SentenceTransformer(model_name)
            vec = model.encode([text], normalize_embeddings=True)[0]
            return jsonify({"embedding": vec.tolist(), "dim": int(len(vec))})
        except Exception:
            return _json_failure("Embedding backend is unavailable.", 503, "Embedding request failed")
    except Exception:
        return _json_failure("Unable to create embedding.", 500, "Embedding endpoint failed")


if __name__ == "__main__":
    app.run(port=8000, debug=False)
