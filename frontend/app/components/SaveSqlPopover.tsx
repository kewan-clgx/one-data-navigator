"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { fileName, NameTakenError } from "@/lib/savedSql";

/**
 * "Save as" / "Rename" popover for saved SQL: a file-style name (pre-filled, selected so typing replaces it) with a
 * fixed ".sql" suffix and an optional one-line description. A name clash offers the next free name.
 */
export default function SaveSqlPopover({
  title,
  initialName,
  initialDescription = "",
  withDescription = true,
  submitLabel = "Save",
  onSubmit,
  onClose,
}: {
  title: string;
  initialName: string;
  initialDescription?: string;
  withDescription?: boolean;
  submitLabel?: string;
  onSubmit: (name: string, description: string) => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);

  const clean = fileName(name);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!clean) {
      setError("Use letters, digits, '_' or '-' for the name.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit(clean, description.trim());
    } catch (err) {
      if (err instanceof NameTakenError) {
        setError(`${clean}.sql already exists. Use ${err.suggestion}.sql, or type another name.`);
        setName(err.suggestion);
        input.current?.focus();
      } else {
        setError((err as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="sqlsave"
      role="dialog"
      aria-label={title}
      onSubmit={submit}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <p className="sqlsave-title">{title}</p>
      <label className="sqlsave-field">
        <span>Name</span>
        <span className="sqlsave-name">
          <input
            ref={input}
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={90}
            spellCheck={false}
            aria-describedby="sqlsave-hint"
          />
          <span aria-hidden="true">.sql</span>
        </span>
      </label>
      <span id="sqlsave-hint" className="sqlsave-hint">
        {clean && clean !== name.trim().toLowerCase().replace(/\.sql$/, "") ? `Saved as ${clean}.sql` : "Letters, digits, _ and -"}
      </span>
      {withDescription && (
        <label className="sqlsave-field">
          <span>Description <span className="muted">(optional)</span></span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={300} placeholder="What it answers" />
        </label>
      )}
      {error && <p className="sqlsave-error" role="alert">{error}</p>}
      <div className="sqlsave-actions">
        <button type="button" className="sqled-btn" onClick={onClose}>Cancel</button>
        <button type="submit" className="sqled-run-btn" disabled={busy || !clean}>{busy ? "Saving…" : submitLabel}</button>
      </div>
    </form>
  );
}
