"use client";

import { AppSidebar } from "@/components/app-sidebar";
import { AuthGuard } from "@/components/auth-guard";
import { UserMenu } from "@/components/user-menu";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <SidebarProvider>
        <div className="flex h-full w-full">
          <AppSidebar />
          <main className="flex-1 flex flex-col min-w-0 overflow-auto">
            <header className="flex items-center gap-3 px-5 py-2.5 border-b bg-card/60 backdrop-blur-sm shrink-0">
              <SidebarTrigger className="-ml-1" aria-label="切换侧边栏" />
              <div className="h-4 w-px bg-border" />
              <div className="flex-1" />
              <UserMenu />
            </header>
            <div className="flex-1 p-5 sm:p-7 min-w-0 bg-mesh">{children}</div>
          </main>
        </div>
      </SidebarProvider>
    </AuthGuard>
  );
}
