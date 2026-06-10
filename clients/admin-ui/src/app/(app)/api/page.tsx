"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import "swagger-ui-react/swagger-ui.css";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const SwaggerUI = dynamic(() => import("swagger-ui-react"), { ssr: false });

const SPECS = [
  { id: "realtime", label: "Realtime", url: "/openapi/realtime.json" },
  { id: "stt", label: "STT", url: "/openapi/stt.json" },
  { id: "tts", label: "TTS", url: "/openapi/tts.json" },
  { id: "token", label: "Token", url: "/openapi/token.json" },
];

export default function ApiPage() {
  const [active, setActive] = useState(SPECS[0].id);
  const spec = SPECS.find((s) => s.id === active)!;

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">API 接口文档</h1>
        <p className="mt-1 text-sm text-muted-foreground">选择服务查看对应 OpenAPI / Swagger 文档</p>
      </div>

      <Tabs value={active} onValueChange={setActive}>
        <TabsList>
          {SPECS.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>
              {s.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="rounded-lg border bg-white dark:bg-zinc-950 overflow-hidden">
        <SwaggerUI key={spec.url} url={spec.url} />
      </div>
    </div>
  );
}
