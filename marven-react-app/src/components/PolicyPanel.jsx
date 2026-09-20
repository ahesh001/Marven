import React, { useEffect, useState } from "react";
const API_BASE = process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000";
export default function PolicyPanel() {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [scaffoldMissing, setScaffoldMissing] = useState(false);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      try {
        const capsRes = await fetch(`${API_BASE}/api/local/capabilities`);
        const caps = await capsRes.json().catch(() => ({}));
        setScaffoldMissing(capsRes.ok ? Object.keys(caps || {}).length === 0 : true);
      } catch {
        setScaffoldMissing(true);
      }
      const res = await fetch(`${API_BASE}/api/local/policy`);
      const j = await res.json();
      if (!res.ok) throw new Error(j.error || res.statusText);
      setNotice("Policy loaded");
      setTimeout(() => setNotice(""), 1500);
      setText(j.content || "");
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/local/policy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text }),
      });
      const j = await res.json();
      if (!res.ok) throw new Error(j.error || res.statusText);
      setNotice("Saved");
      setTimeout(() => setNotice(""), 1500);
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  return (
    <div style={{ width: 460, borderLeft: "1px solid #eee", paddingLeft: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <strong>Policy</strong>
        <button onClick={refresh} disabled={loading}>Refresh</button>
        <button onClick={save} disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
      {scaffoldMissing && (
        <div style={{ background: "#fffbe6", border: "1px solid #ffe58f", color: "#874d00", padding: 6, borderRadius: 6, fontSize: 12, marginBottom: 8 }}>
          Local scaffold isn’t available. Using fallback policy.yaml in project root.
        </div>
      )}
      {error && <div style={{ color: "#b00", fontSize: 12, marginBottom: 8 }}>{error}</div>}
      {notice && (
        <div style={{ background: "#e6ffed", border: "1px solid #b7eb8f", color: "#237804", padding: 6, borderRadius: 6, fontSize: 12, marginBottom: 8 }}>
          {notice}
        </div>
      )}
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={24}
        style={{ width: "100%", fontFamily: "monospace", border: "1px solid #ddd", borderRadius: 6, padding: 8 }}
        placeholder="policy.yaml"
      />
    </div>
  );
}
