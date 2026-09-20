import React, { useState } from "react";
import ModelSelector from "../components/ModelSelector";
import MarvenChatNonStream from "../components/MarvenChatNonStream";
import SettingsPanel from "../components/SettingsPanel";

export default function Home() {
  const [model, setModel] = useState("mistral");
  const [showSettings, setShowSettings] = useState(false);
  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <ModelSelector selectedModel={model} onChange={setModel} />
        <button onClick={() => setShowSettings((v) => !v)} style={{ border: "1px solid #ccc", borderRadius: 6, padding: "6px 10px", background: "white" }}>{showSettings ? "Hide Settings" : "Show Settings"}</button>
      </div>
      {showSettings && (
        <div style={{ marginTop: 10 }}>
          <SettingsPanel />
        </div>
      )}
      <MarvenChatNonStream model={model} />
    </div>
  );
}
