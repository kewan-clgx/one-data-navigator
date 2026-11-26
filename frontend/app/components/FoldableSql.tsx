"use client";

import dynamic from "next/dynamic";
import { createContext, isValidElement, useContext, useState, type ReactElement, type ReactNode } from "react";

// Parser + highlighter load only when someone expands a SQL block.
const SqlCode = dynamic(() => import("./SqlCode"), {
  ssr: false,
  loading: () => <pre className="sqlh-block" aria-busy="true" />,
});

/** Opens the SQL workbench with this SQL (provided by AppShell). */
export const OpenSqlContext = createContext<((sql: string) => void) | null>(null);

type CodeProps = { className?: string; children?: ReactNode };

function codeOf(children: ReactNode): { language: string; text: string } | null {
  const child = (Array.isArray(children) ? children[0] : children) as ReactElement<CodeProps> | undefined;
  if (!isValidElement(child)) return null;
  const { className, children: inner } = child.props as CodeProps;
  const language = /language-(\w+)/.exec(className ?? "")?.[1]?.toLowerCase() ?? "";
  const text = (Array.isArray(inner) ? inner.join("") : String(inner ?? "")).replace(/\n$/, "");
  return { language, text };
}

/**
 * Chat markdown `pre`: SQL blocks start folded (most readers are managers, not SQL users). Analysts can expand
 * them (highlighted like the workbench editor, with copy and download) or open the SQL in the workbench to edit and
 * run it without the AI.
 */
// react-markdown passes `node`; keep it off the DOM element.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function FoldableSql({ children, node: _node, ...rest }: { children?: ReactNode; node?: unknown }) {
  const openSql = useContext(OpenSqlContext);
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const code = codeOf(children);
  if (!code || code.language !== "sql") return <pre {...rest}>{children}</pre>;

  const lines = code.text.split("\n").length;
  const copy = () => {
    void navigator.clipboard.writeText(code.text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([`${code.text}\n`], { type: "application/sql" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "query.sql" });
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="eq-sql">
      <div className="eq-sql-bar">
        <button type="button" className="eq-sql-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <span aria-hidden className="eq-sql-chevron">{open ? "▾" : "▸"}</span>
          {open ? "Hide SQL" : "Show SQL"}
          <span className="eq-sql-meta">{lines} {lines === 1 ? "line" : "lines"}</span>
        </button>
        <span className="eq-sql-actions">
          {open && (
            <>
              <button type="button" className="eq-sql-icon" onClick={copy} title={copied ? "Copied" : "Copy SQL"} aria-label={copied ? "Copied" : "Copy SQL"}>
                {copied ? <CheckIcon /> : <CopyIcon />}
              </button>
              <button type="button" className="eq-sql-icon" onClick={download} title="Download .sql" aria-label="Download .sql">
                <DownloadIcon />
              </button>
            </>
          )}
          {openSql && (
            <button
              type="button"
              className="odn-btn odn-btn--outline odn-btn--small"
              onClick={() => openSql(code.text)}
              title="Edit and run this SQL yourself. Running it in the workbench doesn't use the AI."
            >
              Open in SQL workbench
            </button>
          )}
        </span>
      </div>
      {open && <SqlCode text={code.text} label="SQL" />}
    </div>
  );
}

const iconProps = { width: 14, height: 14, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.5, "aria-hidden": true } as const;

function CopyIcon() {
  return (
    <svg {...iconProps}>
      <rect x="5.5" y="5.5" width="8" height="8" />
      <path d="M10.5 3.5v-1h-8v8h1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg {...iconProps}>
      <path d="M3 8.5l3 3 7-7" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg {...iconProps}>
      <path d="M8 2.5v8M4.5 7L8 10.5 11.5 7M3 13.5h10" />
    </svg>
  );
}
