"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { fieldPath, fillRate, profileField, ProfileStatus, useColumnProfile } from "./ColumnProfile";

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
  rowCount,
  sizeLabel,
  canEdit,
  focusColumn,
}: {
  connectionId: string;
  path: string[];
  rowCount: number | null | undefined;
  sizeLabel: string;
  canEdit: boolean;
  focusColumn: string | null;
}) {
  const [dict, setDict] = useState<Dictionary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
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

  return (
    <section aria-label="Table schema and data dictionary">
      <h2>{path.join(".")}</h2>
      <p className="muted">
        {rowCount?.toLocaleString() ?? "?"} rows · {sizeLabel}
        {dict && ` · ${curatedCount} of ${dict.fields.length} fields have a business definition`}
      </p>
      {error && <p role="alert" className="error">{error}</p>}
      {dict && SOURCE_NOTES[dict.source] && <p className="muted">{SOURCE_NOTES[dict.source]}</p>}
      {!profileDisabled && (
        <div>
          <button type="button" onClick={request} disabled={requesting || profile?.job_status === "queued" || profile?.job_status === "running"}>
            {requesting ? "Requesting…" : "Profile"}
          </button>
          <ProfileStatus profile={profile} error={profileError} />
        </div>
      )}
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
        <details key={`${path.join(".")}:${focusColumn ?? ""}`} open={focusColumn ? true : undefined}>
          <summary>{dict.fields.length} fields</summary>
          <table className="grid dictionary">
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Type</th>
                {!profileDisabled && <th scope="col">Fill rate</th>}
                {!profileDisabled && <th scope="col">Range</th>}
                <th scope="col">Source description</th>
                <th scope="col">Business definition</th>
              </tr>
            </thead>
            <tbody>
              {dict.fields.map((f) => {
                // Nested field matches (a.b) focus their top-level column.
                const focused = !!focusColumn && (focusColumn === f.name || focusColumn.startsWith(`${f.name}.`));
                const field = profileField(profile, f.name, f.type);
                return (
                  <tr key={f.name} ref={focused ? focusRow : undefined} className={focused ? "focused" : undefined}>
                    <td><code>{f.name}</code></td>
                    <td>{f.type}</td>
                    {!profileDisabled && <td>{fillRate(field, profile)}</td>}
                    {!profileDisabled && <td>{field?.min_value != null && field.max_value != null ? `${field.min_value} – ${field.max_value}` : "–"}</td>}
                    <td>{f.source_description ?? <span className="muted">None</span>}</td>
                    <td className="def-cell">
                      {editing === f.name ? (
                        <Editor
                          initial={f.curated}
                          onSave={save(f.name)}
                          onCancel={() => setEditing(null)}
                          label={`Business definition for ${f.name}`}
                        />
                      ) : (
                        <>
                          <CuratedText c={f.curated} />{" "}
                          {canEdit && (
                            <button
                              type="button"
                              className="link"
                              onClick={() => setEditing(f.name)}
                              aria-label={`${f.curated ? "Edit" : "Add"} business definition for ${f.name}`}
                            >
                              {f.curated ? "Edit" : "Add"}
                            </button>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
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
