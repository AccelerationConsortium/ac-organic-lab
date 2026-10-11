"use client";

import Link from "next/link";
import { useState } from "react";

const card = "rounded-xl border border-slate-200 bg-surface-raised p-5 dark:border-slate-800 dark:bg-slate-900";
const button = "rounded-md bg-sky-700 px-4 py-2 text-sm text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500 disabled:opacity-50";

export function PreviewHome() {
  return <div className="space-y-6">
    <p className="text-ink-muted dark:text-slate-300">Start with a platform, your notebook, or a look at the lab.</p>
    <div className="grid gap-4 lg:grid-cols-3">
      <Link href="/preview/dashboard/bookings" className={card}><h2 className="font-semibold">Book equipment</h2><p className="mt-2 text-sm">Explore the mock booking screen. No real reservations.</p></Link>
      <Link href="/preview/dashboard/platforms" className={card}><h2 className="font-semibold">Platforms</h2><p className="mt-2 text-sm">Open the existing platform tiles and controls.</p></Link>
      <a href="/bitacora/" target="_blank" rel="noopener noreferrer" className={card}><h2 className="font-semibold">ELN ↗</h2><p className="mt-2 text-sm">Open Bitácora in a new tab.</p></a>
    </div>
    <section className={card}><h2 className="font-semibold">Upcoming reservations · Mock</h2><p className="mt-2 text-sm">Real reservations are not connected in this preview.</p><Link href="/preview/dashboard/overview" className="mt-4 inline-block text-sky-700 underline dark:text-sky-300">Open lab Overview</Link></section>
    <p className="text-sm text-ink-subtle dark:text-slate-400">Overview, Platforms, Inventory and the assistant use existing services. Device and detail links retain their existing destinations and may leave this preview.</p>
  </div>;
}

export function MockBookings() {
  const [reserved, setReserved] = useState(false);
  return <section className={`${card} space-y-4`}>
    <p className="font-medium">Mock bookings — demonstration only</p>
    <p>No real equipment is reserved. Changes exist only while this screen is open and do not grant control or start a run.</p>
    <h2 className="font-semibold">Example instrument · Example time slot</h2>
    <p role="status">{reserved ? "Mock reservation added in this screen." : "No mock reservation selected."}</p>
    <button type="button" className={button} onClick={() => setReserved(!reserved)}>{reserved ? "Cancel mock reservation" : "Try mock reservation"}</button>
  </section>;
}

export function MockContactAdmin() {
  const [message, setMessage] = useState("");
  const [submitted, setSubmitted] = useState(false);
  return <section className={`${card} space-y-4`}>
    <p className="font-medium">Mock contact form — no message is sent</p>
    <p>This demonstration has no inbox or delivery service. It is not an emergency contact channel.</p>
    <form className="flex max-w-xl flex-col gap-3" onSubmit={(event) => { event.preventDefault(); setSubmitted(true); }}>
      <label htmlFor="mock-admin-message">Example message</label>
      <textarea id="mock-admin-message" required maxLength={2000} rows={5} value={message} onChange={(event) => { setMessage(event.target.value); setSubmitted(false); }} className="w-full rounded-md border border-slate-300 bg-transparent p-3 dark:border-slate-600" />
      <button type="submit" disabled={!message.trim()} className={button}>Preview mock submission</button>
    </form>
    {submitted && <p role="status">Mock submission previewed. Nothing was sent or saved.</p>}
  </section>;
}
