/** Download-only history. Nothing in this format can restore a live card. */
export interface ControlHistoryEvent {
  event: string;
  occurred_at: string;
  detail: Record<string, unknown>;
}

interface TranscriptTurn {
  role: "user" | "assistant";
  text: string;
  tools: { name: string; ok: boolean }[];
  mode?: string;
  stopped?: boolean;
  completion?: "streaming" | "completed" | "interrupted" | "failed";
  error?: string;
  images?: { url: string; camera_id: string; camera_name?: string; taken_at?: string }[];
  refusal?: { code: string; message: string };
  declined?: { reason_code: string; explanation: string };
  control_events?: ControlHistoryEvent[];
}

const NOTICE = "Conversation export only; not a registered Plan or proof of execution. " +
  "Includes history currently available in this tab; a reload may retain only recent messages. " +
  "Control outcomes are browser reports; raw device responses are not included. Proposals and control actions remain in the audit trail. " +
  "Camera images are linked, not embedded; snapshot links expire after about 24 hours.";

export function conversationExport(turns: readonly TranscriptTurn[], now = new Date()) {
  return {
    schema_version: 1,
    record_type: "conversation_export",
    executable: false,
    exported_at: now.toISOString(),
    notice: NOTICE,
    messages: turns.map((turn) => ({
      role: turn.role,
      mode: turn.mode ?? "ask",
      text: turn.text,
      completion: turn.stopped ? "interrupted" : turn.completion ?? "unknown",
      error: turn.error,
      tools: turn.tools.map(({ name, ok }) => ({ name, completed: ok })),
      images: turn.images?.map(({ url, camera_id, camera_name, taken_at }) => ({
        url, camera_id, camera_name, taken_at,
      })),
      refusal: turn.refusal,
      declined: turn.declined,
      control_events: turn.control_events,
    })),
  };
}

// Use a fence longer than any fence in the data, so pasted code remains data.
function fenced(text: string, language = "") {
  const longest = Math.max(2, ...Array.from(text.matchAll(/`+/g), (m) => m[0].length));
  const fence = "`".repeat(longest + 1);
  return `${fence}${language}\n${text}\n${fence}`;
}

export function conversationMarkdown(transcript: ReturnType<typeof conversationExport>) {
  return [
    "# Assistant conversation",
    `Exported: ${transcript.exported_at}`,
    transcript.notice,
    ...transcript.messages.map(({ text, role, mode, completion, ...details }, i) => [
      `## ${i + 1}. ${role} (${mode}; ${completion})`,
      fenced(text),
      fenced(JSON.stringify(details, null, 2), "json"),
    ].join("\n\n")),
    "",
  ].join("\n\n");
}

export function downloadConversation(turns: readonly TranscriptTurn[], format: "md" | "json") {
  const transcript = conversationExport(turns);
  for (const message of transcript.messages) {
    for (const snapshot of message.images ?? []) {
      snapshot.url = new URL(snapshot.url, window.location.href).href;
    }
  }
  const content = format === "json" ? JSON.stringify(transcript, null, 2) : conversationMarkdown(transcript);
  const blob = new Blob([content], { type: format === "json" ? "application/json" : "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `assistant-${transcript.exported_at.replace(/[:.]/g, "-")}.${format}`;
  try {
    document.body.appendChild(link);
    link.click();
  } finally {
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
