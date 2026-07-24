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
      <body className="min-h-screen">
        {/* Shared SDL2 auth banner — the same top bar every lab UI shows,
            served once by ac_auth at /auth/banner.js. It attaches its UI into
            this slot's shadow root, so React owns the light-DOM element and
            never reconciles the banner's markup (no hydration conflict).
            Loaded afterInteractive so it mounts once hydration is done. */}
        <div id="ac-auth-banner-slot" className="sticky top-0 z-50" />
        <Script src="/auth/banner.js" strategy="afterInteractive" />
        <QueryProvider>
         <UserAuthProvider>
          <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
            <header className="flex flex-col gap-4">
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
            </header>
            <main>{children}</main>
            <footer className="pt-6 text-xs text-ink-subtle dark:text-slate-500">
              Live dashboard · sign in to control · v2
            </footer>
          </div>
          <AssistantBubble />
          <StateReferencePanel />
         </UserAuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
