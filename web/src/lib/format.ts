import type { EquipmentState } from "@/types/api";

const stateClasses: Record<EquipmentState, string> = {
  ready: "bg-emerald-100 text-emerald-900 ring-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-200 dark:ring-emerald-800",
  busy: "bg-sky-100 text-sky-900 ring-sky-200 dark:bg-sky-900/30 dark:text-sky-200 dark:ring-sky-800",
  requires_init: "bg-amber-100 text-amber-900 ring-amber-200 dark:bg-amber-900/30 dark:text-amber-200 dark:ring-amber-800",
  degraded: "bg-orange-100 text-orange-900 ring-orange-200 dark:bg-orange-900/30 dark:text-orange-200 dark:ring-orange-800",
  dry_run: "bg-violet-100 text-violet-900 ring-violet-200 dark:bg-violet-900/30 dark:text-violet-200 dark:ring-violet-800",
  error: "bg-rose-100 text-rose-900 ring-rose-200 dark:bg-rose-900/30 dark:text-rose-200 dark:ring-rose-800",
  e_stop: "bg-red-100 text-red-900 ring-red-300 dark:bg-red-900/30 dark:text-red-200 dark:ring-red-800",
  unknown: "bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800/50 dark:text-slate-300 dark:ring-slate-700",
};

const stateLabels: Record<EquipmentState, string> = {
  ready: "Ready",
  busy: "Busy",
  requires_init: "Needs init",
  degraded: "Degraded",
  dry_run: "Dry run",
  error: "Error",
  e_stop: "E-stop",
  unknown: "Unknown",
};

export function stateLabel(state: EquipmentState): string {
  return stateLabels[state];
}

export function stateClass(state: EquipmentState): string {
  return stateClasses[state];
}

export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const diffMs = now.getTime() - then.getTime();
  if (Number.isNaN(diffMs)) return iso;
  const seconds = Math.round(diffMs / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export function isStale(iso: string, thresholdSeconds = 10, now: Date = new Date()): boolean {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return true;
  return now.getTime() - then.getTime() > thresholdSeconds * 1000;
}

const kindLabels: Record<string, string> = {
  solid_doser: "Solid doser",
  liquid_handler: "Liquid handler",
  press: "Press",
  fume_hood: "Fume hood",
  robot_arm: "Robot arm",
  environmental_sensor: "Env sensor",
  hplc: "HPLC",
  plate_reader: "Plate reader",
  plate_sealer: "Plate sealer",
  plate_stacker: "Plate stacker",
  camera: "Camera",
  smart_plug: "Smart plug",
  power_strip: "Power strip",
  other: "Other",
};

export function kindLabel(kind: string): string {
  return kindLabels[kind] ?? kind;
}
