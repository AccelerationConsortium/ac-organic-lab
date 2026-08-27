/**
 * Turning an assistant answer into something worth hearing.
 *
 * The chat panel renders Markdown; a screen reader-ish literal reading of that
 * is unbearable. A stack trace, a table of `equipment_id`s, or a 40-line code
 * block are all *fine* on screen and all terrible aloud — they take minutes to
 * speak and convey nothing without the visual structure.
 *
 * So speech is a SUMMARY channel, not a transcript reader. Two rules:
 *
 *   1. Only prose is spoken. Fenced code, tables, and URLs are dropped, not
 *      read out.
 *   2. It is bounded. Whole sentences up to a character cap, so an utterance
 *      is always a few seconds — never a monologue the operator has to wait
 *      out or hunt for a stop button during.
 *
 * This lands softly because the assistant's system prompt already asks for
 * "1-3 sentences" with "the answer first" (see api/app/assistant.py). The
 * lead sentence IS the summary; everything trimmed here is detail that was
 * already meant for the eyes.
 *
 * Everything in this module is pure so it can be tested without a browser —
 * the actual speechSynthesis call lives in AssistantBubble.tsx.
 */

/** Roughly 12 seconds at a normal speaking rate. Deliberately under the ~15s
 *  mark where a long-standing Chrome bug stalls a single utterance — an
 *  answer that gets cut off mid-word reads as a broken feature, and the
 *  assistant's 1-3 sentence answers fit inside this comfortably anyway. */
export const SPEAK_CHAR_CAP = 180;

/** Spoken when prose was dropped, so a truncated answer doesn't just stop
 *  mid-thought and leave the listener wondering if it crashed. */
const MORE_MARKER = "More on screen.";
/** Spoken when there was no prose at all (answer was pure code or table). */
const NOTHING_MARKER = "The answer is on screen.";

/** Strip Markdown structure that is meaningless or hostile when spoken.
 *  Returns the prose, plus whether anything substantive was removed. */
function stripMarkdown(md: string): { prose: string; dropped: boolean } {
  let dropped = false;
  let s = md;

  const drop = (re: RegExp, replacement = " ") => {
    if (re.test(s)) dropped = true;
    s = s.replace(re, replacement);
  };

  // Fenced code blocks — the single worst thing to read aloud.
  drop(/```[\s\S]*?```/g);
  // An unterminated fence: the turn was cut off mid-block. Drop to the end.
  drop(/```[\s\S]*$/g);
  // Markdown tables: any line that is mostly pipes.
  drop(/^[ \t]*\|.*$/gm);
  // Bare URLs. The link-text form below is handled first so we keep the words.
  s = s.replace(/\[([^\]]+)\]\((?:[^)]*)\)/g, "$1");
  drop(/\bhttps?:\/\/\S+/g);

  // Structure markers that are silent on screen but read as noise.
  s = s
    .replace(/^[ \t]*#{1,6}[ \t]*/gm, "")      // headings
    .replace(/^[ \t]*[-*+][ \t]+/gm, "")       // bullets
    .replace(/^[ \t]*\d+\.[ \t]+/gm, "")       // numbered list markers
    .replace(/`([^`]*)`/g, "$1")               // inline code: keep the word
    .replace(/\*\*([^*]+)\*\*/g, "$1")         // bold
    .replace(/\*([^*]+)\*/g, "$1")             // italic
    .replace(/(^|\s)_([^_]+)_(?=\s|$)/g, "$1$2"); // underscore italic

  // snake_case ids ("ot2_hte") are spelled out letter-by-letter by most
  // engines. Spaces make them pronounceable without changing the words.
  s = s.replace(/([A-Za-z0-9])_([A-Za-z0-9])/g, "$1 $2");

  s = s.replace(/\s+/g, " ").trim();
  return { prose: s, dropped };
}

/** Split prose into sentences, tolerating decimals and short abbreviations. */
function splitSentences(prose: string): string[] {
  if (!prose) return [];
  // Break after .!? when followed by whitespace and something that starts a
  // new sentence. A decimal ("22.3 C") has no space after the dot, so it is
  // never split here.
  const parts = prose.split(/(?<=[.!?])\s+(?=["'([]?[A-Z0-9])/);

  // Re-join fragments that ended on an abbreviation ("e.g.", "approx.") —
  // those look like sentence ends but are too short to be one.
  const out: string[] = [];
  for (const part of parts) {
    const prev = out[out.length - 1];
    if (prev !== undefined && /(^|\s)\S{1,4}\.$/.test(prev) && prev.length < 12) {
      out[out.length - 1] = `${prev} ${part}`;
    } else {
      out.push(part);
    }
  }
  return out.filter((p) => p.trim().length > 0);
}

/**
 * Reduce a Markdown assistant answer to a short spoken utterance.
 *
 * Returns whole sentences up to `cap` characters. Always returns at least
 * one sentence (hard-cut at a word boundary if a single sentence is itself
 * longer than the cap), and appends a short marker when anything was left
 * behind. Returns "" only for genuinely empty input — never speak nothing
 * when the model said something.
 */
export function speakableFromMarkdown(md: string, cap = SPEAK_CHAR_CAP): string {
  if (!md || !md.trim()) return "";

  const { prose, dropped } = stripMarkdown(md);
  if (!prose) return NOTHING_MARKER;

  const sentences = splitSentences(prose);
  if (sentences.length === 0) return NOTHING_MARKER;

  const taken: string[] = [];
  let len = 0;
  for (const sentence of sentences) {
    const next = len === 0 ? sentence.length : len + 1 + sentence.length;
    if (taken.length > 0 && next > cap) break;
    taken.push(sentence);
    len = next;
    if (len >= cap) break;
  }

  let spoken = taken.join(" ");
  let truncated = taken.length < sentences.length;

  // A single sentence longer than the cap still has to be bounded: cut at the
  // last word boundary that fits rather than mid-word.
  if (spoken.length > cap) {
    const cut = spoken.slice(0, cap);
    const lastSpace = cut.lastIndexOf(" ");
    spoken = (lastSpace > cap * 0.5 ? cut.slice(0, lastSpace) : cut).trim();
    truncated = true;
  }

  if (truncated || dropped) spoken = `${spoken} ${MORE_MARKER}`;
  return spoken.trim();
}
