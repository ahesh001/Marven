import React from "react";
import MarkdownMessage from "./MarkdownMessage";
import { formatDistanceToNow } from "date-fns";

export default function ChatBubble({ role, content, streaming, ts, onCopy, onRetry }) {
  const isUser = role === "user";
  const containerStyle = {
    width: "100%",
    display: "flex",
    justifyContent: isUser ? "flex-end" : "flex-start",
    margin: "12px 0",
  };
  const bubbleStyle = {
    background: isUser ? "#1e40af" : "#ffffff",
    color: isUser ? "#ffffff" : "#111827",
    maxWidth: "72ch",
    whiteSpace: "pre-wrap",
    borderRadius: 16,
    padding: "10px 12px",
    boxShadow: "0 1px 2px rgba(0,0,0,0.06)",
    lineHeight: 1.7,
    wordBreak: "break-word",
    border: isUser ? "none" : "1px solid #e5e7eb",
    position: "relative",
  };
  const toolbarStyle = {
    display: "flex",
    gap: 8,
    marginTop: 6,
    justifyContent: isUser ? "flex-end" : "flex-start",
  };
  const tsText = ts ? formatDistanceToNow(new Date(ts), { addSuffix: true }) : "";

  return (
    <div style={containerStyle}>
      <div style={bubbleStyle}>
        <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 4 }}>
          {isUser ? "You" : "Marven"} {tsText ? `• ${tsText}` : ""}
        </div>
        <div style={{ filter: isUser ? "brightness(0.96)" : "none" }}>
          {isUser ? (
            <div>{content}</div>
          ) : (
            <MarkdownMessage text={content} />
          )}
        </div>
        {!isUser && streaming ? (
          <div style={{ marginTop: 8, fontSize: 12, opacity: 0.6 }}>…streaming</div>
        ) : null}
        <div style={toolbarStyle}>
          <button
            type="button"
            onClick={() => (onCopy ? onCopy(String(content || "")) : navigator.clipboard.writeText(String(content || "")))}
            style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: "4px 8px", background: "#fff", cursor: "pointer", fontSize: 12 }}
          >
            Copy
          </button>
          {!isUser && (
            <button
              type="button"
              onClick={() => onRetry && onRetry()}
              style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: "4px 8px", background: "#fff", cursor: "pointer", fontSize: 12 }}
            >
              Regenerate
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

