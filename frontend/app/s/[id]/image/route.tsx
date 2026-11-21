import { ImageResponse } from "next/og";

import { loadPublicShare } from "@/lib/public-share";
import { highlight, questionAndAnswer, type PublicArtifact } from "@/lib/share";

// The link-preview image of a shared answer (og:image). A plain route (not the opengraph-image convention, whose
// one-year immutable cache header can't be overridden): previews must follow edits, revokes and expiry.
const size = { width: 1200, height: 630 };
export const dynamic = "force-dynamic";

const INK = "#1f1a17";
const MUTED = "#6b625c";
const ACCENT = "#e2583e";
const PAPER = "#fbf7f2";

const label = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const short = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

// The app's chart colours (themes/cotality.css --chart-1..8).
const PALETTE = ["#1e1405", "#f2735e", "#f4a259", "#6b8f71", "#8c5a3c", "#4a6fa5", "#b8a07a", "#7d6b5d"];
const MAX_CATEGORIES = 12;

function fmt(n: number) {
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
  return n.toLocaleString("en-US");
}

/** The shared chart as the app draws it: categories along x, a bar (or dot) per series, legend. */
function ChartImage({ artifact, height }: { artifact: Extract<PublicArtifact, { kind: "chart" }>; height: number }) {
  const { spec, points } = artifact.result;
  const categories = [...new Set(points.map((p) => label(p.x)))].slice(0, MAX_CATEGORIES);
  const series = [...new Set(points.map((p) => label(p.series)))].slice(0, PALETTE.length);
  const value = new Map(points.map((p) => [`${label(p.x)}\u0000${label(p.series)}`, Number(p.y)]));
  const ys = [...value.values()].filter(Number.isFinite);
  const max = Math.max(...ys, 0) || 1;
  const plotH = height - (series.length > 1 ? 74 : 44);
  const groupW = Math.floor(1040 / Math.max(categories.length, 1));
  const barW = Math.max(4, Math.floor((groupW * 0.78) / Math.max(series.length, 1)));
  const dots = spec.type !== "bar";
  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%" }}>
      <div style={{ display: "flex", alignItems: "flex-end", height: plotH, borderBottom: `2px solid ${INK}`, position: "relative" }}>
        <div style={{ display: "flex", position: "absolute", top: 0, left: 0, fontSize: 16, color: MUTED }}>{fmt(max)}</div>
        {categories.map((c) => (
          <div key={c} style={{ display: "flex", width: groupW, height: plotH, alignItems: "flex-end", justifyContent: "center", gap: dots ? 0 : 2 }}>
            {series.map((s, i) => {
              const v = value.get(`${c}\u0000${s}`);
              const h = v !== undefined && Number.isFinite(v) ? Math.max(2, (Math.max(v, 0) / max) * (plotH - 24)) : 0;
              return dots ? (
                <div key={s} style={{ display: "flex", width: barW, height: h, alignItems: "flex-start", justifyContent: "center" }}>
                  {h > 0 && <div style={{ width: 14, height: 14, borderRadius: 7, background: PALETTE[i] }} />}
                </div>
              ) : (
                <div key={s} style={{ display: "flex", width: barW, height: h, background: PALETTE[i], borderRadius: "3px 3px 0 0" }} />
              );
            })}
          </div>
        ))}
      </div>
      <div style={{ display: "flex" }}>
        {categories.map((c) => (
          <div key={c} style={{ display: "flex", width: groupW, justifyContent: "center", fontSize: 16, color: MUTED, marginTop: 6, overflow: "hidden", whiteSpace: "nowrap" }}>
            {short(c, Math.max(6, Math.floor(groupW / 10)))}
          </div>
        ))}
      </div>
      {series.length > 1 && (
        <div style={{ display: "flex", gap: 22, marginTop: 10, fontSize: 18, color: INK }}>
          {series.map((s, i) => (
            <div key={s} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ width: 16, height: 16, background: PALETTE[i] }} />
              {short(s || spec.y || "value", 24)}
            </div>
          ))}
        </div>
      )}
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

/** ImageResponse always adds a one-year immutable cache header; serve its PNG with ours instead. */
function png(image: ImageResponse, cacheControl: string) {
  return new Response(image.body, { headers: { "Content-Type": "image/png", "Cache-Control": cacheControl } });
}

export async function GET(_req: Request, { params }: { params: { id: string } }) {
  const share = await loadPublicShare(params.id);
  // API not reachable (e.g. during a deploy): don't let crawlers cache a placeholder.
  if (share === "unavailable") return new Response("Try again shortly", { status: 503, headers: { "Retry-After": "30" } });
  if (!share) {
    return png(new ImageResponse(
      (
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", width: "100%", height: "100%", padding: 80, background: PAPER }}>
          <div style={{ display: "flex", fontSize: 40, fontWeight: 700, color: ACCENT }}>QueryMuse</div>
          <div style={{ display: "flex", fontSize: 56, color: INK, marginTop: 24 }}>Shared answer</div>
          <div style={{ display: "flex", fontSize: 32, color: MUTED, marginTop: 16 }}>Sign in to view</div>
        </div>
      ),
      size,
    ), "no-store");
  }
  const { answer } = questionAndAnswer(share.messages);
  const visual = share.artifacts.find((a) => a.kind === "chart") ?? share.artifacts.find((a) => a.kind === "table");
  return png(new ImageResponse(
    (
      <div style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%", padding: "40px 64px", background: PAPER }}>
        <div style={{ display: "flex", flexShrink: 0, fontSize: 22, color: MUTED }}>
          <span style={{ color: ACCENT, fontWeight: 700, marginRight: 14 }}>QueryMuse</span>
          {short(`Shared answer · ${share.organization}`, 60)}
        </div>
        <div style={{ display: "flex", flexShrink: 0, fontSize: visual ? 40 : 54, fontWeight: 700, color: INK, marginTop: 10, lineHeight: 1.15 }}>
          {short(share.title, visual ? 70 : 140)}
        </div>
        {!visual && (
          <div style={{ display: "flex", fontSize: 28, color: MUTED, marginTop: 18, lineHeight: 1.35 }}>{highlight(answer, 300)}</div>
        )}
        {visual && (
          <div style={{ display: "flex", flexGrow: 1, marginTop: 22 }}>
            {visual.kind === "chart" ? <ChartImage artifact={visual} height={visual.result.spec.series ? 400 : 420} /> : <MiniTable artifact={visual} />}
          </div>
        )}
      </div>
    ),
    size,
  ), "public, max-age=600");
}
