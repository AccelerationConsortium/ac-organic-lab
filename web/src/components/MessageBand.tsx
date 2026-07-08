import type { ReactNode } from "react";

/**
 * The single inline "message band" primitive every tile shares — one geometry,
 * two tones. It is the root cause fix for the drift that used to exist between
 * hand-rolled amber/rose boxes across tiles: there is now exactly one place
 * that owns the padding, radius, text size, and per-tone palette.
 *
 *   - `amber` = the device *declined* an action (a 412/423/409 refusal) or a
 *     status *notice* ("why is it busy?"). Not a fault — the equipment is fine.
 *   - `rose`  = something is *wrong*: a device-reported fault or the aggregator
 *     failing to reach the device at all.
 *
 * The rose tone deliberately matches <LastErrorBadge> (the header fault badge)
 * so the whole "something's wrong" family reads as one colour.
 *
 * `tag` is an optional leading mono chip (an HTTP status, a device error code);
 * `children` is the message body. Callers who need a specific band should reach
 * for the purpose-built wrappers (<ActionErrorBand>, <FetchErrorBand>) rather
 * than this primitive directly.
 */

export type BandTone = "amber" | "rose";

const TONE_CLASSES: Record<BandTone, string> = {
  amber:
    "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200",
  rose: "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-200",
};

export function MessageBand({
  tone,
  tag,
  title,
  children,
}: {
  tone: BandTone;
  tag?: ReactNode;
  title?: string;
  children: ReactNode;
}) {
  return (
    <div
      role="status"
      title={title}
      className={`flex items-start gap-2 rounded-md border px-2.5 py-1.5 text-[11px] ${TONE_CLASSES[tone]}`}
    >
      {tag != null && tag !== false && (
        <span className="shrink-0 font-mono font-semibold">{tag}</span>
      )}
      <div className="min-w-0 flex-1 leading-snug">{children}</div>
    </div>
  );
}
