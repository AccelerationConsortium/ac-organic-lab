// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { openCameraSession } from "./camera-session";

class Socket extends EventTarget {
  static made: Socket[] = [];
  send = vi.fn();
  close = vi.fn();
  constructor(public url: string) { super(); Socket.made.push(this); }
}

describe("camera viewing leases", () => {
  beforeEach(() => { vi.useFakeTimers(); Socket.made = []; vi.stubGlobal("WebSocket", Socket); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
  const minted = () => ({ ok: true, status: 201, json: async () => ({ id: "s", ticket: "secret", heartbeat_seconds: 20 }) });

  it("does not open a socket when permission or capacity is denied", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 429, json: async () => ({ detail: "Capacity in use" }) }));
    await expect(openCameraSession('/streams/api/ws?src=cam_main', new AbortController().signal, vi.fn())).rejects.toThrow("Capacity in use");
    expect(Socket.made).toHaveLength(0);
  });
  it("sends ticket only in first frame and releases on tab cleanup", async () => {
    const fetcher = vi.fn().mockResolvedValue(minted()); vi.stubGlobal("fetch", fetcher);
    const abort = new AbortController();
    await openCameraSession('/streams/api/ws?src=cam_main', abort.signal, vi.fn());
    const socket = Socket.made[0];
    expect(socket.url).not.toContain("secret");
    socket.dispatchEvent(new Event('open'));
    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ type: 'session', value: 'secret' }));
    abort.abort();
    expect(socket.close).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenLastCalledWith('/api/camera-streams/sessions/s', expect.objectContaining({ method: 'DELETE' }));
    await vi.advanceTimersByTimeAsync(60000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("releases a reservation if minting finishes after the tab closes", async () => {
    const abort = new AbortController(); abort.abort();
    const fetcher = vi.fn().mockResolvedValue(minted()); vi.stubGlobal('fetch',fetcher);
    await expect(openCameraSession('/streams/api/ws?src=cam_main',abort.signal,vi.fn())).rejects.toThrow('Cancelled');
    expect(Socket.made).toHaveLength(0);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("closes without reconnecting when lease renewal is denied", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(minted()).mockResolvedValueOnce({ ok:false,json:async()=>({detail:'Revoked'}) }).mockResolvedValue({status:204});
    vi.stubGlobal('fetch',fetcher);
    const error = vi.fn();
    await openCameraSession('/streams/api/ws?src=cam_main',new AbortController().signal,error);
    Socket.made[0].dispatchEvent(new Event('open'));
    await vi.advanceTimersByTimeAsync(20000);
    expect(error).toHaveBeenCalledWith('Revoked');
    expect(Socket.made[0].close).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(120000);
    expect(Socket.made).toHaveLength(1);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});
