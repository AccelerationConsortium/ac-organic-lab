import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import { QueryProvider } from "@/lib/query-client";
import { UserAuthProvider } from "@/lib/user-auth";
import { AssistantBubble } from "@/components/AssistantBubble";
import { Nav } from "@/components/Nav";
import { StateReferencePanel } from "@/components/StateReferencePanel";
import { Logo } from "@/components/Logo";

// Sets the `dark` class before first paint (localStorage, else OS
// preference) so there's no flash of the wrong theme on load.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem("theme");
    var dark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.classList.toggle("dark", dark);
  } catch (e) {}
})();
`;

export const metadata: Metadata = {
  title: "Organic Self-driving Lab",
  description: "Live status of the lab and its platforms.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <Script id="theme-init" strategy="beforeInteractive">
          {THEME_INIT_SCRIPT}
        </Script>
      </head>
      {/*
        App shell: the banner, the nav pills and the footer are flex items of
        a viewport-height column, and <main> is the only scrolling box. The
        chrome therefore stays put without any `fixed`/`sticky` offsets — which
        matters because the auth banner's height is unknown at build time (it
        renders into a shadow root), so any hard-coded `top-…` for a sticky nav
        would drift the moment that banner changes.

        `100dvh` (not `100vh`) so mobile browsers subtract their collapsing
        URL bar instead of pushing the footer off-screen. Where `dvh` is
        unsupported the height simply doesn't apply and the page falls back to
        ordinary document scrolling — degraded, not broken. Printing likewise
        opts out, or `overflow-hidden` would clip everything past page one.
      */}
      <body className="flex h-[100dvh] flex-col overflow-hidden print:h-auto print:block print:overflow-visible">
        {/* Shared SDL2 auth banner — the same top bar every lab UI shows,
            served once by ac_auth at /auth/banner.js. It attaches its UI into
            this slot's shadow root, so React owns the light-DOM element and
            never reconciles the banner's markup (no hydration conflict).
            Loaded afterInteractive so it mounts once hydration is done. */}
        <div id="ac-auth-banner-slot" className="z-50 shrink-0" />
        <Script src="/auth/banner.js" strategy="afterInteractive" />
        <QueryProvider>
         <UserAuthProvider>
          {/* Pinned chrome: title, logo, then the nav tabs beneath them. */}
          <div className="shrink-0">
            <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 pt-6 sm:px-6 lg:px-8">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <h1 className="text-2xl font-semibold tracking-tight text-ink dark:text-slate-100 md:text-3xl">
                    Organic Self-driving Lab
                  </h1>
                  <p className="mt-1 text-base text-ink-muted dark:text-slate-400 md:text-lg">
                    Live Status of Lab and Platforms
                  </p>
                </div>
                <Logo />
              </div>
              <Nav />
            </div>
          </div>
          {/* The only scrolling region. `min-h-0` is load-bearing: without it
              a flex item refuses to shrink below its content's height and the
              page scrolls as a whole again.

              A page's own filter pills pin themselves to the top of THIS box
              with `stickyPillRow` (lib/pill.ts) — sticky inside the scroll
              container, so they need no knowledge of the chrome's height.

              `overscroll-none` kills the rubber-band bounce: past either end
              of the list, iOS would otherwise drag the whole scroll box (and
              the sticky pills riding on it) away from the chrome and let it
              spring back. It also stops the gesture from chaining outward. */}
          <main className="min-h-0 flex-1 overflow-y-auto overscroll-none print:overflow-visible">
            <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
              {children}
            </div>
          </main>
          {/* Pinned. */}
          <footer className="shrink-0 border-t border-slate-200 dark:border-slate-800 print:hidden">
            <div className="mx-auto w-full max-w-7xl px-4 py-2 text-xs text-ink-subtle dark:text-slate-500 sm:px-6 lg:px-8">
              Live dashboard · sign in to control · v2
            </div>
          </footer>
          <AssistantBubble />
          <StateReferencePanel />
         </UserAuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
