"use client";

import { createContext, useContext, type ReactNode } from "react";

/** Fills (never sends) the chat input with a question (provided by AppShell). */
export const AskContext = createContext<((question: string) => void) | null>(null);

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node && typeof node === "object" && "props" in node) {
    return textOf((node as { props: { children?: ReactNode } }).props.children);
  }
  return "";
}

/**
 * Chat markdown `a`: suggested follow-up questions are written as `[question](#ask)`; those render as buttons
 * that fill the message box, so the user can edit or just press Enter. Other links open in a new tab.
 */
// react-markdown passes `node`; keep it off the DOM element.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function AskLink({ href, children, node: _node, ...rest }: { href?: string; children?: ReactNode; node?: unknown }) {
  const ask = useContext(AskContext);
  // `#ask` asks the link text; `#ask=<encoded question>` asks that question (e.g. "Ask again for a fresh answer").
  const match = href ? /#ask(?:=(.*))?$/.exec(href) : null;
  if (match && ask) {
    let question = textOf(children).trim();
    try {
      if (match[1]) question = decodeURIComponent(match[1]);
    } catch {
      // keep the link text
    }
    return (
      <button type="button" className="ask-link" title="Put this question in the message box" onClick={() => ask(question)}>
        {children}
        <span className="ask-link-icon" aria-hidden="true">↵</span>
      </button>
    );
  }
  return <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>{children}</a>;
}
