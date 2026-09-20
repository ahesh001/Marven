import React, { useEffect, useState } from "react";

async function apiGet(path, params) {
  const url = new URL((process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000") + path);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url.toString());
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function apiPost(path, body) {
  const res = await fetch((process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000") + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

export default function ProposalsPanel() {
  const [items, setItems] = useState([]);
  const [sel, setSel] = useState(null);
  const [content, setContent] = useState("");
  const [approval, setApproval] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [scaffoldMissing, setScaffoldMissing] = useState(false);

  async function refresh() {
    setError("");
    setLoading(true);
    try {
      // Ping capabilities to detect scaffold presence
      try {
        const capsRes = await fetch((process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000") + "/api/local/capabilities");
        const caps = await capsRes.json().catch(() => ({}));
        setScaffoldMissing(capsRes.ok ? Object.keys(caps || {}).length === 0 : true);
      } catch {
        setScaffoldMissing(true);
      }
      const list = await apiGet("/api/local/self_update/proposals");
      setItems(list);
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function openProposal(name) {
    try {
      setSel(name);
      const p = await apiGet("/api/local/self_update/proposal", { name });
      setContent(p.content || "");
      setApproval(p.approval_content || null);
    } catch (e) {
      setError(e?.message || String(e));
    }
  }

  async function approveCurrent() {
    if (!sel) return;
    try {
      await apiPost("/api/local/self_update/approve", { proposal: sel, approved: true });
      await refresh();
      await openProposal(sel);
    } catch (e) {
      setError(e?.message || String(e));
    }
  }

  async function applyApproved() {
    try {
      await apiPost("/api/local/self_update/apply");
      await refresh();
    } catch (e) {
      setError(e?.message || String(e));
    }
  }

  return (
    <div style={{ width: 420, borderLeft: "1px solid #eee", paddingLeft: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <strong>Proposals</strong>
        <button onClick={refresh} disabled={loading}>Refresh</button>
        <button onClick={applyApproved}>Apply Approved</button>
      </div>
      {scaffoldMissing && (
        <div style={{ background: "#fffbe6", border: "1px solid #ffe58f", color: "#874d00", padding: 6, borderRadius: 6, fontSize: 12, marginBottom: 8 }}>
          Local scaffold isn’t available. Showing limited proposals view.
        </div>
      )}
      {error && <div style={{ color: "#b00", fontSize: 12, marginBottom: 8 }}>{error}</div>}
      <div style={{ display: "flex", gap: 12 }}>
        <div style={{ width: 200, maxHeight: 420, overflowY: "auto", border: "1px solid #ddd", borderRadius: 6 }}>
          {items.map((it) => (
            <div
              key={it.name}
              onClick={() => openProposal(it.name)}
              style={{ padding: 6, borderBottom: "1px solid #eee", cursor: "pointer", background: sel===it.name?"#f0f7ff":"transparent" }}
            >
              {it.manifest?.description || it.name}
              <div style={{ fontSize: 11, color: "#666" }}>{it.approved ? "approved" : "pending"}</div>
            </div>
          ))}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
            <button onClick={approveCurrent} disabled={!sel}>Approve</button>
          </div>
          <pre style={{ whiteSpace: "pre-wrap", border: "1px solid #ddd", borderRadius: 6, padding: 8, maxHeight: 420, overflowY: "auto" }}>
            {content || "(select a proposal to view diff)"}
          </pre>
          {approval ? (
            <div style={{ fontSize: 12, color: "#555", marginTop: 6 }}>Approval metadata loaded.</div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

