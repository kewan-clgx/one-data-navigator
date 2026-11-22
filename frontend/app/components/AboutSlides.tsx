"use client";

import { useEffect, useState, type KeyboardEvent } from "react";

const ROTATE_MS = 5000;

import type { AboutSlide } from "../themes";

export default function AboutSlides({ slides, label }: { slides: AboutSlide[]; label: string }) {
  const [index, setIndex] = useState(0);
  // Paused by the button, or while the user is hovering/focused inside (so they can read or use the dots).
  const [paused, setPaused] = useState(false);
  const [engaged, setEngaged] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const go = (next: number) => setIndex((next + slides.length) % slides.length);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mq.matches);
    const onChange = () => setReducedMotion(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const rotating = slides.length > 1 && !paused && !engaged && !reducedMotion;
  useEffect(() => {
    if (!rotating) return;
    // Restarts on every slide change, so a manual pick also gets a full 5 seconds.
    const timer = setTimeout(() => setIndex((i) => (i + 1) % slides.length), ROTATE_MS);
    return () => clearTimeout(timer);
  }, [rotating, index, slides.length]);
  const slide = slides[index];

  function onKeyDown(e: KeyboardEvent<HTMLElement>) {
    if (e.key === "ArrowRight") go(index + 1);
    else if (e.key === "ArrowLeft") go(index - 1);
    else return;
    e.preventDefault();
  }

  return (
    <section
      className="odn-slides"
      aria-roledescription="carousel"
      aria-label={label}
      onKeyDown={onKeyDown}
      onMouseEnter={() => setEngaged(true)}
      onMouseLeave={() => setEngaged(false)}
      onFocus={() => setEngaged(true)}
      onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setEngaged(false)}
    >
      <div
        className="odn-slide"
        role="group"
        aria-roledescription="slide"
        aria-label={`${index + 1} of ${slides.length}`}
        aria-live={rotating ? "off" : "polite"}
      >
        <span className="odn-tag">{slide.kicker}</span>
        <h2 className="odn-slide-title">{slide.title}</h2>
        <p className="odn-slide-lead">{slide.lead}</p>
        <ul className="odn-slide-points">
          {slide.points.map((p) => <li key={p}>{p}</li>)}
        </ul>
      </div>
      <div className="odn-slide-dots">
        {slides.map((s, i) => (
          <button
            key={s.kicker}
            type="button"
            className="odn-slide-dot"
            aria-label={`Slide ${i + 1}: ${s.kicker}`}
            aria-current={i === index ? "true" : undefined}
            onClick={() => go(i)}
          />
        ))}
        {slides.length > 1 && !reducedMotion && (
          <button
            type="button"
            className="odn-slide-play"
            aria-label={paused ? "Play slideshow" : "Pause slideshow"}
            onClick={() => setPaused((p) => !p)}
          >
            {paused ? "▶" : "❚❚"}
          </button>
        )}
      </div>
    </section>
  );
}
