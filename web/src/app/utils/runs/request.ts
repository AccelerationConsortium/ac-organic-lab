export async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/workflow${path}`, {
    ...(body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.json();
}
