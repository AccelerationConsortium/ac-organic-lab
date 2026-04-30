"use client";

import { useEffect, useState } from "react";
import { isStale, relativeTime } from "@/lib/format";

interface Props {
  fetchedAt: string;
  /** Seconds after which the data is considered stale. Default 8s. */
  staleAfterSeconds?: number;
}

export function StalenessIndicator({ fetchedAt, staleAfterSeconds = 8 }: Props) {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!now) {
    return <span className="text-xs text-ink-subtle dark:text-slate-500">…</span>;
  }

  const stale = isStale(fetchedAt, staleAfterSeconds, now);
  return (
    <span
      className={`text-xs ${
        stale
          ? "text-amber-700 dark:text-amber-400"
          : "text-ink-subtle dark:text-slate-400"
      }`}
      title={fetchedAt}
    >
      {stale ? "stale · " : ""}
      {relativeTime(fetchedAt, now)}
    </span>
  );
}
