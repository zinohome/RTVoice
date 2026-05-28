"use client";

import Link from "next/link";
import {
  AudioLines,
  KeyRound,
  Mic,
  Radio,
  Speech,
  Ticket,
  Waves,
  type LucideIcon,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useMe } from "@/lib/hooks/use-me";

type Feature = { title: string; desc: string; icon: LucideIcon; href: string };
type Section = { title: string; features: Feature[] };

const SECTIONS: Section[] = [
  {
    title: "系统管理",
    features: [
      { title: "服务监控", desc: "各服务健康状态与版本", icon: Waves, href: "/monitor" },
      { title: "Key 发放", desc: "签发 / 吊销 / 轮换客户端 key", icon: KeyRound, href: "/keys" },
    ],
  },
  {
    title: "系统测试",
    features: [
      { title: "STT 转写", desc: "文件上传 + 麦克风录音", icon: Mic, href: "/test/stt" },
      { title: "TTS 合成", desc: "文本转语音试听", icon: Speech, href: "/test/tts" },
      { title: "Realtime", desc: "实时语音对话", icon: Radio, href: "/test/realtime" },
      { title: "LiveKit Token 签发", desc: "房间 JWT 签发测试", icon: Ticket, href: "/test/token" },
    ],
  },
  {
    title: "资源管理",
    features: [
      { title: "Voice 音色", desc: "音色列表 / 试听 / 新增 / 删除", icon: AudioLines, href: "/voices" },
    ],
  },
];

export default function HomePage() {
  const { data } = useMe();
  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          欢迎，{data?.username ?? "管理员"}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          RTVoice 控制台 · 选择下方模块开始管理与测试。
        </p>
      </div>

      {SECTIONS.map((section) => (
        <section key={section.title} className="space-y-3">
          <h2 className="text-sm font-medium text-muted-foreground">{section.title}</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {section.features.map((f) => {
              const Icon = f.icon;
              return (
                <Link key={f.title} href={f.href} className="block">
                  <Card className="transition-colors hover:border-primary/50 hover:bg-accent/40">
                    <CardHeader>
                      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
                        <Icon className="h-4.5 w-4.5 text-primary" />
                      </div>
                      <CardTitle className="mt-2 text-base">{f.title}</CardTitle>
                      <CardDescription>{f.desc}</CardDescription>
                    </CardHeader>
                    <CardContent className="text-xs text-primary">进入 →</CardContent>
                  </Card>
                </Link>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
