"use client";

import { createContext, isValidElement, useContext, useState, type ReactElement, type ReactNode } from "react";

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
 * them (copy and download stay inside) or open the SQL in the workbench to edit and run it without the AI.
 */
// react-markdown passes `node`; keep it off the DOM element.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function FoldableSql({ children, node: _node, ...rest }: { children?: ReactNode; node?: unknown }) {
  const openSql = useContext(OpenSqlContext);
  const [open, setOpen] = useState(false);
  const code = codeOf(children);
  if (!code || code.language !== "sql") return <pre {...rest}>{children}</pre>;

  const lines = code.text.split("\n").length;
  return (
    <div className="eq-sql">
      <div className="eq-sql-bar">
        <button type="button" className="eq-sql-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <span aria-hidden className="eq-sql-chevron">{open ? "▾" : "▸"}</span>
          {open ? "Hide SQL" : "Show SQL"}
          <span className="eq-sql-meta">{lines} {lines === 1 ? "line" : "lines"}</span>
        </button>
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
      </div>
      {open && <pre {...rest}>{children}</pre>}
    </div>
  );
}
