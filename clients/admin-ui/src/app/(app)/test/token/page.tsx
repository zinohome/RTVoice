"use client";

import { Ticket, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader, CopyButton } from "@/components/console-ui";

interface TokenResp {
  token: string;
  url: string;
  room: string;
  identity: string;
}

export default function TokenTestPage() {
  const [room, setRoom] = useState("rtvoice-test");
  const [identity, setIdentity] = useState("user-1");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TokenResp | null>(null);

  const sign = async () => {
    setBusy(true);
    try {
      const r = await apiFetch<TokenResp>("/v1/console/tokens", {
        method: "POST",
        body: JSON.stringify({ room, identity }),
      });
      setResult(r);
      toast.success("Token 已签发");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="LiveKit Token 签发"
        desc="为 LiveKit 房间签发参与者 JWT（有效期 1 小时）。客户端用它加入实时音视频房间。"
      />
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="tk-room">房间名 (room)</Label>
              <Input id="tk-room" value={room} onChange={(e) => setRoom(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="tk-id">参与者 (identity)</Label>
              <Input id="tk-id" value={identity} onChange={(e) => setIdentity(e.target.value)} />
            </div>
          </div>
          <Button onClick={sign} disabled={busy || !room.trim() || !identity.trim()}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Ticket className="h-4 w-4" />}
            签发 Token
          </Button>

          {result && (
            <div className="space-y-3 rounded-md border bg-muted/40 p-4">
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <Label>JWT Token</Label>
                  <CopyButton value={result.token} />
                </div>
                <code className="block max-h-32 overflow-auto rounded bg-muted px-3 py-2 text-xs break-all">
                  {result.token}
                </code>
              </div>
              <div className="text-sm">
                <span className="text-muted-foreground">LiveKit URL：</span>
                <code className="text-xs">{result.url}</code>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
