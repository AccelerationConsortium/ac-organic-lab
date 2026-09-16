/** The browser never receives a relay URL or credentials. One lease per tab. */
export class CameraSessionError extends Error {
  constructor(message: string, public status = 0) { super(message); }
}

export async function openCameraSession(
  src: string, signal: AbortSignal, onError: (message: string) => void, grantId?: string,
): Promise<WebSocket> {
  const source = new URL(src, window.location.origin).searchParams.get("src");
  if (!source) throw new CameraSessionError("Unknown camera feed", 404);
  const response = await fetch("/api/camera-streams/sessions", {
    method: "POST", credentials: "same-origin", cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stream: source, ...(grantId ? { grant_id: grantId } : {}) }),
    // Do not abort minting: if the tab closes while the response is in flight,
    // we still collect its ID and release the reservation immediately below.
  });
  const body = await response.json();
  if (!response.ok) throw new CameraSessionError(body.detail ?? "Camera access unavailable", response.status);
  const endpoint = `/api/camera-streams/sessions/${encodeURIComponent(body.id)}`;
  let socket: WebSocket | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let ended = false;
  const release = () => {
    if (ended) return;
    ended = true;
    clearTimeout(timer);
    signal.removeEventListener("abort", release);
    socket?.close();
    void fetch(endpoint, { method: "DELETE", credentials: "same-origin", keepalive: true }).catch(() => {});
  };
  if (signal.aborted) { release(); throw new DOMException("Cancelled", "AbortError"); }
  signal.addEventListener("abort", release, { once: true });
  const url = new URL("/api/camera-streams/ws", window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(url.toString());
  const heartbeat = async () => {
    if (ended) return;
    try {
      const r = await fetch(`${endpoint}/heartbeat`, { method: "POST", credentials: "same-origin", cache: "no-store" });
      if (!r.ok) {
        const e = await r.json();
        onError(e.detail ?? "Camera viewing permission expired");
        release();
        return;
      }
    } catch {
      onError("Lost contact with camera authorization service");
      release();
      return;
    }
    if (!ended) timer = setTimeout(heartbeat, body.heartbeat_seconds * 1000);
  };
  // Registered before the player's onopen: authenticate before sending MSE/SDP.
  socket.addEventListener("open", () => {
    if (ended) return;
    socket?.send(JSON.stringify({ type: "session", value: body.ticket }));
    body.ticket = "";
    timer = setTimeout(heartbeat, body.heartbeat_seconds * 1000);
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data !== "string") return;
    try {
      const message = JSON.parse(event.data);
      if (message.type === "session/error") { onError(message.value); release(); }
    } catch { /* Player handles the media protocol. */ }
  });
  socket.addEventListener("close", release, { once: true });
  return socket;
}

/** Mint the same bounded viewing lease for a registered HTTP/MJPEG source. */
export async function openMjpegSession(
  src: string, signal: AbortSignal, onError: (message: string) => void, grantId?: string,
): Promise<string> {
  const source = new URL(src, window.location.origin).searchParams.get("src");
  if (!source) throw new CameraSessionError("Unknown camera feed", 404);
  const response = await fetch("/api/camera-streams/sessions", {
    method: "POST", credentials: "same-origin", cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stream: source, ...(grantId ? { grant_id: grantId } : {}) }),
  });
  const body = await response.json();
  if (!response.ok) throw new CameraSessionError(body.detail ?? "Camera access unavailable", response.status);
  const endpoint = `/api/camera-streams/sessions/${encodeURIComponent(body.id)}`;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let ended = false;
  const release = () => {
    if (ended) return;
    ended = true;
    clearTimeout(timer);
    signal.removeEventListener("abort", release);
    void fetch(endpoint, { method: "DELETE", credentials: "same-origin", keepalive: true }).catch(() => {});
  };
  if (signal.aborted) { release(); throw new DOMException("Cancelled", "AbortError"); }
  signal.addEventListener("abort", release, { once: true });
  if (body.transport !== "mjpeg") {
    release();
    throw new CameraSessionError("Registered camera transport does not provide MJPEG", 409);
  }
  const heartbeat = async () => {
    if (ended) return;
    try {
      const r = await fetch(`${endpoint}/heartbeat`, { method: "POST", credentials: "same-origin", cache: "no-store" });
      if (!r.ok) {
        const e = await r.json();
        onError(e.detail ?? "Camera viewing permission expired");
        release();
        return;
      }
    } catch {
      onError("Lost contact with camera authorization service");
      release();
      return;
    }
    if (!ended) timer = setTimeout(heartbeat, body.heartbeat_seconds * 1000);
  };
  timer = setTimeout(heartbeat, body.heartbeat_seconds * 1000);
  return `${endpoint}/mjpeg`;
}
