import React, { useMemo, useRef, useState, useEffect } from "react";
import MicrophoneButton from "./MicrophoneButton";
import { streamMarven, streamAnalyzeFiles, streamVisionMarven } from "../lib/marvenStreamClient";
import { pullOllamaModel } from "../lib/marvenClient";
import FileBrowserPanel from "./FileBrowserPanel";
import ProposalsPanel from "./ProposalsPanel";
import PolicyPanel from "./PolicyPanel";
import CommandBar from "./CommandBar";
import ChatBubble from "./ChatBubble";
import { secureId, secureSessionId } from "../lib/secureId";

export default function MarvenChat({ model }) {
  const [messages, setMessages] = useState([
    { id: "welcome", content: "Hello! I'm Marven. How can I assist you today?", sender: "marven", timestamp: new Date() },
  ]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [images, setImages] = useState([]);
  const [files, setFiles] = useState([]);
  const [autoApply, setAutoApply] = useState(false);
  const [showFiles, setShowFiles] = useState(false);
  const [showProps, setShowProps] = useState(false);
  const [showPolicy, setShowPolicy] = useState(false);
  const [brainUrl, setBrainUrl] = useState("");
  const [selfAware, setSelfAware] = useState(false);
  const [phase, setPhase] = useState("");
  const sessionId = useMemo(() => secureSessionId(), []);
  const endRef = useRef(null);
  const abortRef = useRef(null);
  const currentIdRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, phase]);

  async function handleSend() {
    const text = input.trim();
    if (!text && images.length === 0 && files.length === 0) return;
    if (isSending) return;
    setInput("");
    const userId = secureId('u_');
    setMessages((prev) => prev.concat([{ id: userId, content: text || (images.length ? "[Images attached]" : files.length ? "[Files attached]" : ""), sender: "user", timestamp: new Date() }]));
    setIsSending(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      if (images.length && files.length) {
        const combined = [
          ...files,
          ...images.map((i) => ({ name: i.name || "image", type: "image/*", base64: i.b64 }))
        ];
        const mid = secureId('m_');
        currentIdRef.current = mid;
        setMessages((prev) => prev.concat([{ id: mid, content: "", sender: "marven", timestamp: new Date() }]));
        let acc = ""; let last = 0;
        for await (const evt of streamAnalyzeFiles(text || "Analyze these attachments", combined, { model, sessionId, selfAware, messageId: mid, signal: controller.signal })) {
          if (evt?.id && currentIdRef.current && evt.id !== currentIdRef.current) { continue; }
          if (evt?.type === "status") { setPhase(String(evt.data || "")); continue; }
          if (evt?.type === "text" && evt.data) { acc += evt.data; const now = Date.now(); if (now - last > 40) { last = now; setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: acc } : m))); } }
          else if (evt?.type === "applied") { const note = `Applied ${evt.files?.length || 0} file update(s).`; setMessages((prev) => prev.concat([{ id: secureId('s_'), content: note, sender: "marven", timestamp: new Date() }])); }
          else if (evt?.type === "error") { setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: `Error: ${evt.error}` } : m))); }
        }
        setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: acc } : m)));
        setPhase("");
        return;
      }
      if (images.length) {
        const mid = secureId('m_');
        currentIdRef.current = mid;
        setMessages((prev) => prev.concat([{ id: mid, content: "", sender: "marven", timestamp: new Date() }]));
        let acc = ""; let last = 0;
        for await (const evt of streamVisionMarven(text || "Describe the image", images.map((i) => i.b64), { model: model || "llava", sessionId, selfAware, messageId: mid, signal: controller.signal })) {
          if (evt?.id && currentIdRef.current && evt.id !== currentIdRef.current) { continue; }
          if (evt?.type === "status") { setPhase(String(evt.data || "")); continue; }
          if (evt?.type === "text" && evt.data) { acc += evt.data; const now = Date.now(); if (now - last > 40) { last = now; setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: acc } : m))); } }
          else if (evt?.type === "error") { setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: `Error: ${evt.error}` } : m))); }
        }
        setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: acc } : m)));
        setPhase("");
        return;
      }
      if (files.length) {
        const mid = secureId('m_');
        currentIdRef.current = mid;
        setMessages((prev) => prev.concat([{ id: mid, content: "", sender: "marven", timestamp: new Date() }]));
        let acc = ""; let last = 0;
        for await (const evt of streamAnalyzeFiles(text || "Analyze these files", files, { model, sessionId, selfAware, messageId: mid, signal: controller.signal })) {
          if (evt?.id && currentIdRef.current && evt.id !== currentIdRef.current) { continue; }
          if (evt?.type === "status") { setPhase(String(evt.data || "")); continue; }
          if (evt?.type === "text" && evt.data) { acc += evt.data; const now = Date.now(); if (now - last > 40) { last = now; setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: acc } : m))); } }
          else if (evt?.type === "error") { setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: `Error: ${evt.error}` } : m))); }
        }
        setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: acc } : m)));
        setPhase("");
        return;
      }
      // Text streaming
      {
        const mid = secureId('m_');
        currentIdRef.current = mid;
        setMessages((prev) => prev.concat([{ id: mid, content: "", sender: "marven", timestamp: new Date() }]));
        let acc = ""; let last = 0;
        for await (const evt of streamMarven(text, { model, sessionId, autoApply, selfAware, messageId: mid, signal: controller.signal })) {
          if (evt?.id && currentIdRef.current && evt.id !== currentIdRef.current) { continue; }
          if (evt?.type === "status") { setPhase(String(evt.data || "")); continue; }
          if (evt?.type === "text" && evt.data) { acc += evt.data; const now = Date.now(); if (now - last > 40) { last = now; setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: acc } : m))); } }
          else if (evt?.type === "applied") { const note = `Applied ${evt.files?.length || 0} file update(s).`; setMessages((prev) => prev.concat([{ id: secureId('s_'), content: note, sender: "marven", timestamp: new Date() }])); }
          else if (evt?.type === "error") { setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: `Error: ${evt.error}` } : m))); }
        }
        setMessages((prev) => prev.map((m) => (m.id === mid ? { ...m, content: acc } : m)));
        setPhase("");
        return;
      }
    } catch (err) {
      const mid = secureId('m_');
      setMessages((prev) => prev.concat([{ id: mid, content: `${err?.name === 'AbortError' ? 'Stopped.' : 'Error: ' + (err?.message || String(err))}`, sender: "marven", timestamp: new Date() }]));
    } finally {
      setIsSending(false);
      setImages([]); setFiles([]);
      abortRef.current = null; setPhase("");
    }
  }

  function pickFilesFromList(fileList) {
    const arr = Array.from(fileList || []);
    arr.forEach((f) => {
      if ((f.type || "").startsWith("image/")) {
        const r = new FileReader();
        r.onload = () => { const b64 = (r.result || "").toString().split(",")[1] || ""; if (b64) setImages((p) => p.concat([{ name: f.name || "image", b64 }])); };
        r.readAsDataURL(f);
      } else {
        const isTextLike = (f.type || "").startsWith("text/") || /\.(md|txt|json|ya?ml|toml|ini|cfg|log|py|js|jsx|ts|tsx|css|scss|html|xml|c|h|cpp|hpp|rs|go|rb|java)$/i.test(f.name || "");
        if (isTextLike) {
          const r = new FileReader(); r.onload = () => setFiles((p) => p.concat([{ name: f.name, type: f.type || "text/plain", textContent: (r.result || "").toString() }])); r.readAsText(f);
        } else {
          const r = new FileReader(); r.onload = () => setFiles((p) => p.concat([{ name: f.name, type: f.type || "application/octet-stream", base64: (r.result || "").toString().split(",")[1] || "" }])); r.readAsDataURL(f);
        }
      }
    });
  }
  function onDrop(e) { e.preventDefault(); e.stopPropagation(); if (e.dataTransfer?.files?.length) pickFilesFromList(e.dataTransfer.files); }
  function onPaste(e) { const items = e.clipboardData?.items || []; const list = []; for (let i = 0; i < items.length; i++) { const it = items[i]; if (it.kind === "file") { const f = it.getAsFile(); if (f) list.push(f); } } if (list.length) { e.preventDefault(); pickFilesFromList(list); } }
  function onKeyDown(e) { if (e.key === "Enter" && !e.shiftKey) { if (isSending) { e.preventDefault(); return; } e.preventDefault(); handleSend(); } }

  const lastAssistantId = (() => { const rev = [...messages].reverse(); const m = rev.find(x => x.sender !== "user"); return m?.id || null; })();

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", display: "flex", gap: 16 }}>
      <div style={{ flex: 1 }}>
        <div id="transcript" style={{ height: 480, overflowY: "auto", border: "1px solid #ddd", padding: 12, borderRadius: 8, background: "#f6f8fa" }}>
          {messages.map((m) => (
            <ChatBubble
              key={m.id}
              role={m.sender === "user" ? "user" : "assistant"}
              content={m.content}
              ts={m.timestamp}
              streaming={isSending && m.sender !== "user" && m.id === lastAssistantId}
            />
          ))}
          <div ref={endRef} />
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ padding: "6px 10px", border: "1px solid #ccc", borderRadius: 6, cursor: "pointer" }}>
            {String.fromCodePoint(0x1F4CE)} Attach
            <input type="file" multiple onChange={(e) => { pickFilesFromList(e.target.files); e.target.value = ""; }} style={{ display: "none" }} />
          </label>
          <CommandBar value={input} setValue={setInput} onRun={handleSend} disabled={isSending} />
          <textarea
            rows={2}
            placeholder="Message Marven... (Enter to send, Shift+Enter newline)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onDrop={onDrop}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
            onPaste={onPaste}
            style={{ flex: 1, padding: 10, borderRadius: 12, border: "1px solid #ccc", lineHeight: 1.7 }}
          />
          <MicrophoneButton disabled={isSending} onResult={(t) => setInput((prev) => (prev ? prev + " " + t : t))} />
          <button onClick={handleSend} disabled={(!input.trim() && images.length === 0 && files.length === 0) || isSending} style={{ borderRadius: 12, padding: "8px 12px", background: "#1e40af", color: "white", border: "none" }}>{isSending ? "Sending..." : "Send"}</button>
          <button onClick={() => { try { abortRef.current?.abort(); } catch {}; currentIdRef.current = `stopped_${Date.now()}`; }} disabled={!isSending} title="Stop" style={{ borderRadius: 12, padding: "8px 12px", border: "1px solid #ddd", background: "white" }}>{String.fromCodePoint(0x23F9)} Stop</button>
          <label style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: 8 }}>
            <input type="checkbox" checked={autoApply} onChange={(e) => setAutoApply(e.target.checked)} />
            <span style={{ fontSize: 12, color: "#555" }}>Auto-apply code fences</span>
          </label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={selfAware} onChange={(e) => setSelfAware(e.target.checked)} />
            <span style={{ fontSize: 12, color: "#555" }}>Self-aware</span>
          </label>
          {phase ? <span style={{ fontSize: 12, color: "#666" }}><span className="spinner" />Working... ({phase})</span> : null}
          <button onClick={async () => {
            try {
              const base = (typeof window !== 'undefined' && window.localStorage.getItem('API_BASE')) || (process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000");
              const metaRes = await fetch(base + "/api/brain/meta");
              const meta = await metaRes.json();
              const variant = (meta?.available?.includes("full")) ? "full" : (meta?.available?.[0] || "full");
              const tourUrl = `${base}/api/brain/tour?variant=${variant}`;
              const resp = await fetch(tourUrl);
              const ctype = (resp.headers.get("content-type") || "").toLowerCase();
              if (resp.ok && ctype.includes("audio")) {
                const blob = await resp.blob(); const url = URL.createObjectURL(blob); setBrainUrl(url);
              } else {
                try { const j = await resp.json(); if (j && j.redirect) { setBrainUrl(j.redirect); } else { alert("Brain tour not available. Ensure offline assets exist or MARVEN_BRAIN_BASE_URL is set."); } }
                catch { alert("Brain tour not available. Ensure offline assets exist or MARVEN_BRAIN_BASE_URL is set."); }
              }
            } catch (e) { alert("Brain tour failed to load. Check server logs."); }
          }}>Play Brain Tour</button>
          <button onClick={() => setShowFiles((v) => !v)} style={{ marginLeft: 8 }}>{showFiles ? "Hide Files" : "Show Files"}</button>
          <button onClick={() => setShowProps((v) => !v)} style={{ marginLeft: 8 }}>{showProps ? "Hide Proposals" : "Show Proposals"}</button>
          <button onClick={() => setShowPolicy((v) => !v)} style={{ marginLeft: 8 }}>{showPolicy ? "Hide Policy" : "Show Policy"}</button>
          <button onClick={async () => {
            try {
              setPhase("pulling model");
              const m = model || "tinyllama";
              if (String(m).toLowerCase() === 'marven') {
                alert("'marven' is a local custom Ollama model. Create it via: ollama create marven -f <path-to-Modelfile>");
              } else {
                await pullOllamaModel(m);
                alert(`Model '${m}' is available.`);
              }
            } catch (e) {
              alert(`Model pull failed: ${e?.message || String(e)}`);
            } finally {
              setPhase("");
            }
          }} style={{ marginLeft: 8 }}>Pull Model</button>
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








