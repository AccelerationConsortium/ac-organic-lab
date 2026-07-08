import { useCallback, useState, useTransition } from "react";
import {
  interpretActionError,
  type ActionError,
  type Parse412,
} from "./action-error";

/**
 * Owns the inline action-error bubble state for a control tile and the
 * transition-wrapped `exec` that drives it. Pair with <ActionErrorBand>.
 *
 * Every control tile follows the same shape: clicking a control clears any
 * prior error, fires the request in a transition, and — on failure — surfaces
 * the device's refusal in the band. This hook is that shape, factored out.
 *
 * @param parse412 optional device-specific 412 body specializer. Pass a
 *   module-scope function (stable identity) so the memoized callbacks below
 *   don't churn.
 */
export function useActionError(parse412?: Parse412) {
  const [actionError, setActionError] = useState<ActionError | null>(null);
  const [isPending, startTransition] = useTransition();

  const clearError = useCallback(() => setActionError(null), []);

  /** Interpret + store an error caught elsewhere (e.g. inside a tile's own
   *  optimistic-rollback catch block). */
  const reportError = useCallback(
    (err: unknown, action?: string) =>
      setActionError(interpretActionError(err, { action, parse412 })),
    [parse412],
  );

  /**
   * Run a control action inside a transition: clear any prior error, await
   * `fn`, and surface a failure in the band. `opts.action` is the skill name
   * (e.g. "seal.start") so `parse412` can specialize on it. `opts.onError`
   * runs before the band is set, for extra cleanup like rolling back an
   * optimistic UI state — the band still shows afterward.
   */
  const exec = useCallback(
    <T,>(
      fn: () => Promise<T>,
      opts: { action?: string; onError?: (err: unknown) => void } = {},
    ) => {
      setActionError(null);
      startTransition(() => {
        fn().catch((err: unknown) => {
          opts.onError?.(err);
          setActionError(
            interpretActionError(err, { action: opts.action, parse412 }),
          );
        });
      });
    },
    [parse412],
  );

  return { actionError, setActionError, clearError, reportError, exec, isPending };
}
