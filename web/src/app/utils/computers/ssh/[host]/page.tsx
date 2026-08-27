"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { fetchSshHosts, type SshHost } from "@/lib/ssh-api";
import { SshTerminal } from "./SshTerminal";

/**
 * SSH console for one lab host: the connection banner, then a live terminal.
 *
 * Reached from the "SSH terminal" link on a tile in Utils → Computers and
 * Servers, in a new tab. Admin-only three times over: the link is hidden from
 * non-admins, `web/src/middleware.ts` redirects a non-admin navigation here,
 * and `api/app/ssh_console.py` refuses to mint a ticket. Machine principals
 * (API keys) are refused outright — see that module's docstring for why a
 * shell is a human-only affordance in this lab.
 */

function BannerRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-wrap gap-x-2 text-xs">
      <span className="w-24 shrink-0 font-medium uppercase text-ink-muted dark:text-slate-400">
        {label}
      </span>
      <span className="font-mono text-ink-subtle dark:text-slate-300">{value}</span>
    </div>
  );
}

function Banner({ host }: { host: SshHost }) {
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-surface-raised p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-0.5">
        <h1 className="text-lg font-semibold text-ink dark:text-slate-100">
          {host.label} · SSH
        </h1>
        <p className="text-xs text-ink-subtle dark:text-slate-400">
          <span className="uppercase">{host.kind}</span> ·{" "}
          <span className="font-mono">{host.hostname}</span>
        </p>
      </div>
      <div className="flex flex-col gap-1">
        <BannerRow label="User" value={host.user} />
        <BannerRow label="Shell" value={host.shell} />
        <BannerRow label="From here" value={host.ssh_command} />
        <BannerRow label="From a shell" value={host.ssh_command_explicit} />
      </div>
      <p className="text-xs text-ink-subtle dark:text-slate-300">{host.note}</p>
      <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
        This session runs as <span className="font-mono">{host.user}</span> using the
        dashboard host&apos;s SSH key, and is recorded in the lab history DB with your
        identity. A shell sits below every interlock the lab has — for routine service
        restarts prefer the whitelisted host-ops surface, and never drive hardware from
        here.
      </p>
    </section>
  );
}

export default function SshConsolePage() {
  const params = useParams<{ host: string }>();
  const hostId = typeof params?.host === "string" ? params.host : "";
  const { data, error, isPending } = useQuery({
    queryKey: ["ssh-hosts"],
    queryFn: fetchSshHosts,
    staleTime: 5 * 60_000,
    retry: false,
  });

  if (isPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-300">Loading host…</p>;
  }
  if (error) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Could not load the SSH host list: {error.message}
      </p>
    );
  }

  const host = data.hosts.find((candidate) => candidate.id === hostId);
  if (!host) {
    return (
      <div className="flex flex-col gap-2">
        <p className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
          No SSH host registered as <span className="font-mono">{hostId || "(none)"}</span>.
        </p>
        <Link
          href="/utils/computers"
          className="w-fit text-sm text-sky-700 underline dark:text-sky-300"
        >
          ← Computers and Servers
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Banner host={host} />
      <SshTerminal hostId={host.id} hostLabel={host.label} />
    </div>
  );
}
