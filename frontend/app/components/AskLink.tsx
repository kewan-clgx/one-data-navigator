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
 * Chat markdown `a`: the agent writes suggested follow-up questions as `[question](#ask)`; those render as buttons
 * that fill the message box, so the user can edit or just press Enter. Other links open in a new tab.
 */
// react-markdown passes `node`; keep it off the DOM element.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function AskLink({ href, children, node: _node, ...rest }: { href?: string; children?: ReactNode; node?: unknown }) {
  const ask = useContext(AskContext);
  if (href?.endsWith("#ask") && ask) {
    const question = textOf(children).trim();
    return (
      <button type="button" className="ask-link" title="Put this question in the message box" onClick={() => ask(question)}>
        {children}
        <span className="ask-link-icon" aria-hidden="true">↵</span>
      </button>
    );
  }
  return <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>{children}</a>;
}
