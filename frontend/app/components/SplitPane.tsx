"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";

const HANDLE_PX = 6;
const KEY_STEP = 16;

export default function SplitPane({
  storageKey,
  defaultFraction,
  minLeft = 240,
  minRight = 320,
  className,
  label,
  left,
  right,
}: {
  storageKey: string;
  defaultFraction: number;
  minLeft?: number;
  minRight?: number;
  className: string;
  label: string;
  left: ReactNode;
  right: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);

  // Hidden tabs report 0 width, so fall back to the viewport.
  const total = () => ref.current?.clientWidth || window.innerWidth;
  const clamp = useCallback(
    (w: number) => Math.round(Math.max(minLeft, Math.min(w, total() - minRight - HANDLE_PX))),
    [minLeft, minRight],
  );
  const defaultWidth = useCallback(() => clamp(total() * defaultFraction), [clamp, defaultFraction]);
  const persist = (w: number) => localStorage.setItem(storageKey, String(w));

  useEffect(() => {
    const saved = Number(localStorage.getItem(storageKey));
    setWidth(saved > 0 ? clamp(saved) : defaultWidth());
  }, [storageKey, clamp, defaultWidth]);

  useEffect(() => {
    const onResize = () => setWidth((w) => (w == null ? w : clamp(w)));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clamp]);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (width == null) return;
    e.preventDefault();
    const handle = e.currentTarget;
    const startX = e.clientX;
    const startWidth = width;
    let latest = startWidth;
    handle.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    const move = (ev: globalThis.PointerEvent) => {
      latest = clamp(startWidth + ev.clientX - startX);
      setWidth(latest);
    };
    const end = () => {
      handle.removeEventListener("pointermove", move);
      document.body.classList.remove("resizing");
      persist(latest);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end, { once: true });
    handle.addEventListener("pointercancel", end, { once: true });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (width == null) return;
    const step = e.shiftKey ? KEY_STEP * 4 : KEY_STEP;
    const next =
      e.key === "ArrowLeft" ? width - step
      : e.key === "ArrowRight" ? width + step
      : e.key === "Home" ? minLeft
      : e.key === "End" ? Number.MAX_SAFE_INTEGER
      : null;
    if (next == null) return;
    e.preventDefault();
    const w = clamp(next);
    setWidth(w);
    persist(w);
  };

  const reset = () => {
    const w = defaultWidth();
    setWidth(w);
    localStorage.removeItem(storageKey);
  };

  return (
    <div
      ref={ref}
      className={`split ${className}`}
      style={width == null ? undefined : { gridTemplateColumns: `${width}px ${HANDLE_PX}px minmax(0, 1fr)` }}
    >
      {left}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={label}
        aria-valuenow={width ?? undefined}
        aria-valuemin={minLeft}
        tabIndex={0}
        className="split-handle"
        title="Drag to resize · double-click to reset"
        onPointerDown={onPointerDown}
        onKeyDown={onKeyDown}
        onDoubleClick={reset}
      />
      {right}
    </div>
  );
}
