// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ComponentList } from "./ComponentList";

afterEach(cleanup);

describe("ComponentList control modes", () => {
  it("shows Flex HTTP and REPL ownership as ON/OFF pills", () => {
    render(
      <ComponentList
        components={{
          robot_server: { connected: true, state: "up" },
          repl_session: { connected: false, state: "none" },
          camera: { connected: false, state: "off" },
        }}
      />,
    );

    expect(screen.getByLabelText("HTTP ON").textContent).toBe("ON");
    expect(screen.getByLabelText("REPL OFF").textContent).toBe("OFF");
    expect(screen.getByText("Camera")).toBeTruthy();
    expect(screen.getByText("off")).toBeTruthy();
  });
});
