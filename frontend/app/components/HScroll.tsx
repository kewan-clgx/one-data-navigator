"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Horizontal-scroll container for wide grids that makes off-screen columns obvious: edge shadows where more content
 * is hidden, an always-visible scrollbar (macOS overlay scrollbars stay hidden until you scroll), and a
 * "Columns 1–6 of 47" counter with ‹ › buttons that page by the visible width.
 */
export default function HScroll({
  children,
  className = "",
  columns,
  busy,
}: {
  children: ReactNode;
  className?: string;
  /** Total columns, for the counter; omit to hide it. */
  columns?: number;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [edge, setEdge] = useState({ left: false, right: false, first: 1, last: 0 });

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const left = el.scrollLeft > 1;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    // Visible column range from the header cells (the row-number gutter, if any, is a th.wb-rownum and skipped).
    let first = 1;
    let last = 0;
    const heads = [...el.querySelectorAll<HTMLElement>("thead th")].filter((th) => !th.classList.contains("wb-rownum"));
    const gutter = el.querySelector<HTMLElement>("thead th.wb-rownum")?.offsetWidth ?? 0;
    const viewL = el.scrollLeft + gutter;
    const viewR = el.scrollLeft + el.clientWidth;
    heads.forEach((th, i) => {
      // Counted when at least 40px (or all of a narrow column) is on screen.
      const overlap = Math.min(th.offsetLeft + th.offsetWidth, viewR) - Math.max(th.offsetLeft, viewL);
      if (overlap >= Math.min(40, th.offsetWidth)) {
        if (!last) first = i + 1;
        last = i + 1;
      }
    });
    setEdge((e) => (e.left === left && e.right === right && e.first === first && e.last === last ? e : { left, right, first, last }));
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    const table = el.querySelector("table");
    if (table) observer.observe(table);
    return () => observer.disconnect();
  }, [measure, columns]);

  const page = (dir: 1 | -1) => {
    const el = ref.current;
    if (el) el.scrollBy({ left: dir * el.clientWidth * 0.8, behavior: "smooth" });
  };
  const overflow = edge.left || edge.right;

  return (
    <div className={`hscroll${edge.left ? " hscroll--left" : ""}${edge.right ? " hscroll--right" : ""}`}>
      {overflow && columns != null && columns > 0 && (
        <div className="hscroll-bar">
          <span className="muted">
            Columns {edge.first}–{edge.last} of {columns}
            <span className="hscroll-hint"> · scroll sideways or Shift + wheel</span>
          </span>
          <span className="hscroll-nav">
            <button type="button" className="hscroll-btn" onClick={() => page(-1)} disabled={!edge.left} aria-label="Scroll to earlier columns">‹</button>
            <button type="button" className="hscroll-btn" onClick={() => page(1)} disabled={!edge.right} aria-label="Scroll to more columns">›</button>
          </span>
        </div>
      )}
      <div className="hscroll-frame">
        <div ref={ref} className={`hscroll-body ${className}`} onScroll={measure} aria-busy={busy}>
          {children}
        </div>
      </div>
    </div>
  );
}
