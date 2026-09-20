import React, { useCallback, useEffect, useState } from "react";
import { listFiles, readFile, writeFile } from "../lib/marvenClient";

export default function FileBrowserPanel() {
  const [cwd, setCwd] = useState(".");
  const [entries, setEntries] = useState([]);
  const [selPath, setSelPath] = useState("");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async (dir = cwd) => {
    setError("");
    setLoading(true);
    try {
      const data = await listFiles(dir);
      setCwd(data.path || dir);
      setEntries(data.entries || []);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, [cwd]);

  useEffect(() => { refresh("."); }, [refresh]);

  async function openFile(p) {
    setError("");
    setSelPath(p);
    setLoading(true);
    try {
      const txt = await readFile(p);
      setContent(txt);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  async function saveFile() {
    if (!selPath) return;
    setSaving(true);
    setError("");
    try {
      await writeFile(selPath, content);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  }

  const goUp = () => {
    if (!cwd || cwd === ".") return refresh(".");
    const parts = cwd.split(/[\\/]+/);
    parts.pop();
    const parent = parts.join("/") || ".";
    refresh(parent);
  };

  return (
    <div style={{ width: 380, borderLeft: "1px solid #eee", paddingLeft: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <strong>Files</strong>
        <button onClick={() => refresh()} disabled={loading}>Refresh</button>
        <button onClick={goUp} disabled={loading}>Up</button>
        <span style={{ color: "#999", fontSize: 12 }}>{cwd}</span>
      </div>
      {error && <div style={{ color: "#b00", fontSize: 12, marginBottom: 8 }}>{error}</div>}
      <div style={{ display: "flex", gap: 12 }}>
        <div style={{ width: 180, maxHeight: 420, overflowY: "auto", border: "1px solid #ddd", borderRadius: 6 }}>
          {entries.map((e) => (
            <div
              key={e.path}
              style={{ padding: 6, borderBottom: "1px solid #eee", cursor: e.isDir ? "pointer" : "default", background: e.path === selPath ? "#f0f7ff" : "transparent" }}
              onClick={() => (e.isDir ? refresh(e.path) : openFile(e.path))}
            >
              {e.isDir ? String.fromCodePoint(0x1F4C1) : String.fromCodePoint(0x1F4C4)} {e.name}
            </div>
          ))}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ marginBottom: 6, fontSize: 12, color: "#666" }}>{selPath || "(no file selected)"}</div>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={20}
            style={{ width: "100%", fontFamily: "monospace", border: "1px solid #ddd", borderRadius: 6, padding: 8 }}
            placeholder="Select a file to view/edit its content"
          />
          <div style={{ marginTop: 8 }}>
            <button onClick={saveFile} disabled={!selPath || saving}>{saving ? "Saving..." : "Save"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

