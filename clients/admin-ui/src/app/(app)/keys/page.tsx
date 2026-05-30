"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, RotateCw, Ban, TriangleAlert, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader, CopyButton } from "@/components/console-ui";

const ALL_SCOPES = ["stt", "tts", "tokens", "realtime", "admin"] as const;

interface KeySummary {
  id: string;
  name: string;
  scopes: string[];
  sessions_concurrent_max: number;
  sessions_per_hour_max: number;
  created_at: string;
  revoked_at: string | null;
  legacy: boolean;
  notes: string;
}

interface SecretResult {
  id: string;
  secret: string;
  title: string;
}

export default function KeysPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<KeySummary[]>({
    queryKey: ["admin", "keys"],
    queryFn: () => apiFetch<KeySummary[]>("/v1/admin/keys"),
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [secret, setSecret] = useState<SecretResult | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);

  // 创建表单
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["stt", "tts", "realtime", "tokens"]);
  const [concurrent, setConcurrent] = useState(5);
  const [perHour, setPerHour] = useState(100);
  const [notes, setNotes] = useState("");

  const refresh = () => qc.invalidateQueries({ queryKey: ["admin", "keys"] });

  const createMut = useMutation({
    mutationFn: () =>
      apiFetch<{ id: string; secret: string }>("/v1/admin/keys", {
        method: "POST",
        body: JSON.stringify({
          name,
          scopes,
          sessions_concurrent: concurrent,
          sessions_per_hour: perHour,
          notes,
        }),
      }),
    onSuccess: (r) => {
      setCreateOpen(false);
      setSecret({ id: r.id, secret: r.secret, title: "Key 已创建" });
      setName("");
      setNotes("");
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const rotateMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ id: string; secret: string }>(`/v1/admin/keys/${id}/rotate`, { method: "POST" }),
    onSuccess: (r) => {
      setSecret({ id: r.id, secret: r.secret, title: "Secret 已轮换（旧 secret 立即失效）" });
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/admin/keys/${id}/revoke`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Key 已吊销");
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/v1/admin/keys/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("已删除吊销 Key");
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const purgeMut = useMutation({
    mutationFn: () =>
      apiFetch<{ deleted: number }>("/v1/admin/keys/purge-revoked", { method: "POST" }),
    onSuccess: (r) => {
      toast.success(`已清除 ${r.deleted} 个已吊销 Key`);
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const toggleScope = (s: string) =>
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));

  const revokedCount = data?.filter((k) => k.revoked_at !== null).length ?? 0;
  const visibleKeys = showRevoked ? data : data?.filter((k) => k.revoked_at === null);

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex items-start justify-between">
        <PageHeader title="Key 发放" desc="为客户端签发访问凭证。Secret 仅在创建/轮换时显示一次，请妥善保存。" />
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          新建 Key
        </Button>
      </div>

      <div className="mb-3 mt-1 flex items-center gap-2">
        <button
          type="button"
          role="switch"
          aria-checked={showRevoked}
          onClick={() => setShowRevoked((v) => !v)}
          className={
            "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors " +
            (showRevoked ? "bg-primary" : "bg-input")
          }>
          <span
            className={
              "inline-block h-4 w-4 rounded-full bg-background shadow transition-transform " +
              (showRevoked ? "translate-x-4" : "translate-x-0.5")
            }
          />
        </button>
        <Label
          className="cursor-pointer text-sm text-muted-foreground"
          onClick={() => setShowRevoked((v) => !v)}>
          显示已吊销 Key
          {!showRevoked && revokedCount > 0 && (
            <span className="ml-1">（已隐藏 {revokedCount} 个）</span>
          )}
        </Label>
        {revokedCount > 0 && (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto text-destructive hover:text-destructive"
            disabled={purgeMut.isPending}
            onClick={() => {
              if (confirm(`确认清除全部 ${revokedCount} 个已吊销 Key？此操作永久删除，不可恢复。`))
                purgeMut.mutate();
            }}>
            <Trash2 className="h-3.5 w-3.5" />一键清除已吊销
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
        </div>
      ) : visibleKeys && visibleKeys.length === 0 ? (
        <Card className="p-8 text-center text-sm text-muted-foreground">
          {revokedCount > 0
            ? "暂无活跃 Key，已吊销的 Key 已隐藏（打开上方开关可查看）。"
            : "暂无 Key，点击右上角「新建 Key」开始签发。"}
        </Card>
      ) : (
        <div className="space-y-2">
          {visibleKeys?.map((k) => {
            const active = k.revoked_at === null;
            return (
              <Card key={k.id} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0 space-y-1">
                  <div className="flex items-center gap-2">
                    <KeyRound className="h-4 w-4 text-muted-foreground shrink-0" />
                    <span className="font-medium truncate">{k.name}</span>
                    {active ? (
                      <Badge variant="secondary" className="text-[10px]">活跃</Badge>
                    ) : (
                      <Badge variant="destructive" className="text-[10px]">已吊销</Badge>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    <code className="text-xs text-muted-foreground">{k.id}</code>
                    {k.scopes.map((s) => (
                      <Badge key={s} variant="outline" className="text-[10px]">{s}</Badge>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    并发 {k.sessions_concurrent_max} · 每小时 {k.sessions_per_hour_max}
                  </p>
                </div>
                {active ? (
                  <div className="flex gap-2 shrink-0">
                    <Button variant="outline" size="sm" onClick={() => rotateMut.mutate(k.id)}
                      disabled={rotateMut.isPending}>
                      <RotateCw className="h-3.5 w-3.5" />轮换
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => {
                      if (confirm(`确认吊销 Key「${k.name}」？吊销后该 key 立即失效，不可恢复。`))
                        revokeMut.mutate(k.id);
                    }} disabled={revokeMut.isPending}>
                      <Ban className="h-3.5 w-3.5" />吊销
                    </Button>
                  </div>
                ) : (
                  <div className="flex gap-2 shrink-0">
                    <Button variant="outline" size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => {
                        if (confirm(`确认删除已吊销 Key「${k.name}」？此操作永久删除，不可恢复。`))
                          deleteMut.mutate(k.id);
                      }} disabled={deleteMut.isPending}>
                      <Trash2 className="h-3.5 w-3.5" />删除
                    </Button>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {/* 创建对话框 */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建 Key</DialogTitle>
            <DialogDescription>为客户端签发一把访问凭证，勾选所需权限范围。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="key-name">名称</Label>
              <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)}
                placeholder="例如 mobile-app-prod" />
            </div>
            <div className="space-y-1.5">
              <Label>权限范围（scopes）</Label>
              <div className="flex flex-wrap gap-2">
                {ALL_SCOPES.map((s) => (
                  <button key={s} type="button" onClick={() => toggleScope(s)}
                    className={
                      "rounded-md border px-2.5 py-1 text-xs transition-colors " +
                      (scopes.includes(s)
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-input text-muted-foreground hover:bg-accent")
                    }>
                    {s}
                  </button>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="key-conc">最大并发</Label>
                <Input id="key-conc" type="number" min={1} max={100} value={concurrent}
                  onChange={(e) => setConcurrent(Number(e.target.value))} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="key-hour">每小时上限</Label>
                <Input id="key-hour" type="number" min={1} max={10000} value={perHour}
                  onChange={(e) => setPerHour(Number(e.target.value))} />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="key-notes">备注（可选）</Label>
              <Input id="key-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button
              onClick={() => createMut.mutate()}
              disabled={createMut.isPending || !name.trim() || scopes.length === 0}>
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Secret 一次性展示 */}
      <Dialog open={secret !== null} onOpenChange={(o) => !o && setSecret(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{secret?.title}</DialogTitle>
            <DialogDescription className="flex items-center gap-1.5 text-amber-600 dark:text-amber-500">
              <TriangleAlert className="h-4 w-4" />
              Secret 只显示这一次，关闭后无法再次查看。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <Label>Key ID</Label>
              <code className="block rounded-md bg-muted px-3 py-2 text-sm break-all">{secret?.id}</code>
            </div>
            <div className="space-y-1">
              <Label>Secret</Label>
              <code className="block rounded-md bg-muted px-3 py-2 text-sm break-all">{secret?.secret}</code>
            </div>
          </div>
          <DialogFooter>
            {secret && <CopyButton value={secret.secret} label="复制 Secret" />}
            <Button onClick={() => setSecret(null)}>我已保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
