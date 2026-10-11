import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { AUTH_COOKIE_NAME } from "@/lib/auth-service";
import { dashboardPreviewAccess } from "@/lib/dashboard-preview-access";
import OverviewContent from "@/components/overview/OverviewContent";
import PlatformsPage from "@/app/platforms/page";
import { InventoryContent } from "@/components/InventoryContent";
import { WorkspaceShell } from "@/components/WorkspaceShell";
import { MockBookings, MockContactAdmin, PreviewHome } from "@/components/DashboardPreviewContent";

export const dynamic = "force-dynamic";

// Check every page/RSC request, independently of middleware and client chrome.
export default async function DashboardPreviewPage({ params }: { params: { screen?: string[] } }) {
  const access = await dashboardPreviewAccess(cookies().get(AUTH_COOKIE_NAME)?.value);
  if (access === 404) notFound();
  if (access !== 200) redirect("/");

  const screen = params.screen?.join("/") ?? "";
  const pages: Record<string, { title: string; content: React.ReactNode }> = {
    "": { title: "Home", content: <PreviewHome /> },
    overview: { title: "Overview", content: <OverviewContent /> },
    platforms: { title: "Platforms", content: <PlatformsPage /> },
    inventory: { title: "Inventory", content: <InventoryContent className="h-[70dvh] min-h-[320px]" /> },
    bookings: { title: "Bookings · Mock", content: <MockBookings /> },
    "contact-admin": { title: "Contact admin · Mock", content: <MockContactAdmin /> },
  };
  const page = Object.hasOwn(pages, screen) ? pages[screen] : undefined;
  if (!page) notFound();
  return <WorkspaceShell title={page.title}>{page.content}</WorkspaceShell>;
}
