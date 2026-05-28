"use client";

import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useMe } from "@/lib/hooks/use-me";

export function UserMenu() {
  const router = useRouter();
  const qc = useQueryClient();
  const { data } = useMe();

  async function logout() {
    try {
      await apiFetch("/auth/logout", { method: "POST", skipAuthRedirect: true });
    } catch {
      /* 即使后端报错也清前端状态 */
    }
    qc.clear();
    toast.success("已退出登录");
    router.replace("/login");
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted-foreground">{data?.username ?? ""}</span>
      <Button variant="ghost" size="icon" onClick={logout} aria-label="退出登录" title="退出登录">
        <LogOut className="h-4 w-4" />
      </Button>
    </div>
  );
}
