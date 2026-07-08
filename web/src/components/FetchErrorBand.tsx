import type { FetchError } from "@/types/api";
import { MessageBand } from "./MessageBand";

/**
 * The shared band for a transport-level reachability failure: the aggregator's
 * poll of the device's `/status` timed out or was refused. This is a
 * reader-side ("unreachable") condition, not a device-reported fault — but it
 * still means "something is wrong," so it wears the rose tone alongside
 * <LastErrorBadge>.
 *
 * Previously this exact block was copy-pasted into EquipmentStatusCard and a
 * couple of kind-specific tiles, which is how they drifted; this is the one
 * definition. Drop `{snapshot.fetch_error && <FetchErrorBand error={...} />}`
 * into any tile that wants to surface reachability failures in-body.
 */
export function FetchErrorBand({ error }: { error: FetchError }) {
  return (
    <MessageBand tone="rose">
      <span className="block font-medium">Aggregator could not reach device</span>
      <span className="block font-mono">
        {error.kind}
        {error.http_status ? ` · HTTP ${error.http_status}` : ""}
      </span>
    </MessageBand>
  );
}
