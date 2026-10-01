// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EquipmentCameraButton, cameraSource } from "./EquipmentCameraButton";
const auth = { authenticated: true, requestLogin: vi.fn() };
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));
vi.mock("./CameraPlayer", () => ({ CameraPlayer: ({ src }: { src: string }) => <div data-testid="player">{src}</div> }));
afterEach(() => { cleanup(); auth.authenticated = true; auth.requestLogin.mockClear(); });
it("opens explicitly and unmounts player on close, toggle and Escape", () => {
  render(<EquipmentCameraButton stream="ot2_hte_overhead" label="Overhead" transport="mjpeg" />);
  expect(screen.queryByTestId("player")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Camera" }));
  expect(screen.getByTestId("player").textContent).toBe("/streams/api/ws?src=ot2_hte_overhead");
  fireEvent.click(screen.getByRole("button", { name: "Turn off and hide camera" }));
  expect(screen.queryByTestId("player")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Camera" }));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(screen.queryByTestId("player")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Camera" }));
  fireEvent.click(screen.getByRole("button", { name: "Camera" }));
  expect(screen.queryByTestId("player")).toBeNull();
});
it("requires sign-in before mounting a player", () => {
  auth.authenticated = false;
  render(<EquipmentCameraButton stream="ot2_hte_overhead" label="Overhead" transport="mjpeg" />);
  fireEvent.click(screen.getByRole("button", { name: "Camera" }));
  expect(auth.requestLogin).toHaveBeenCalledOnce();
  expect(screen.queryByTestId("player")).toBeNull();
});
it("passes a source the camera session layer can resolve", () => {
  const src = new URL(cameraSource("ot2_hte_overhead"), "https://dashboard.test");
  expect(src.searchParams.get("src")).toBe("ot2_hte_overhead");
});
