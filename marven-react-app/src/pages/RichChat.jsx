import React, { useMemo, useRef, useState, useEffect } from "react";
import ModelSelector from "../components/ModelSelector";
import SettingsPanel from "../components/SettingsPanel";
import MicrophoneButton from "../components/MicrophoneButton";
import ChatBubble from "../components/ChatBubble";
import FileBrowserPanel from "../components/FileBrowserPanel";
import ProposalsPanel from "../components/ProposalsPanel";
import PolicyPanel from "../components/PolicyPanel";
import CommandBar from "../components/CommandBar";
import { streamMarven, streamVisionMarven, streamAnalyzeFiles } from "../lib/marvenStreamClient";
import { pullOllamaModel } from "../lib/marvenClient";
import { secureId, secureSessionId } from "../lib/secureId";

export default function RichChat() {
  const [model, setModel] = useState("mistral");
  const [showSettings, setShowSettings] = useState(false);
  const [messages, setMessages] = useState([
    { id: "welcome", content: "Hey! I'm Marven - streaming live. Ask me anything.", sender: "marven", ts: new Date() },
  ]);
  const [input, setInput] = useState("");
  const [statusMap, setStatusMap] = useState({});
  const controllersRef = useRef(new Map());
  const [images, setImages] = useState([]); // [{name,b64}]
  const [files, setFiles] = useState([]);   // [{name,type,textContent?,base64?}]
  const [autoApply, setAutoApply] = useState(false);
  const [selfAware, setSelfAware] = useState(false);
  const [showFiles, setShowFiles] = useState(false);
  const [showProps, setShowProps] = useState(false);
  const [showPolicy, setShowPolicy] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [phase, setPhase] = useState("");
  const [brainUrl, setBrainUrl] = useState("");
  const abortRef = useRef(null);
  const sessionId = useMemo(() => secureSessionId(), []);
  const endRef = useRef(null);
  const listRef = useRef(null);
  const [stickBottom, setStickBottom] = useState(true);
  const [queue, setQueue] = useState([]); // queued text items

  // Auto-scroll to the latest message only if user is near the bottom
  useEffect(() => { if (stickBottom) endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, statusMap, stickBottom]);

  const onScrollTranscript = (e) => {
    try {
      const el = e.currentTarget;
      const nearBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 80;
      setStickBottom(nearBottom);
    } catch {}
  };

  const setMessageContent = (id, content) => {
    setMessages((prev) => prev.map((m) => (m.id === id && m.sender === "marven" ? { ...m, content } : m)));
  };

  async function handleSend(seedText) {
    const text = (typeof seedText === 'string' ? seedText : input).trim();
    if (!text && images.length === 0 && files.length === 0) return;
    const streamingNow = Object.keys(statusMap).length > 0;
    if (streamingNow) { if (text) setQueue((q) => q.concat([{ text }])); setInput(""); return; }
    setInput("");
    const userId = secureId('u_');
    setMessages((prev) => [
      ...prev,
      { id: userId, content: text || (images.length ? "[Images attached]" : files.length ? "[Files attached]" : ""), sender: "user", ts: new Date() },
    ]);

    // Both images and files: send combined
    if (images.length && files.length) {
      const combined = [
        ...files,
        ...images.map((i) => ({ name: i.name || "image", type: "image/*", base64: i.b64 }))
      ];
      await streamToMessage("Analyze these attachments", (signal) => streamAnalyzeFiles(text || "Analyze these attachments", combined, { model, sessionId, selfAware, signal }), true, true);
      return;
    }
    if (images.length) {
      await streamToMessage("Describe the image", (signal) => streamVisionMarven(text || "Describe the image", images.map((i) => i.b64), { model: model || "llava", sessionId, selfAware, signal }), true, false);
      return;
    }
    if (files.length) {
      await streamToMessage("Analyze these files", (signal) => streamAnalyzeFiles(text || "Analyze these files", files, { model, sessionId, selfAware, signal }), false, true);
      return;
    }
    // Plain text streaming
    await streamToMessage(text, (signal) => streamMarven(text, { model, sessionId, autoApply, selfAware, signal }), false, false, true);
  }

  async function streamToMessage(prompt, makeStream, clearImages = false, clearFiles = false, allowApplied = false) {
    const mid = secureId('m_');
    setMessages((prev) => [...prev, { id: mid, content: "", sender: "marven", ts: new Date() }]);
    let acc = "";
    try {
      const controller = new AbortController();
      abortRef.current = controller;
      controllersRef.current.set(mid, controller);
      let last = 0;
      for await (const evt of makeStream(controller.signal)) {
        if (evt?.type === "status") {
          const ph = String(evt.data || "");
          setStatusMap((prev) => ({ ...prev, [mid]: ph }));
          setPhase(ph);
        } else if (evt?.type === "text" && evt.data) {
          acc += evt.data;
          const now = Date.now();
          if (now - last > 40) { last = now; setMessageContent(mid, acc); }
        } else if (allowApplied && evt?.type === "applied") {
          const sid = secureId('s_');
          const note = `Applied ${evt.files?.length || 0} file update(s).`;
          setMessages((prev) => [...prev, { id: sid, content: note, sender: "marven", ts: new Date() }]);
        } else if (evt?.type === "error") {
          setMessageContent(mid, `Error: ${evt.error}`);
        }
      }
    } catch (e) {
      if (e?.name !== "AbortError") setMessageContent(mid, `Error: ${String(e)}`);
    } finally {
      if (clearImages) setImages([]);
      if (clearFiles) setFiles([]);
      controllersRef.current.delete(mid);
      setStatusMap((prev) => { const copy = { ...prev }; delete copy[mid]; return copy; });
      abortRef.current = null; setPhase("");
      // run queued messages if any
      setQueue((q) => {
        if (q.length > 0) { const next = q[0]; setTimeout(() => handleSend(next.text), 0); return q.slice(1); }
        return q;
      });
    }
  }

  function pickFilesFromList(fileList) {
    const arr = Array.from(fileList || []);
    arr.forEach((f) => {
      if ((f.type || "").startsWith("image/")) {
        const r = new FileReader();
        r.onload = () => setImages((prev) => [...prev, { name: f.name || "image", b64: (r.result || "").toString().split(",")[1] || "" }]);
        r.readAsDataURL(f);
      } else {
        const isTextLike = (f.type || "").startsWith("text/") || /\.(md|txt|json|ya?ml|toml|ini|cfg|log|py|js|jsx|ts|tsx|css|scss|html|xml|c|h|cpp|hpp|rs|go|rb|java)$/i.test(f.name || "");
        if (isTextLike) {
          const r = new FileReader(); r.onload = () => setFiles((prev) => [...prev, { name: f.name, type: f.type || "text/plain", textContent: (r.result || "").toString() }]); r.readAsText(f);
        } else {
          const r = new FileReader(); r.onload = () => setFiles((prev) => [...prev, { name: f.name, type: f.type || "application/octet-stream", base64: (r.result || "").toString().split(",")[1] || "" }]); r.readAsDataURL(f);
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

  async function pullVisionModel() {
    try { setPulling(true); await pullOllamaModel("llava"); alert("llava model is ready."); }
    catch (e) { alert("Pull failed: " + (e?.message || String(e))); }
    finally { setPulling(false); }
  }

  return (
    <div style={{ padding: 16, display: "flex", gap: 16, maxWidth: 1200, margin: "0 auto" }}>
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>Marven Streaming Chat</h2>
          {Object.keys(statusMap).length ? (
            <div style={{ fontSize: 12, color: "#555", padding: "2px 8px", border: "1px solid #ddd", borderRadius: 12, background: "#fffbe6" }}>{Object.values(statusMap)[0]}</div>
          ) : null}
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <ModelSelector selectedModel={model} onChange={setModel} />
            <button onClick={() => setShowSettings((v) => !v)} style={{ border: "1px solid #ccc", borderRadius: 6, padding: "6px 10px", background: "white" }}>{showSettings ? "Hide Settings" : "Show Settings"}</button>
            <button onClick={pullVisionModel} disabled={pulling}>{pulling ? (<><span className="spinner" />Pulling llava...</>) : "Pull llava"}</button>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={autoApply} onChange={(e) => setAutoApply(e.target.checked)} />
              <span style={{ fontSize: 12, color: "#555" }}>Auto-apply code fences</span>
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={selfAware} onChange={(e) => setSelfAware(e.target.checked)} />
              <span style={{ fontSize: 12, color: "#555" }}>Self-aware</span>
            </label>
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
            <button onClick={() => setShowFiles((v) => !v)}>{showFiles ? "Hide Files" : "Show Files"}</button>
            <button onClick={() => setShowProps((v) => !v)}>{showProps ? "Hide Proposals" : "Show Proposals"}</button>
            <button onClick={() => setShowPolicy((v) => !v)}>{showPolicy ? "Hide Policy" : "Show Policy"}</button>
          </div>
        </div>
        {showSettings && (
          <div style={{ marginBottom: 12 }}>
            <SettingsPanel />
          </div>
        )}

        <div ref={listRef} onScroll={onScrollTranscript} style={{ height: 520, overflowY: "auto", border: "1px solid #ddd", padding: 12, borderRadius: 8, background: "#f6f8fa" }}>
          {messages.map((m, i) => (
            <ChatBubble
              key={m.id}
              role={m.sender === "user" ? "user" : "assistant"}
              content={m.content || (m.sender === "marven" && statusMap[m.id] ? (statusMap[m.id] || "Marven is working...") : "")}
              streaming={controllersRef.current.has(m.id)}
              ts={m.ts}
              onRetry={() => {
                const prev = messages[i - 1];
                if (prev && prev.sender === "user" && Object.keys(statusMap).length === 0) {
                  setInput(prev.content || "");
                  setTimeout(() => { if (prev.content) handleSend(); }, 0);
                }
              }}
            />
          ))}
          <div ref={endRef} />
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ padding: "6px 10px", border: "1px solid #ccc", borderRadius: 6, cursor: "pointer" }}>
            {String.fromCodePoint(0x1F4CE)} Attach
            <input type="file" multiple onChange={(e) => { pickFilesFromList(e.target.files); e.target.value = ""; }} style={{ display: "none" }} />
          </label>
          <CommandBar value={input} setValue={setInput} onRun={() => handleSend()} />
          <textarea
            rows={2}
            placeholder="Type while Marven replies (Enter to send)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
            onDrop={onDrop}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
            onPaste={onPaste}
            style={{ flex: 1, padding: 10, borderRadius: 8, border: "1px solid #ccc" }}
          />
          <MicrophoneButton onResult={(t) => setInput((prev) => (prev ? prev + " " + t : t))} />
          <button onClick={() => handleSend()} disabled={!input.trim() && images.length === 0 && files.length === 0}>Send</button>
          <button onClick={() => { controllersRef.current.forEach((c) => { try { c.abort(); } catch {} }); }} disabled={Object.keys(statusMap).length === 0} title="Stop">{String.fromCodePoint(0x23F9)} Stop</button>
          {queue.length > 0 ? (<span style={{ fontSize: 12, color: "#666" }}>Queued: {queue.length}</span>) : null}
        </div>
        {phase ? (<div style={{ fontSize: 12, color: "#666", marginTop: 6 }}><span className="spinner" />Working... ({phase})</div>) : null}

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












