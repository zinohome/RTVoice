"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader, StatusDot } from "@/components/console-ui";

interface ServiceStatus {
  name: string;
  status: string;
  version: string | null;
  detail: string | null;
}

export default function MonitorPage() {
  const { data, isLoading, isFetching, refetch } = useQuery<ServiceStatus[]>({
    queryKey: ["console", "services"],
    queryFn: () => apiFetch<ServiceStatus[]>("/v1/console/services"),
    refetchInterval: 10_000,
  });

  const healthy = data?.filter((s) => s.status === "healthy").length ?? 0;
  const total = data?.length ?? 0;

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex items-start justify-between">
        <PageHeader title="服务监控" desc="各内部服务的健康状态与版本（每 10 秒自动刷新）" />
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={isFetching ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
          刷新
        </Button>
      </div>

      {!isLoading && (
        <p className="mb-4 text-sm text-muted-foreground">
          健康 <span className="font-semibold text-foreground">{healthy}</span> / {total} 个服务
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {isLoading
          ? Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28 rounded-lg" />)
          : data?.map((s) => (
              <Card key={s.name}>
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base">{s.name}</CardTitle>
                    <StatusDot ok={s.status === "healthy"} />
                  </div>
                </CardHeader>
                <CardContent className="space-y-1.5 text-sm">
                  <div className="flex items-center gap-2">
                    <Badge variant={s.status === "healthy" ? "secondary" : "destructive"}>
                      {s.status === "healthy" ? "运行中" : "不可用"}
                    </Badge>
                    {s.version && <span className="text-muted-foreground">v{s.version}</span>}
                  </div>
                  {s.detail && <p className="text-xs text-muted-foreground">{s.detail}</p>}
                </CardContent>
              </Card>
            ))}
      </div>
    </div>
  );
}
