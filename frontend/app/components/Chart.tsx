"use client";

import { useMemo } from "react";

export type ChartSpec = {
  source: string;
  type: "bar" | "line" | "scatter";
  x: string;
  y?: string | null;
  agg: string;
  series?: string | null;
  title?: string;
};
export type ChartPoint = { x: unknown; y: unknown; series?: unknown };
export type ChartData = {
  spec: ChartSpec;
  sql: string;
  points: ChartPoint[];
  truncated: boolean;
  source_tables: string[];
  source_truncated: boolean;
  explanation?: string;
};

const W = 680;
const H = 340;
const M = { top: 16, right: 16, bottom: 70, left: 64 };
// Series colours come from the active theme (--chart-1 … --chart-8).
const PALETTE = Array.from({ length: 8 }, (_, i) => `var(--chart-${i + 1})`);

function label(v: unknown): string {
  if (v === null || v === undefined) return "(null)";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

function niceTicks(min: number, max: number, count = 5): number[] {
  if (min === max) return [min];
  const step0 = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? step0;
  const ticks: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step / 2; t += step) ticks.push(+t.toPrecision(12));
  return ticks;
}

function fmt(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

function toCsv(points: ChartPoint[], hasSeries: boolean): string {
  const esc = (v: unknown) => `"${label(v).replace(/"/g, '""')}"`;
  const head = hasSeries ? "x,series,y" : "x,y";
  return [head, ...points.map((p) => (hasSeries ? [p.x, p.series, p.y] : [p.x, p.y]).map(esc).join(","))].join("\n");
}

export default function Chart({ data }: { data: ChartData }) {
  const { spec, points } = data;
  const hasSeries = !!spec.series;
  const yLabel = spec.agg === "count" ? "count" : `${spec.agg === "none" ? "" : `${spec.agg} of `}${spec.y ?? ""}`;

  const model = useMemo(() => {
    const ys = points.map((p) => Number(p.y)).filter(Number.isFinite);
    const yMin = Math.min(0, ...ys);
    const yMax = Math.max(0, ...ys);
    const ticks = niceTicks(yMin, yMax);
    const lo = Math.min(yMin, ticks[0] ?? yMin);
    const hi = Math.max(yMax, ticks[ticks.length - 1] ?? yMax) || 1;
    const categories = [...new Map(points.map((p) => [label(p.x), p.x])).keys()];
    const seriesNames = hasSeries ? [...new Set(points.map((p) => label(p.series)))] : [""];
    const numericX = spec.type === "scatter" && points.every((p) => Number.isFinite(Number(p.x)));
    const xs = points.map((p) => Number(p.x));
    return { ticks, lo, hi, categories, seriesNames, numericX, xMin: Math.min(...xs), xMax: Math.max(...xs) };
  }, [points, hasSeries, spec.type]);

  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;
  const yPos = (v: number) => M.top + plotH - ((v - model.lo) / (model.hi - model.lo)) * plotH;
  const band = plotW / Math.max(model.categories.length, 1);
  const catX = (x: unknown) => M.left + model.categories.indexOf(label(x)) * band + band / 2;
  const numX = (x: unknown) =>
    M.left + ((Number(x) - model.xMin) / (model.xMax - model.xMin || 1)) * plotW;
  const xPos = model.numericX ? numX : catX;
  const color = (s: unknown) => PALETTE[model.seriesNames.indexOf(hasSeries ? label(s) : "") % PALETTE.length];
  const labelEvery = Math.ceil(model.categories.length / 20);
  const title = spec.title || `${yLabel} by ${spec.x}`;
  const summary = `${spec.type} chart: ${title}. ${points.length} points from workspace table ${spec.source}.`;

  return (
    <figure className="chart">
      <figcaption>
        <strong>{title}</strong>{" "}
        <span className="muted">
          ({spec.type} · from <code>{spec.source}</code>)
        </span>
        {data.explanation && <span className="explanation">{data.explanation}</span>}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary} preserveAspectRatio="xMidYMid meet">
        {model.ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={W - M.right} y1={yPos(t)} y2={yPos(t)} className="gridline" />
            <text x={M.left - 6} y={yPos(t)} textAnchor="end" dominantBaseline="middle" className="tick">
              {fmt(t)}
            </text>
          </g>
        ))}
        <line x1={M.left} x2={W - M.right} y1={yPos(0)} y2={yPos(0)} className="axis" />
        {!model.numericX &&
          model.categories.map((c, i) =>
            i % labelEvery === 0 ? (
              <text
                key={c}
                transform={`translate(${M.left + i * band + band / 2},${H - M.bottom + 12}) rotate(-35)`}
                textAnchor="end"
                className="tick"
              >
                {c.length > 18 ? `${c.slice(0, 17)}…` : c}
              </text>
            ) : null,
          )}
        {spec.type === "bar" &&
          points.map((p, i) => {
            const k = model.seriesNames.length;
            const bw = (band * 0.8) / k;
            const si = hasSeries ? model.seriesNames.indexOf(label(p.series)) : 0;
            const x = catX(p.x) - (band * 0.8) / 2 + si * bw;
            const y = Number(p.y);
            return (
              <rect
                key={i}
                x={x}
                width={Math.max(bw - 1, 1)}
                y={Math.min(yPos(y), yPos(0))}
                height={Math.abs(yPos(y) - yPos(0))}
                style={{ fill: color(p.series) }}
              >
                <title>{`${label(p.x)}${hasSeries ? ` · ${label(p.series)}` : ""}: ${label(p.y)}`}</title>
              </rect>
            );
          })}
        {spec.type === "line" &&
          model.seriesNames.map((s) => {
            const pts = points.filter((p) => !hasSeries || label(p.series) === s);
            return (
              <g key={s}>
                <polyline
                  fill="none"
                  style={{ stroke: color(s) }}
                  strokeWidth={2}
                  points={pts.map((p) => `${xPos(p.x)},${yPos(Number(p.y))}`).join(" ")}
                />
                {pts.map((p, i) => (
                  <circle key={i} cx={xPos(p.x)} cy={yPos(Number(p.y))} r={3} style={{ fill: color(s) }}>
                    <title>{`${label(p.x)}${hasSeries ? ` · ${s}` : ""}: ${label(p.y)}`}</title>
                  </circle>
                ))}
              </g>
            );
          })}
        {spec.type === "scatter" &&
          points.map((p, i) => (
            <circle key={i} cx={xPos(p.x)} cy={yPos(Number(p.y))} r={4} style={{ fill: color(p.series) }} opacity={0.75}>
              <title>{`${label(p.x)}, ${label(p.y)}`}</title>
            </circle>
          ))}
        <text x={14} y={M.top + plotH / 2} transform={`rotate(-90 14 ${M.top + plotH / 2})`} textAnchor="middle" className="axis-label">
          {yLabel}
        </text>
        <text x={M.left + plotW / 2} y={H - 4} textAnchor="middle" className="axis-label">
          {spec.x}
        </text>
      </svg>
      {hasSeries && (
        <ul className="legend" aria-label="Series">
          {model.seriesNames.map((s) => (
            <li key={s}>
              <span className="swatch" style={{ background: color(s) }} aria-hidden /> {s}
            </li>
          ))}
        </ul>
      )}
      <p className="muted chart-notes">
        {data.truncated && <strong>Showing the first {points.length} points. </strong>}
        {data.source_truncated && <strong>Source result was partial. </strong>}
        Source: {data.source_tables.length ? data.source_tables.join(", ") : "unknown"}
      </p>
      <details>
        <summary>Chart data &amp; SQL</summary>
        <a
          className="download"
          href={`data:text/csv;charset=utf-8,${encodeURIComponent(toCsv(points, hasSeries))}`}
          download={`${spec.source}-chart.csv`}
        >
          Download chart data (CSV)
        </a>
        <pre className="hint">{data.sql.replace(/FROM t\b/, `FROM ${spec.source}`)}</pre>
        <div className="results">
          <table className="grid">
            <thead>
              <tr>
                <th scope="col">{spec.x}</th>
                {hasSeries && <th scope="col">{spec.series}</th>}
                <th scope="col">{yLabel}</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p, i) => (
                <tr key={i}>
                  <td>{label(p.x)}</td>
                  {hasSeries && <td>{label(p.series)}</td>}
                  <td>{label(p.y)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
