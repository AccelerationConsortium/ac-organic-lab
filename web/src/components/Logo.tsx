"use client";

import { useEffect, useState } from "react";

/**
 * Swaps the UofT/AC logo for its dark-background variant when the `dark`
 * class is set (see ThemeToggle). Renders the light logo until mounted —
 * matches the light theme's SSR default, so no hydration mismatch.
 */
export function Logo() {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains("dark"));
    const observer = new MutationObserver(() => {
      setIsDark(document.documentElement.classList.contains("dark"));
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={isDark ? "/logo-uoft-ac_dark.png" : "/logo-uoft-ac.png"}
      alt="University of Toronto · Acceleration Consortium"
      className="hidden h-[60px] w-auto shrink-0 sm:block md:h-[72px]"
    />
  );
}
