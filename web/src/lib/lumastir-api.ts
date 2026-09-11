import { ApiError } from "./api";

export async function lumastirCommand<T = unknown>(id: string, action: string,
  body: Record<string, unknown> = {}, token?: string, keepalive = false): Promise<T> {
  const response = await fetch(`/api/equipment/${encodeURIComponent(id)}/control/${action}`, {
    method: "POST", credentials: "same-origin", keepalive,
    headers: { "Content-Type": "application/json", ...(token ? { "X-Claim-Token": token } : {}) },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new ApiError(response.status, response.statusText, data, response.url, null);
  return data as T;
}
