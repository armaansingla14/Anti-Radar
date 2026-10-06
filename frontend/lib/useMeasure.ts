"use client";

import { useEffect, useRef, useState } from "react";

/** Track an element's content width so SVG charts render at true pixel size. */
export function useMeasure<T extends HTMLElement>(fallback = 600) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}
