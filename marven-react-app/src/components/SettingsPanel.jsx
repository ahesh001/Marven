import React, { useEffect, useState } from "react";

function getStoredApiBase() {
  try {
    const v = window.localStorage.getItem("API_BASE");
    if (v && v.trim()) return v.trim();
  } catch {}
  return "";
}

function getDefaultApiBase() {
  return (process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000");
}

export default function SettingsPanel() {
  const [apiBase, setApiBase] = useState("");
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const v = getStoredApiBase() || getDefaultApiBase();
    setApiBase(v);
  }, []);

  async function refreshHealth() {
    const base = (apiBase || getDefaultApiBase()).replace(/\/$/, "");
    const url = base + "/health";
    setLoading(true);
    setError("");
    try {
      const res = await fetch(url);
      const j = await res.json();
      setHealth(j);
    } catch (e) {
      setError((e && e.message) || String(e));
      setHealth(null);
    } finally {
      setLoading(false);
    }
  }

  function saveBase() {
    try {
      if (apiBase && apiBase.trim()) {
        window.localStorage.setItem("API_BASE", apiBase.trim());
      } else {
        window.localStorage.removeItem("API_BASE");
      }
      alert("API base saved. New requests will use this base.");
    } catch (e) {
      alert("Failed to save: " + ((e && e.message) || String(e)));
    }
  }

  return (
    <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12, background: "#fafafa" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <label style={{ fontWeight: 600 }}>API Base:</label>
        <input
          value={apiBase}
          onChange={(e) => setApiBase(e.target.value)}
          placeholder={getDefaultApiBase()}
          style={{ flex: "1 1 320px", minWidth: 280, border: "1px solid #ccc", borderRadius: 6, padding: "6px 8px" }}
        />
        <button onClick={saveBase} style={{ border: "1px solid #ccc", borderRadius: 6, padding: "6px 10px", background: "white" }}>Save</button>
        <button onClick={refreshHealth} disabled={loading} style={{ border: "1px solid #ccc", borderRadius: 6, padding: "6px 10px", background: "white" }}>{loading ? "Checking..." : "Check /health"}</button>
      </div>
      {error ? (
        <div style={{ color: "#b91c1c", marginTop: 8 }}>Error: {error}</div>
      ) : null}
      {health ? (
        <div style={{ marginTop: 10, fontSize: 13, color: "#222" }}>
          <div><b>OK:</b> {String(!!health.ok)}</div>
          <div style={{ marginTop: 6 }}><b>vLLM</b></div>
          <div>base_url: {String(health.vllm?.base_url || "")}</div>
          <div>reachable: {String(!!health.vllm?.reachable)}</div>
          <div>configured: {(health.vllm?.configured_models || []).join(", ")}</div>
          <div>reported: {(health.vllm?.models || []).join(", ")}</div>
          <div style={{ marginTop: 6 }}><b>Ollama</b></div>
          <div>installed: {String(!!health.ollama?.installed)}</div>
          <div>running: {String(!!health.ollama?.running)}</div>
          <div>models: {(health.ollama?.models || []).join(", ")}</div>
        </div>
      ) : null}
    </div>
  );
}

