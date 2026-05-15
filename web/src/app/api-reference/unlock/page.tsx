export default async function UnlockPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next = "/api-reference", error } = await searchParams;

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-surface-raised p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h2 className="mb-1 text-lg font-semibold text-ink dark:text-slate-100">
          API Reference
        </h2>
        <p className="mb-6 text-sm text-ink-muted dark:text-slate-400">
          Enter the lab password to view the device skill catalog.
        </p>

        {error && (
          <p className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300">
            Incorrect password — try again.
          </p>
        )}

        <form method="POST" action="/api/api-ref-auth" className="flex flex-col gap-3">
          <input type="hidden" name="next" value={next} />
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-ink-muted dark:text-slate-400">
              Password
            </span>
            <input
              type="password"
              name="password"
              autoFocus
              autoComplete="current-password"
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-ink shadow-sm outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-200 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:focus:border-sky-500 dark:focus:ring-sky-900/50"
            />
          </label>
          <button
            type="submit"
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700 dark:bg-sky-700 dark:hover:bg-sky-600"
          >
            Unlock
          </button>
        </form>
      </div>
    </div>
  );
}
