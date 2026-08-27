import { describe, expect, it } from "vitest";

import { SPEAK_CHAR_CAP, speakableFromMarkdown } from "./speech";

const speak = (md: string) => speakableFromMarkdown(md);

describe("speakableFromMarkdown", () => {
  it("passes a short prose answer through unchanged", () => {
    expect(speak("The shaker is degraded but still running.")).toBe(
      "The shaker is degraded but still running."
    );
  });

  it("says nothing for empty input", () => {
    expect(speak("")).toBe("");
    expect(speak("   \n  ")).toBe("");
  });

  // The whole point: a verbose answer must not become a monologue.
  it("stops at whole sentences within the cap and flags the remainder", () => {
    const out = speak(
      "The Cytation is degraded. Its incubator stopped responding at 14:20. " +
        "This has happened twice this month. The RTD calibration is suspect. " +
        "An operator should recalibrate at the front panel. Until then " +
        "temperature control stays unavailable."
    );
    expect(out.length).toBeLessThanOrEqual(SPEAK_CHAR_CAP + 20);
    expect(out).toContain("The Cytation is degraded.");
    expect(out).toContain("More on screen.");
    // Cut on a sentence boundary, not mid-word.
    expect(out).not.toContain("recalibr ");
  });

  it("bounds a single sentence longer than the cap at a word boundary", () => {
    const out = speak(`${"alpha ".repeat(80)}omega.`);
    expect(out.length).toBeLessThanOrEqual(SPEAK_CHAR_CAP + 20);
    expect(out).toContain("More on screen.");
    expect(out).not.toMatch(/alph More/); // no mid-word cut
  });

  it("never reads a fenced code block aloud", () => {
    const out = speak(
      "The service failed to start.\n\n```\nTraceback (most recent call last):\n" +
        "  File \"/x.py\", line 3\n    raise RuntimeError('boom')\n```\n"
    );
    expect(out).toContain("The service failed to start.");
    expect(out).not.toContain("Traceback");
    expect(out).not.toContain("RuntimeError");
    expect(out).toContain("More on screen.");
  });

  it("drops an unterminated code fence from a cut-off turn", () => {
    const out = speak("Here is the config.\n\n```yaml\nid: plateloc\nbase_url: htt");
    expect(out).toContain("Here is the config.");
    expect(out).not.toContain("base_url");
  });

  it("never reads a table aloud", () => {
    const out = speak(
      "Three devices are unreachable.\n\n" +
        "| id | state |\n|---|---|\n| plateloc | error |\n| ot2_hte | unknown |\n"
    );
    expect(out).toContain("Three devices are unreachable.");
    expect(out).not.toContain("plateloc");
    expect(out).toContain("More on screen.");
  });

  it("speaks a marker rather than silence when the answer is pure structure", () => {
    expect(speak("```\nx = 1\n```")).toBe("The answer is on screen.");
    expect(speak("| a | b |\n|---|---|\n| 1 | 2 |")).toBe("The answer is on screen.");
  });

  it("keeps link text but not the URL", () => {
    const out = speak("See [the roadmap](http://100.64.254.6/docs/ROADMAP.md) for detail.");
    expect(out).toContain("the roadmap");
    expect(out).not.toContain("100.64");
    expect(out).not.toContain("http");
  });

  it("strips markdown emphasis, headings and bullets", () => {
    const out = speak("## Status\n\n- The **press** is `ready`.\n");
    expect(out).toContain("The press is ready.");
    expect(out).not.toContain("#");
    expect(out).not.toContain("*");
    expect(out).not.toContain("`");
  });

  it("makes snake_case ids pronounceable", () => {
    expect(speak("Device ot2_hte is ready.")).toContain("ot2 hte");
  });

  it("does not split a decimal into two sentences", () => {
    const out = speak("The temperature is 22.3 C right now.");
    expect(out).toBe("The temperature is 22.3 C right now.");
  });

  it("does not treat a short abbreviation as a sentence end", () => {
    const out = speak("Check the gateway, e.g. The OT-2 one, before restarting.");
    expect(out).toContain("e.g. The OT-2 one");
  });
});
