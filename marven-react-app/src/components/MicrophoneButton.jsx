import React, { useEffect, useRef, useState } from "react";

export default function MicrophoneButton({ onResult, onStart, onStop, disabled }) {
  const [listening, setListening] = useState(false);
  const recRef = useRef(null);

  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    const rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = true;
    rec.onstart = () => {
      setListening(true);
      onStart && onStart();
    };
    rec.onend = () => {
      setListening(false);
      onStop && onStop();
    };
    rec.onresult = (e) => {
      let finalText = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        if (res.isFinal) finalText += res[0].transcript;
      }
      if (finalText && onResult) onResult(finalText.trim());
    };
    recRef.current = rec;
    return () => {
      try {
        rec.stop();
      } catch {}
    };
  }, [onResult, onStart, onStop]);

  const toggle = () => {
    const rec = recRef.current;
    if (!rec || disabled) return;
    try {
      if (listening) {
        rec.stop();
      } else {
        rec.start();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const supported = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  return (
    <button onClick={toggle} disabled={!supported || disabled} style={{ padding: "8px 10px" }}>
      {supported ? (listening ? "Stop Mic" : "Start Mic") : "Mic N/A"}
    </button>
  );
}

