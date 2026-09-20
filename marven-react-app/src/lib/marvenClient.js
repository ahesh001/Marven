function effectiveApiBase() {
  try {
    const v = (typeof window !== 'undefined') ? window.localStorage.getItem('API_BASE') : null;
    if (v && v.trim()) return v.trim();
  } catch {}
  return process.env.REACT_APP_API_BASE || "http://127.0.0.1:8000";
}

export async function askMarven(input, { model, sessionId, autoApply, selfAware, signal } = {}) {
  const base = effectiveApiBase();
  const res = await fetch(`${base}/api/respond`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, model, sessionId, autoApply, selfAware }),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API error ${res.status}: ${text || res.statusText}`);
  }
  const data = await res.json();
  return data.output ?? "";
}

export async function askMarvenVision(input, imagesBase64 = [], { model = "llava", sessionId, selfAware, signal } = {}) {
  const base = effectiveApiBase();
  const res = await fetch(`${base}/api/vision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, images: imagesBase64, model, sessionId, selfAware }),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Vision API error ${res.status}: ${text || res.statusText}`);
  }
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data.output ?? "";
}

export async function pullOllamaModel(model = "llava") {
  const base = effectiveApiBase();
  const res = await fetch(`${base}/api/ollama/pull`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  const data = await res.json();
  if (!res.ok || data.status !== "ok") {
    throw new Error(data.error || data.message || "Failed to pull model");
  }
  return data;
}

export async function askMarvenAnalyzeFiles(input, files = [], { model, sessionId, selfAware, signal } = {}) {
  const base = effectiveApiBase();
  const res = await fetch(`${base}/api/analyze_files`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, files, model, sessionId, selfAware }),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Analyze files error ${res.status}: ${text || res.statusText}`);
  }
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data.output ?? "";
}

export async function listFiles(path = ".") {
  const base = effectiveApiBase();
  const url = new URL(`${base}/api/fs/list`);
  url.searchParams.set("path", path);
  const res = await fetch(url.toString());
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data; // { path, entries: [{name,isDir,size,path}] }
}

export async function readFile(path) {
  const base = effectiveApiBase();
  const url = new URL(`${base}/api/fs/read`);
  url.searchParams.set("path", path);
  const res = await fetch(url.toString());
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data.content || "";
}

export async function writeFile(path, content) {
  const base = effectiveApiBase();
  const res = await fetch(`${base}/api/fs/write`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
