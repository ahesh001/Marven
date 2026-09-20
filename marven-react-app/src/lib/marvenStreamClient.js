function effectiveApiBase() {
  try {
    const v = (typeof window !== 'undefined') ? window.localStorage.getItem('API_BASE') : null;
    if (v && v.trim()) return v.trim();
  } catch {}
  return process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000";
}

export async function* streamMarven(input, { model, sessionId, autoApply, selfAware, messageId, signal } = {}) {
  const base = effectiveApiBase();
  const res = await fetch(`${base}/api/respond_stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, model, sessionId, autoApply, selfAware, messageId }),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`API error ${res.status}: ${text || res.statusText}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line) continue;
      let evt;
      try { evt = JSON.parse(line); } catch { continue; }
      if (evt && evt.type === 'status') {
        try { window.dispatchEvent(new CustomEvent('marven-status', { detail: String(evt.data || '') })); } catch {}
      } else if (evt && evt.type === 'done') {
        try { window.dispatchEvent(new CustomEvent('marven-status', { detail: '' })); } catch {}
      }
      yield evt;
    }
  }
}

export async function* streamVisionMarven(input, imagesBase64 = [], { model = "llava", sessionId, selfAware, messageId, signal } = {}) {
  const base = effectiveApiBase();
  const res = await fetch(`${base}/api/vision_stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, images: imagesBase64, model, sessionId, selfAware, messageId }),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`Vision stream error ${res.status}: ${text || res.statusText}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line) continue;
      let evt;
      try { evt = JSON.parse(line); } catch { continue; }
      if (evt && evt.type === 'status') {
        try { window.dispatchEvent(new CustomEvent('marven-status', { detail: String(evt.data || '') })); } catch {}
      } else if (evt && evt.type === 'done') {
        try { window.dispatchEvent(new CustomEvent('marven-status', { detail: '' })); } catch {}
      }
      yield evt;
    }
  }
}

export async function* streamAnalyzeFiles(input, files = [], { model, sessionId, selfAware, messageId, signal } = {}) {
  const base = effectiveApiBase();
  const res = await fetch(`${base}/api/analyze_files_stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, files, model, sessionId, selfAware, messageId }),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`Analyze files stream error ${res.status}: ${text || res.statusText}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line) continue;
      let evt;
      try { evt = JSON.parse(line); } catch { continue; }
      if (evt && evt.type === 'status') {
        try { window.dispatchEvent(new CustomEvent('marven-status', { detail: String(evt.data || '') })); } catch {}
      } else if (evt && evt.type === 'done') {
        try { window.dispatchEvent(new CustomEvent('marven-status', { detail: '' })); } catch {}
      }
      yield evt;
    }
  }
}
