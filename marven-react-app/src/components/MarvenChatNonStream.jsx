import React, { useMemo, useRef, useState, useEffect } from "react";
import MicrophoneButton from "./MicrophoneButton";
import { askMarven, askMarvenAnalyzeFiles, askMarvenVision } from "../lib/marvenClient";
import FileBrowserPanel from "./FileBrowserPanel";
import ProposalsPanel from "./ProposalsPanel";
import PolicyPanel from "./PolicyPanel";
import CommandBar from "./CommandBar";

function randomSessionId() { return Math.random().toString(36).slice(2); }
function uid(prefix = "") {
  const rand = Math.random().toString(36).slice(2);
  const ts = Date.now().toString(36);
  return (prefix || "") + rand + "_" + ts;
}
export default function MarvenChatNonStream({ model }) {
  const [messages, setMessages] = useState([
    { id: "welcome", content: "Hello! I'm Marven. How can I assist you today?", sender: "marven", timestamp: new Date() },
  ]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [images, setImages] = useState([]); // [{name,b64}]
  const [files, setFiles] = useState([]);   // [{name,type,textContent?,base64?}]
  const [autoApply, setAutoApply] = useState(false);
  const [showFiles, setShowFiles] = useState(false);
  const [showProps, setShowProps] = useState(false);
  const [showPolicy, setShowPolicy] = useState(false);
  const [brainUrl, setBrainUrl] = useState("");
  const [selfAware, setSelfAware] = useState(false);
  const sessionId = useMemo(() => randomSessionId(), []);
  const endRef = useRef(null);
  const listRef = useRef(null);
  const [stickBottom, setStickBottom] = useState(true);
  const [queue, setQueue] = useState([]); // queued text strings
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);
  const abortRef = useRef(null);

  useEffect(() => { if (stickBottom) endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, stickBottom]);

  const onScrollTranscript = (e) => {
    try {
      const el = e.currentTarget;
      const nearBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 80;
      setStickBottom(nearBottom);
    } catch {}
  };

  async function handleSend(seedText) {
    const text = (typeof seedText === 'string' ? seedText : input).trim();
    if (!text && images.length === 0 && files.length === 0) return;
    if (isSending) {
      if (text) setQueue(q => q.concat([text]));
      setInput("");
      return;
    }
    if (paused && !text && queue.length > 0) {
      const next = queue[0];
      setQueue((q) => q.slice(1));
      setPaused(false);
      pausedRef.current = false;
      return handleSend(next);
    }
    setInput("");
    const userId = uid('u_');
    setMessages((prev) => [...prev, { id: userId, content: text || (images.length ? "[Images attached]" : files.length ? "[Files attached]" : ""), sender: "user", timestamp: new Date() }]);
    setIsSending(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      if (images.length && files.length) {
        const combined = [
          ...files,
          ...images.map((i) => ({ name: i.name || "image", type: "image/*", base64: i.b64 }))
        ];
        const mid = uid('m_');
        setMessages((prev) => [...prev, { id: mid, content: "", sender: "marven", timestamp: new Date() }]);
        const out = await askMarvenAnalyzeFiles(text || "Analyze these attachments", combined, { model, sessionId, selfAware, signal: controller.signal });
        setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: out } : m)));
        return;
      }
      if (images.length) {
        const mid = uid('m_');
        setMessages((prev) => [...prev, { id: mid, content: "", sender: "marven", timestamp: new Date() }]);
        const out = await askMarvenVision(text || "Describe the image", images.map((i) => i.b64), { model: model || "llava", sessionId, selfAware, signal: controller.signal });
        setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: out } : m)));
        return;
      }
      if (files.length) {
        const mid = uid('m_');
        setMessages((prev) => [...prev, { id: mid, content: "", sender: "marven", timestamp: new Date() }]);
        const out = await askMarvenAnalyzeFiles(text || "Analyze these files", files, { model, sessionId, selfAware, signal: controller.signal });
        setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: out } : m)));
        return;
      }
      // Text non-streaming
      {
        const mid = uid('m_');
        setMessages((prev) => [...prev, { id: mid, content: "", sender: "marven", timestamp: new Date() }]);
        const out = await askMarven(text, { model, sessionId, autoApply, selfAware, signal: controller.signal });
        setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: out } : m)));
        return;
      }
    } catch (err) {
      const mid = uid('m_');
      setMessages((prev) => [...prev, { id: mid, content: `${err?.name === 'AbortError' ? 'Stopped.' : 'Error: ' + (err?.message || String(err))}`, sender: "marven", timestamp: new Date() }]);
    } finally {
      setIsSending(false);
      setImages([]);
      setFiles([]);
      abortRef.current = null;
      // process next queued text
      if (!pausedRef.current) { setQueue((q) => { if (q.length > 0) { const next = q[0]; setTimeout(() => handleSend(next), 0); return q.slice(1); } return q; }); }
    }
  }

  function pickFilesFromList(fileList) {
    const arr = Array.from(fileList || []);
    arr.forEach((f) => {
      if ((f.type || "").startsWith("image/")) {
        const r = new FileReader();
        r.onload = () => {
          const b64 = (r.result || "").toString().split(",")[1] || "";
          if (b64) setImages((prev) => [...prev, { name: f.name || "image", b64 }]);
        };
        r.readAsDataURL(f);
      } else {
        const isTextLike = (f.type || "").startsWith("text/") || /\.(md|txt|json|ya?ml|toml|ini|cfg|log|py|js|jsx|ts|tsx|css|scss|html|xml|c|h|cpp|hpp|rs|go|rb|java)$/i.test(f.name || "");
        if (isTextLike) {
          const r = new FileReader();
          r.onload = () => {
            const text = (r.result || "").toString();
            setFiles((prev) => [...prev, { name: f.name, type: f.type || "text/plain", textContent: text }]);
          };
          r.readAsText(f);
        } else {
          const r = new FileReader();
          r.onload = () => {
            const b64 = (r.result || "").toString().split(",")[1] || "";
            setFiles((prev) => [...prev, { name: f.name, type: f.type || "application/octet-stream", base64: b64 }]);
          };
          r.readAsDataURL(f);
        }
      }
    });
  }

  function onDrop(e) { e.preventDefault(); e.stopPropagation(); if (e.dataTransfer?.files?.length) pickFilesFromList(e.dataTransfer.files); }
  function onPaste(e) {
    const items = e.clipboardData?.items || []; const list = [];
    for (let i = 0; i < items.length; i++) { const it = items[i]; if (it.kind === "file") { const f = it.getAsFile(); if (f) list.push(f); } }
    if (list.length) { e.preventDefault(); pickFilesFromList(list); }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  }

  return (
    <div style={{ padding: 16, display: "flex", gap: 16, maxWidth: 1200, margin: "0 auto" }}>
      <div style={{ flex: 1 }}>
        <div ref={listRef} onScroll={onScrollTranscript} style={{ height: 520, overflowY: "auto", border: "1px solid #ddd", padding: 12, borderRadius: 8, background: "#f6f8fa" }}>
          {messages.map((m) => (
            <div key={m.id} style={{ margin: "10px 0" }}>
              <div style={{ fontSize: 12, color: "#666", marginBottom: 4 }}>{m.sender === "user" ? "You" : "Marven"}</div>
              <div style={{ padding: "10px 12px", borderRadius: 8, background: m.sender === "user" ? "#e7f0ff" : "white", border: "1px solid #e1e1e1" }}>
                {(m.content || "")
                  .split(/\n{2,}/)
                  .map((para, idx) => (
                    <p key={idx} style={{ margin: '8px 0', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{para}</p>
                  ))}
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ padding: "6px 10px", border: "1px solid #ccc", borderRadius: 6, cursor: "pointer" }}>
            {String.fromCodePoint(0x1F4CE)} Attach
            <input type="file" multiple onChange={(e) => { pickFilesFromList(e.target.files); e.target.value = ""; }} style={{ display: "none" }} />
          </label>
          <CommandBar value={input} setValue={setInput} onRun={() => handleSend()} disabled={false} />
          <textarea
            rows={2}
            placeholder="Type a message or drop files here (Enter to send)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onDrop={onDrop}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
            onPaste={onPaste}
            style={{ flex: 1, padding: 10, borderRadius: 8, border: "1px solid #ccc" }}
          />
          <MicrophoneButton disabled={isSending} onResult={(t) => setInput((prev) => (prev ? prev + " " + t : t))} />
          <button onClick={() => handleSend()} disabled={(!input.trim() && images.length === 0 && files.length === 0)}>{isSending ? "Queue" : (paused && queue.length > 0 ? "Resume" : "Send")}</button>
          <button onClick={() => { try { abortRef.current?.abort(); } catch {} finally { if (queue.length > 0) { setPaused(true); pausedRef.current = true; } } }} disabled={!isSending} title="Stop">{String.fromCodePoint(0x23F9)} Stop</button>
          {queue.length > 0 ? (<span style={{ fontSize: 12, color: "#666" }}>Queued: {queue.length}</span>) : null}
          {paused && queue.length > 0 ? (<span style={{ fontSize: 12, color: "#999", marginLeft: 6 }}>Paused</span>) : null}
          <label style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: 8 }}>
            <input type="checkbox" checked={autoApply} onChange={(e) => setAutoApply(e.target.checked)} />
            <span style={{ fontSize: 12, color: "#555" }}>Auto-apply code fences</span>
          </label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={selfAware} onChange={(e) => setSelfAware(e.target.checked)} />
            <span style={{ fontSize: 12, color: "#555" }}>Self-aware</span>
          </label>
          <button onClick={async () => {
            try {
              const base = (process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000");
              const metaRes = await fetch(base + "/api/brain/meta");
              const meta = await metaRes.json();
              const variant = (meta?.available?.includes("full")) ? "full" : (meta?.available?.[0] || "full");
              const tourUrl = `${base}/api/brain/tour?variant=${variant}`;
              const resp = await fetch(tourUrl);
              const ctype = (resp.headers.get("content-type") || "").toLowerCase();
              if (resp.ok && ctype.includes("audio")) {
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                setBrainUrl(url);
              } else {
                try {
                  const j = await resp.json();
                  if (j && j.redirect) {
                    setBrainUrl(j.redirect);
                  } else {
                    console.warn("Brain tour not available:", j);
                    alert("Brain tour not available. Ensure offline assets exist or MARVEN_BRAIN_BASE_URL is set.");
                  }
                } catch (_) {
                  console.warn("Unexpected brain tour response.");
                  alert("Brain tour not available. Ensure offline assets exist or MARVEN_BRAIN_BASE_URL is set.");
                }
              }
            } catch (e) {
              console.warn("Brain tour error:", e);
              alert("Brain tour failed to load. Check server logs.");
            }
          }}>Play Brain Tour</button>
          <button onClick={() => setShowFiles((v) => !v)} style={{ marginLeft: 8 }}>{showFiles ? "Hide Files" : "Show Files"}</button>
          <button onClick={() => setShowProps((v) => !v)} style={{ marginLeft: 8 }}>{showProps ? "Hide Proposals" : "Show Proposals"}</button>
          <button onClick={() => setShowPolicy((v) => !v)} style={{ marginLeft: 8 }}>{showPolicy ? "Hide Policy" : "Show Policy"}</button>
        </div>

        {(images.length > 0 || files.length > 0) && (
          <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
            {images.map((img, idx) => (
              <div key={`img-${idx}`} style={{ display: "flex", alignItems: "center", gap: 6, border: "1px solid #ddd", borderRadius: 16, padding: "4px 8px", background: "#f8fafc" }}>
                <img alt={img.name} src={`data:image/*;base64,${img.b64}`} style={{ width: 24, height: 24, objectFit: "cover", borderRadius: 4 }} />
                <span style={{ fontSize: 12 }}>{img.name || `image-${idx + 1}`}</span>
                <button onClick={() => setFiles((prev) => prev.filter((_, i) => i !== idx))} title="Remove" style={{ border: "none", background: "transparent", cursor: "pointer" }}>Remove</button>
              </div>
            ))}
            {files.map((f, idx) => (
              <div key={`file-${idx}`} style={{ display: "flex", alignItems: "center", gap: 6, border: "1px solid #ddd", borderRadius: 16, padding: "4px 8px", background: "#f8fafc" }}>
                <span>File</span>
                <span style={{ fontSize: 12 }}>{f.name}</span>
                <button onClick={() => setFiles((prev) => prev.filter((_, i) => i !== idx))} title="Remove" style={{ border: "none", background: "transparent", cursor: "pointer" }}>Remove</button>
              </div>
            ))}
          </div>
        )}

        <div style={{ color: "#888", fontSize: 12, marginTop: 6 }}>Session: {sessionId} {model ? `- Model: ${model}` : ""}</div>
        {brainUrl ? (
          <div style={{ marginTop: 8 }}>
            <audio src={brainUrl} controls autoPlay style={{ width: '100%' }} />
          </div>
        ) : null}
      </div>
      {showFiles && <FileBrowserPanel />}
      {showProps && <ProposalsPanel />}
      {showPolicy && <PolicyPanel />}
    </div>
  );
}










