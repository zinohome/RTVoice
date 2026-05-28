"use client";

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface AdminMe {
  username: string;
}

/** 拉 /auth/me 校验会话。401 不重定向（由 AuthGuard 决定跳转），失败即视为未登录。 */
export function useMe() {
  return useQuery<AdminMe>({
    queryKey: ["me"],
    queryFn: () => apiFetch<AdminMe>("/auth/me", { skipAuthRedirect: true }),
    retry: false,
    staleTime: 30_000,
  });
}
