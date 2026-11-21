import { ImageResponse } from "next/og";

import { fetchPublicShare } from "@/lib/public-share";
import { plainText, questionAndAnswer, type PublicArtifact } from "@/lib/share";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "QueryMuse shared answer";
export const dynamic = "force-dynamic";

const INK = "#1f1a17";
const MUTED = "#6b625c";
const ACCENT = "#e2583e";
const PAPER = "#fbf7f2";

const label = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const short = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function Bars({ artifact }: { artifact: Extract<PublicArtifact, { kind: "chart" }> }) {
  const points = artifact.result.points
    .map((p) => ({ x: [label(p.x), label(p.series)].filter(Boolean).join(" · "), y: Number(p.y) }))
    .filter((p) => Number.isFinite(p.y))
    .slice(0, 6);
  const max = Math.max(...points.map((p) => p.y), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, width: "100%" }}>
      <div style={{ display: "flex", fontSize: 24, color: MUTED }}>{short(artifact.title || "Chart", 60)}</div>
      {points.map((p, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 14, height: 34 }}>
          <div style={{ display: "flex", width: 360, fontSize: 20, color: INK, whiteSpace: "nowrap", overflow: "hidden" }}>{short(p.x, 32)}</div>
          <div style={{ display: "flex", height: 26, width: Math.max(6, (p.y / max) * 600), background: ACCENT, borderRadius: 4 }} />
          <div style={{ display: "flex", fontSize: 20, color: MUTED }}>{p.y.toLocaleString("en-US")}</div>
        </div>
      ))}
    </div>
  );
}

function MiniTable({ artifact }: { artifact: Extract<PublicArtifact, { kind: "table" }> }) {
  const cols = artifact.columns.slice(0, 4);
  const rows = artifact.rows.slice(0, 5);
  const cell = { display: "flex", flex: 1, padding: "8px 12px", fontSize: 22, overflow: "hidden" } as const;
  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%", border: `2px solid #e7ded5`, borderRadius: 8 }}>
      <div style={{ display: "flex", background: "#f1e9e0", color: MUTED }}>
        {cols.map((c) => <div key={c} style={cell}>{short(c, 22)}</div>)}
      </div>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", color: INK, borderTop: "1px solid #eee5dc" }}>
          {cols.map((c) => <div key={c} style={cell}>{short(label(r[c]), 22)}</div>)}
        </div>
      ))}
    </div>
  );
}

export default async function Image({ params }: { params: { id: string } }) {
  const share = await fetchPublicShare(params.id);
  if (!share) {
    return new ImageResponse(
      (
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", width: "100%", height: "100%", padding: 80, background: PAPER }}>
          <div style={{ display: "flex", fontSize: 40, fontWeight: 700, color: ACCENT }}>QueryMuse</div>
          <div style={{ display: "flex", fontSize: 56, color: INK, marginTop: 24 }}>Shared answer</div>
          <div style={{ display: "flex", fontSize: 32, color: MUTED, marginTop: 16 }}>Sign in to view</div>
        </div>
      ),
      size,
    );
  }
  const { question, answer } = questionAndAnswer(share.messages);
  const visual = share.artifacts.find((a) => a.kind === "chart") ?? share.artifacts.find((a) => a.kind === "table");
  return new ImageResponse(
    (
      <div style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%", padding: "48px 64px", background: PAPER }}>
        <div style={{ display: "flex", flexShrink: 0, fontSize: 26, color: MUTED }}>
          <span style={{ color: ACCENT, fontWeight: 700, marginRight: 14 }}>QueryMuse</span>
          {short(`Shared answer · ${share.organization}`, 60)}
        </div>
        <div style={{ display: "flex", flexShrink: 0, fontSize: visual ? 40 : 54, fontWeight: 700, color: INK, marginTop: 16, lineHeight: 1.2 }}>
          {short(question || share.title, visual ? 80 : 140)}
        </div>
        <div style={{ display: "flex", flexShrink: 0, fontSize: 24, color: MUTED, marginTop: 12, lineHeight: 1.35 }}>
          {plainText(answer, visual ? 85 : 360)}
        </div>
        {visual && (
          <div style={{ display: "flex", marginTop: 22 }}>
            {visual.kind === "chart" ? <Bars artifact={visual} /> : <MiniTable artifact={visual} />}
          </div>
        )}
      </div>
    ),
    size,
  );
}
