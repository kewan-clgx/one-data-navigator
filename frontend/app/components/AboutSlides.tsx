"use client";

import { useState, type KeyboardEvent } from "react";

import type { AboutSlide } from "../themes";

export default function AboutSlides({ slides, label }: { slides: AboutSlide[]; label: string }) {
  const [index, setIndex] = useState(0);
  const go = (next: number) => setIndex((next + slides.length) % slides.length);
  const slide = slides[index];

  function onKeyDown(e: KeyboardEvent<HTMLElement>) {
    if (e.key === "ArrowRight") go(index + 1);
    else if (e.key === "ArrowLeft") go(index - 1);
    else return;
    e.preventDefault();
  }

  return (
    <section className="odn-slides" aria-roledescription="carousel" aria-label={label} onKeyDown={onKeyDown}>
      <div
        className="odn-slide"
        role="group"
        aria-roledescription="slide"
        aria-label={`${index + 1} of ${slides.length}`}
        aria-live="polite"
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
      </div>
    </section>
  );
}
