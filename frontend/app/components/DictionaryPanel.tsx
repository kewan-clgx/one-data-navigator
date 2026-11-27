"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fieldPath, fillRate, profileField, ProfileStatus, useColumnProfile } from "./ColumnProfile";

/** "3 days ago" style, with the absolute time kept for the tooltip. */
function relative(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [[60, "second"], [60, "minute"], [24, "hour"], [30, "day"], [12, "month"]];
  let v = -s;
  for (const [n, unit] of steps) {
    if (Math.abs(v) < n) return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(Math.round(v), unit);
    v /= n;
  }
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(Math.round(v), "year");
}

/** Field names break after underscores (business_status_code), never mid-word. */
function breakable(name: string) {
  return name.split("_").flatMap((part, i, all) => (i < all.length - 1 ? [part, "_", <wbr key={i} />] : [part]));
}

function matches(f: FieldEntry, tokens: string[]): boolean {
  const hay = `${f.name} ${f.source_description ?? ""} ${f.curated?.definition ?? ""}`.toLowerCase();
  return tokens.every((t) => hay.includes(t));
}

type Curated = { definition: string; status: "draft" | "approved" | "deprecated"; updated_by: string | null; updated_at: string };
type FieldEntry = { name: string; type: string; source_description: string | null; curated: Curated | null };
type Dictionary = {
  table_path: string;
  source: "ok" | "not_found" | "permission_denied" | "unavailable";
  source_description: string | null;
  curated: Curated | null;
  fields: FieldEntry[];
};

const SOURCE_NOTES: Record<Dictionary["source"], string | null> = {
  ok: null,
  not_found: "The table was not found in the warehouse; showing curated definitions only.",
  permission_denied: "No permission to read warehouse metadata for this table; showing curated definitions only.",
  unavailable: "Warehouse metadata is temporarily unavailable; showing curated definitions only.",
};

function CuratedText({ c }: { c: Curated | null }) {
  if (!c) return <span className="muted">Definition unavailable</span>;
  return (
    <>
      {c.definition}{" "}
      <span className={`badge status-${c.status}`}>{c.status}</span>
      <span className="muted provenance">
        {" "}
        · {c.updated_by ?? "unknown"}, {new Date(c.updated_at).toLocaleDateString()}
      </span>
    </>
  );
}

function Editor({
  initial,
  onSave,
  onCancel,
  label,
}: {
  initial: Curated | null;
  onSave: (definition: string, status: Curated["status"]) => Promise<void>;
  onCancel: () => void;
  label: string;
}) {
  const [text, setText] = useState(initial?.definition ?? "");
  const [status, setStatus] = useState<Curated["status"]>(initial?.status ?? "draft");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => ref.current?.focus(), []);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(text.trim(), status);
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  };

  return (
    <div className="def-editor" role="group" aria-label={label}>
      <textarea
        ref={ref}
        rows={3}
        value={text}
        maxLength={4000}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onCancel()}
        aria-label={`${label} text`}
      />
      <div className="def-actions">
        <label>
          Status{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value as Curated["status"])}>
            <option value="draft">draft</option>
            <option value="approved">approved</option>
            <option value="deprecated">deprecated</option>
          </select>
        </label>
        <button type="button" onClick={save} disabled={saving || !text.trim()}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        {error && <span role="alert" className="error">{error}</span>}
      </div>
    </div>
  );
}

export default function DictionaryPanel({
  connectionId,
  path,
  kind = "table",
  rowCount,
  sizeLabel,
  modifiedAt,
  partitioning,
  clustering,
  canEdit,
  focusColumn,
  initialFilter = "",
  onInsertField,
  compact = false,
}: {
  connectionId: string;
  path: string[];
  kind?: "table" | "view";
  rowCount: number | null | undefined;
  /** Formatted size, or null when unknown (views report none). */
  sizeLabel: string | null;
  modifiedAt?: string | null;
  partitioning?: string | null;
  clustering?: string[] | null;
  canEdit: boolean;
  focusColumn: string | null;
  /** Pre-fills the field filter (e.g. the Explorer search), when it matches any field. */
  initialFilter?: string;
  /** Click on a field name: insert it into the SQL editor. */
  onInsertField?: (name: string) => void;
  /** Narrow side pane: drop the Range column (shown as a tooltip on Fill). */
  compact?: boolean;
}) {
  const [dict, setDict] = useState<Dictionary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [filter, setFilter] = useState(initialFilter);
  const [copied, setCopied] = useState(false);
  const focusRow = useRef<HTMLTableRowElement | null>(null);
  const query = path.map((p) => `path=${encodeURIComponent(p)}`).join("&");
  const { profile, error: profileError, disabled: profileDisabled, requesting, request } =
    useColumnProfile(connectionId, path);

  const load = useCallback(() => {
    setError(null);
    fetch(`/api/odn/connections/${connectionId}/dictionary?${query}`)
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${r.status}`);
        setDict(body);
      })
      .catch((e: Error) => setError(e.message));
  }, [connectionId, query]);

  useEffect(() => {
    setDict(null);
    setEditing(null);
    load();
  }, [load]);

  useEffect(() => {
    focusRow.current?.scrollIntoView({ block: "center" });
  }, [dict, focusColumn]);

  const save = (field: string) => async (definition: string, status: Curated["status"]) => {
    const r = await fetch(`/api/odn/connections/${connectionId}/definitions`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ table_path: path.join("."), field_path: field, definition, status }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Save failed (HTTP ${r.status})`);
    setEditing(null);
    load();
  };

  const curatedCount = dict ? dict.fields.filter((f) => f.curated).length : 0;
  const tokens = useMemo(() => filter.toLowerCase().split(/\s+/).filter(Boolean), [filter]);
  const matched = dict ? dict.fields.filter((f) => matches(f, tokens)) : [];
  // A carried-over Explorer search that matches nothing here shouldn't hide every field.
  const shownFields = dict && !matched.length && filter === initialFilter ? dict.fields : matched;
  const name = path[path.length - 1];
  const identity = [
    kind === "view" ? "View" : "Table",
    rowCount ? `${rowCount.toLocaleString()} rows` : null,
    sizeLabel,
    partitioning ? `Partitioned by ${partitioning}` : null,
    clustering?.length ? `Clustered by ${clustering.join(", ")}` : null,
  ].filter(Boolean);
  const profiling = profile?.job_status === "queued" || profile?.job_status === "running";
  const profileProblem = profileError || profile?.error_code || profile?.credential_changed;

  return (
    <section aria-label="Table schema and data dictionary" className="dict">
      <header className="dict-head">
        <h2 title={path.join(".")}>{name}</h2>
        <div className="dict-path">
          <code>{path.slice(0, -1).join(".")}</code>
          <button
            type="button"
            className="link"
            onClick={() => void navigator.clipboard?.writeText(path.join(".")).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            })}
            aria-label={`Copy full path ${path.join(".")}`}
          >
            {copied ? "Copied" : "Copy path"}
          </button>
        </div>
        <p className="dict-identity">
          {identity.join(" · ")}
          {modifiedAt && (
            <>
              {" · "}
              <span title={new Date(modifiedAt).toLocaleString()}>Modified {relative(modifiedAt)}</span>
            </>
          )}
          {dict && ` · ${curatedCount} of ${dict.fields.length} fields defined`}
        </p>
      </header>
      {error && <p role="alert" className="error">{error}</p>}
      {dict && SOURCE_NOTES[dict.source] && <p className="muted">{SOURCE_NOTES[dict.source]}</p>}
      {dict && (
        <div className="table-defs">
          <p>
            <strong>Source description:</strong>{" "}
            {dict.source_description ?? <span className="muted">None in the warehouse</span>}
          </p>
          {editing === "" ? (
            <Editor initial={dict.curated} onSave={save("")} onCancel={() => setEditing(null)} label="Table business definition" />
          ) : (
            <p>
              <strong>Business definition:</strong> <CuratedText c={dict.curated} />{" "}
              {canEdit && (
                <button type="button" className="link" onClick={() => setEditing("")}>
                  {dict.curated ? "Edit" : "Add"}
                </button>
              )}
            </p>
          )}
        </div>
      )}
      {dict && (
        <div className="dict-fields">
          <div className="dict-fields-head">
            <h3>
              Fields <span className="muted">({filter && shownFields !== dict.fields ? `${shownFields.length} of ${dict.fields.length}` : dict.fields.length})</span>
            </h3>
            <input
              type="search"
              className="dict-filter"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter fields"
              aria-label="Filter fields by name or definition"
            />
            {!profileDisabled && (
              <span className="dict-profile muted" aria-live="polite">
                {profiling
                  ? "Profiling…"
                  : profile?.sampled_at
                    ? <span title={new Date(profile.sampled_at).toLocaleString()}>Profile: 10% sample · {relative(profile.sampled_at)}</span>
                    : "No profile yet"}{" "}
                <button type="button" className="link" onClick={request} disabled={requesting || profiling}
                        title="Estimate fill rates and ranges from a 10% sample of the table">
                  {requesting ? "Requesting…" : profile?.sampled_at ? "↻ Refresh profile" : "Profile this table"}
                </button>
              </span>
            )}
          </div>
          {!profileDisabled && profileProblem && <ProfileStatus profile={profile} error={profileError} />}
          {!shownFields.length && <p className="muted">No fields match “{filter}”.</p>}
          <table className={`grid dictionary dict-table${compact ? " dict-table--compact" : ""}`}>
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Type</th>
                {!profileDisabled && <th scope="col">Fill</th>}
                {!profileDisabled && !compact && <th scope="col">Range</th>}
              </tr>
            </thead>
            <tbody>
              {shownFields.map((f) => {
                // Nested field matches (a.b) focus their top-level column.
                const focused = !!focusColumn && (focusColumn === f.name || focusColumn.startsWith(`${f.name}.`));
                const field = profileField(profile, f.name, f.type);
                const range = field?.min_value != null && field.max_value != null ? `${field.min_value} – ${field.max_value}` : null;
                const pct = field && profile?.sample_rows ? (100 * field.filled_rows) / profile.sample_rows : null;
                return (
                  <tr key={f.name} ref={focused ? focusRow : undefined} className={focused ? "focused" : undefined}>
                    <td className="dict-field-cell">
                      {onInsertField ? (
                        <button type="button" className="dict-field" onClick={() => onInsertField(f.name)}
                                title="Insert into the SQL editor at the cursor">
                          <code>{breakable(f.name)}</code>
                        </button>
                      ) : <code>{breakable(f.name)}</code>}
                      {editing === f.name ? (
                        <Editor
                          initial={f.curated}
                          onSave={save(f.name)}
                          onCancel={() => setEditing(null)}
                          label={`Business definition for ${f.name}`}
                        />
                      ) : (f.curated || canEdit) && (
                        <span className={`dict-desc dict-def${f.curated ? "" : " dict-def--add"}`}>
                          {f.curated && <CuratedText c={f.curated} />}{" "}
                          {canEdit && (
                            <button
                              type="button"
                              className="link"
                              onClick={() => setEditing(f.name)}
                              aria-label={`${f.curated ? "Edit" : "Add"} business definition for ${f.name}`}
                            >
                              {f.curated ? "Edit" : "Add definition"}
                            </button>
                          )}
                        </span>
                      )}
                      {f.source_description && <span className="dict-desc">{f.source_description}</span>}
                    </td>
                    <td className="dict-type">{f.type}</td>
                    {!profileDisabled && (
                      <td className="dict-fill" title={compact && range ? `Range ${range}` : undefined}>
                        {pct == null ? <span className="muted">–</span> : (
                          <span className="fill" aria-label={`${pct.toFixed(1)}% filled`}>
                            <span className="fill-bar" aria-hidden><span style={{ width: `${Math.max(2, pct)}%` }} /></span>
                            {fillRate(field, profile)}
                          </span>
                        )}
                      </td>
                    )}
                    {!profileDisabled && !compact && <td className="dict-range">{range ?? <span className="muted">–</span>}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {profile && profile.fields.some((field) => field.path.length > 1) && (
        <details>
          <summary>Nested field profiles</summary>
          <table className="grid dictionary">
            <thead><tr><th scope="col">Field</th><th scope="col">Type</th><th scope="col">Fill rate</th><th scope="col">Range</th></tr></thead>
            <tbody>
              {profile.fields.filter((field) => field.path.length > 1).map((field) => (
                <tr key={JSON.stringify(field.path)}>
                  <td><code>{fieldPath(field)}</code></td>
                  <td>{field.data_type}</td>
                  <td>{fillRate(field, profile)}</td>
                  <td>{field.min_value != null && field.max_value != null ? `${field.min_value} – ${field.max_value}` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}
