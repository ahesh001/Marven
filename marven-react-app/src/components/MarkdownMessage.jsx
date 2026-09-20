import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import DOMPurify from "dompurify";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";

export default function MarkdownMessage({ text = "" }) {
  const safe = useMemo(() => DOMPurify.sanitize(String(text || "")), [text]);
  return (
    <div className="prose" style={{ maxWidth: "none", lineHeight: 1.7 }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={{
          code({ inline, className, children, ...props }) {
            const m = /language-(\w+)/.exec(className || "");
            if (inline) {
              return (
                <code
                  className={className}
                  style={{ background: "#f3f4f6", padding: "0 4px", borderRadius: 4 }}
                  {...props}
                >
                  {children}
                </code>
              );
            }
            const lang = m?.[1] || "text";
            const codeStr = String(children).replace(/\n$/, "");
            return (
              <div className="codeblock" style={{ position: "relative" }}>
                <button
                  onClick={() => navigator.clipboard.writeText(codeStr)}
                  style={{
                    position: "absolute",
                    right: 8,
                    top: 8,
                    fontSize: 12,
                    border: "1px solid #ddd",
                    borderRadius: 6,
                    padding: "4px 8px",
                    background: "#fff",
                    cursor: "pointer",
                  }}
                >
                  Copy
                </button>
                <SyntaxHighlighter language={lang} PreTag="div" {...props}>
                  {codeStr}
                </SyntaxHighlighter>
              </div>
            );
          },
        }}
      >
        {safe}
      </ReactMarkdown>
    </div>
  );
}

