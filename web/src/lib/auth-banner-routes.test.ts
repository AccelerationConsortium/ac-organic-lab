import { expect, it } from "vitest";

it("exposes only the shared banner and existing public login handlers on direct URLs", async () => {
  // Test the actual Next rewrite configuration, including internal API aliases.
  // @ts-expect-error Next's JS config intentionally has no declaration file.
  const { default: config } = await import("../../next.config.mjs");
  const routes = (await config.rewrites()) as { source: string; destination: string }[];
  const auth = routes.filter((r) => r.source.startsWith("/auth/"));
  expect(auth.map((r) => r.source).sort()).toEqual([
    "/auth/banner.js", "/auth/login", "/auth/logout", "/auth/me", "/auth/users", "/auth/verify-code",
  ]);
  for (const path of ["me", "users", "login", "verify-code", "logout"]) {
    expect(auth.find((r) => r.source === `/auth/${path}`)?.destination).toBe(`/api/auth/${path}`);
  }
  expect(auth.find((r) => r.source === "/auth/banner.js")?.destination).toBe(
    "/api/auth/banner",
  );
});
