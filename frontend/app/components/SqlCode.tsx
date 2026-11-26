"use client";

import { useMemo } from "react";

import { highlightSql, type SqlDialectId } from "@/lib/sqlSyntax";

/** Read-only highlighted SQL with line numbers, using the workbench editor's highlighter and colours. */
export default function SqlCode({ text, dialect, label }: { text: string; dialect?: SqlDialectId; label?: string }) {
  const lines = useMemo(() => highlightSql(text, dialect), [text, dialect]);
  return (
    <pre className="sqlh-block" tabIndex={0} aria-label={label}>
      <code>
        {lines.map((tokens, i) => (
          <span key={i} className="sqlh-line">
            {tokens.map((tok, j) => (tok.className ? <span key={j} className={tok.className}>{tok.text}</span> : tok.text))}
          </span>
        ))}
      </code>
    </pre>
  );
}
