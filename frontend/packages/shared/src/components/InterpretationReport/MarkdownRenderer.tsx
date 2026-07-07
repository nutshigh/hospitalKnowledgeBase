import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function MarkdownRenderer({ children }: { children: string }) {
  return (
    <div className="interp-md" style={{ fontSize: 13, lineHeight: 1.7, color: "var(--color-text-secondary, #555)" }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ node, ...props }) => <p style={{ margin: "4px 0" }} {...props} />,
          h1: ({ node, ...props }) => <h3 style={{ fontSize: 14, margin: "12px 0 4px" }} {...props} />,
          h2: ({ node, ...props }) => <h3 style={{ fontSize: 14, margin: "12px 0 4px" }} {...props} />,
          h3: ({ node, ...props }) => <h4 style={{ fontSize: 13, margin: "8px 0 4px" }} {...props} />,
          ul: ({ node, ...props }) => <ul style={{ margin: "4px 0 4px 18px" }} {...props} />,
          ol: ({ node, ...props }) => <ol style={{ margin: "4px 0 4px 18px" }} {...props} />,
          code: ({ node, ...props }) => <code style={{ background: "#f5f5f5", padding: "1px 4px", borderRadius: 4 }} {...props} />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
