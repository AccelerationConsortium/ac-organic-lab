import type { Metadata } from "next";
import "./globals.css";
import { QueryProvider } from "@/lib/query-client";
import { ControlAuthProvider } from "@/lib/control-auth";
import { AssistantBubble } from "@/components/AssistantBubble";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "Organic Self-driving Lab",
  description: "Live status of the lab and its platforms.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <QueryProvider>
         <ControlAuthProvider>
          <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
            <header className="flex flex-col gap-4">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <h1 className="text-2xl font-semibold tracking-tight text-ink dark:text-slate-100 md:text-3xl">
                    Organic Self-driving Lab
                  </h1>
                  <p className="mt-1 text-base text-ink-muted dark:text-slate-400 md:text-lg">
                    Acceleration Consortium | Live Status of Lab and Platforms
                  </p>
                </div>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src="/logo-uoft-ac.png"
                  alt="University of Toronto · Acceleration Consortium"
                  className="hidden h-[60px] w-auto shrink-0 sm:block md:h-[72px]"
                />
              </div>
              <Nav />
            </header>
            <main>{children}</main>
            <footer className="pt-6 text-xs text-ink-subtle dark:text-slate-500">
              Read-only dashboard · v1
            </footer>
          </div>
          <AssistantBubble />
         </ControlAuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
