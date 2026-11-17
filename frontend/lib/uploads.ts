/** Browser-side file uploads into the artifacts store (CSV/XLSX also become workspace tables). */

// Matches the API (odn.platform.workspace.uploads).
export const UPLOAD_MAX_BYTES = 20 * 1024 * 1024;
export const MIME_BY_EXTENSION: Record<string, string> = {
  ".csv": "text/csv",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".pdf": "application/pdf",
  ".txt": "text/plain",
  ".md": "text/markdown",
  ".json": "application/json",
};
export const UPLOAD_ACCEPT = Object.keys(MIME_BY_EXTENSION).join(",");
// Browsers report some types inconsistently (empty for .md/.xlsx), so the chat matches either form.
export const CHAT_ATTACH_ACCEPT = [...new Set([...Object.keys(MIME_BY_EXTENSION), ...Object.values(MIME_BY_EXTENSION)])].join(",");

function mimeOf(file: File): string {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  return MIME_BY_EXTENSION[ext] ?? (file.type || "application/octet-stream");
}

/** Fired after an upload so the Artifacts tab and the chat's file context refresh. */
export const FILES_CHANGED = "odn:files-changed";

export type UploadResult = {
  file: { id: string; name: string; table_name: string | null };
  table: { artifact: { name: string; row_count: number } } | null;
};

export async function uploadFile(sessionId: string, file: File): Promise<UploadResult> {
  if (file.size > UPLOAD_MAX_BYTES) throw new Error(`${file.name} is larger than 20 MB.`);
  const res = await fetch(
    `/api/odn/workspace/${encodeURIComponent(sessionId)}/files?filename=${encodeURIComponent(file.name)}`,
    { method: "POST", headers: { "content-type": file.type || "application/octet-stream" }, body: file },
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Upload failed (HTTP ${res.status})`);
  window.dispatchEvent(new Event(FILES_CHANGED));
  return body as UploadResult;
}

export function describeUpload(result: UploadResult): string {
  return result.table
    ? `${result.file.name} uploaded as table ${result.table.artifact.name} (${result.table.artifact.row_count.toLocaleString()} rows).`
    : `${result.file.name} uploaded to Artifacts.`;
}

/**
 * Chat paperclip: stores the file in Artifacts and attaches only a reference to the message. The agent
 * replaces the reference with a short note (table name, columns, sample rows) before calling the model.
 */
export async function uploadChatAttachment(sessionId: string, file: File) {
  const result = await uploadFile(sessionId, file);
  const table = result.table ? `&table=${encodeURIComponent(result.table.artifact.name)}` : "";
  return {
    type: "url" as const,
    value: `/api/odn/artifacts/files/${encodeURIComponent(result.file.id)}?inline=true${table}`,
    mimeType: mimeOf(file),
  };
}
