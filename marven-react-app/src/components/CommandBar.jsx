import React, { useState } from "react";

const PRESETS = [
  { key: "remember", label: "remember:", template: "remember: <fact or preference>" },
  { key: "remember_mm", label: "remember_mm:", template: "remember_mm: <principle or reflection>" },
  { key: "web_search", label: "web:search", template: "web:search <query>" },
  { key: "web_analyze", label: "web:analyze", template: "web:analyze https://example.com" },
  { key: "web_compare", label: "web:compare", template: "web:compare https://a.com https://b.com" },
  { key: "readfile", label: "readfile:", template: "readfile: path/to/file.txt" },
  { key: "writefile", label: "writefile:", template: "writefile: path/to/file.txt\n\n<content>" },
  { key: "writedocx", label: "write:docx", template: "write:docx path/to/file.docx\n\n<content>" },
];

export default function CommandBar({ value, setValue, onRun, disabled }) {
  const [sel, setSel] = useState(PRESETS[0].key);
  const selected = PRESETS.find(p => p.key === sel) || PRESETS[0];
  function insert() {
    const t = selected.template;
    setValue(prev => (prev ? prev + "\n" + t : t));
  }
  function run() {
    const t = selected.template;
    setValue(t);
    onRun?.();
  }
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <select value={sel} onChange={(e) => setSel(e.target.value)} style={{ padding: 6, borderRadius: 6 }}>
        {PRESETS.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
      </select>
      <button onClick={insert} disabled={disabled}>Insert</button>
      <button onClick={run} disabled={disabled}>Run</button>
    </div>
  );
}

