import React from "react";

function ModelSelector({ selectedModel, onChange }) {
  const isMarven = String(selectedModel || '').toLowerCase() === 'marven';
  const createCmd = `ollama create marven -f ./ollama/Modelfile.marven`;
  const createCmdL3 = `ollama create marven -f ./ollama/Modelfile.marven.llama3`;
  const runCmd = `ollama run marven "Hello Marven!"`;
  const copy = (text) => {
    try { navigator.clipboard.writeText(text); alert('Copied to clipboard'); } catch {}
  };
  return (
    <div className="flex flex-col gap-2 p-2">
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium">Select Model (Ollama):</label>
        <select
          value={selectedModel}
          onChange={(e) => onChange(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm"
        >
          <option value="mistral">Creative (mistral)</option>
          <option value="phi3">Fast (phi3)</option>
          <option value="llama3:8b">Code (llama3:8b)</option>
          <option value="marven">Marven (custom, if created)</option>
        </select>
      </div>
      {isMarven && (
        <div style={{ border: '1px solid #ddd', borderRadius: 8, padding: 10, background: '#fafafa' }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Create local Marven model (Ollama)</div>
          <div style={{ fontSize: 13, color: '#333' }}>Use the provided Modelfiles to create your own Marven model locally.</div>
          <ol style={{ marginTop: 8, paddingLeft: 18, fontSize: 13, color: '#222' }}>
            <li>Open a terminal in your project root.</li>
            <li>Build the model from Mistral base:
              <div style={{ display:'flex', gap:8, alignItems:'center', marginTop:4 }}>
                <code style={{ background:'#fff', border:'1px solid #eee', padding:'2px 6px', borderRadius:6 }}>{createCmd}</code>
                <button onClick={() => copy(createCmd)} style={{ border:'1px solid #ccc', borderRadius:6, padding:'2px 8px', background:'#fff' }}>Copy</button>
              </div>
            </li>
            <li>Or build from Llama‑3 8B instruct:
              <div style={{ display:'flex', gap:8, alignItems:'center', marginTop:4 }}>
                <code style={{ background:'#fff', border:'1px solid #eee', padding:'2px 6px', borderRadius:6 }}>{createCmdL3}</code>
                <button onClick={() => copy(createCmdL3)} style={{ border:'1px solid #ccc', borderRadius:6, padding:'2px 8px', background:'#fff' }}>Copy</button>
              </div>
            </li>
            <li>Test it locally:
              <div style={{ display:'flex', gap:8, alignItems:'center', marginTop:4 }}>
                <code style={{ background:'#fff', border:'1px solid #eee', padding:'2px 6px', borderRadius:6 }}>{runCmd}</code>
                <button onClick={() => copy(runCmd)} style={{ border:'1px solid #ccc', borderRadius:6, padding:'2px 8px', background:'#fff' }}>Copy</button>
              </div>
            </li>
          </ol>
          <div style={{ fontSize: 12, color: '#666', marginTop: 6 }}>Tip: adjust the Modelfile to change style or base model. Files are under <code>ollama/</code>.</div>
        </div>
      )}
    </div>
  );
}

export default ModelSelector;
