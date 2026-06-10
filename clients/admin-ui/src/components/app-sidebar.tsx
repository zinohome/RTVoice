"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AudioLines,
  BookOpen,
  Code2,
  FlaskConical,
  KeyRound,
  LayoutDashboard,
  Mic,
  Radio,
  Speech,
  Ticket,
  Waves,
  type LucideIcon,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { Badge } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/theme-toggle";

type NavItem = { href: string; label: string; icon: LucideIcon; soon?: boolean; external?: boolean };
type NavGroup = { label: string; items: NavItem[] };

// 信息架构（验收讨论敲定的三大块）。阶段① 仅「概览」可用，其余为后续阶段占位。
const GROUPS: NavGroup[] = [
  {
    label: "系统管理",
    items: [
      { href: "/home", label: "概览", icon: LayoutDashboard },
      { href: "/monitor", label: "服务监控", icon: Waves },
      { href: "/keys", label: "Key 发放", icon: KeyRound },
    ],
  },
  {
    label: "系统测试",
    items: [
      { href: "/test/stt", label: "STT 转写", icon: Mic },
      { href: "/test/tts", label: "TTS 合成", icon: Speech },
      { href: "/test/realtime", label: "Realtime", icon: Radio },
      { href: "/test/token", label: "LiveKit Token 签发", icon: Ticket },
    ],
  },
  {
    label: "资源管理",
    items: [{ href: "/voices", label: "Voice 音色", icon: AudioLines }],
  },
  {
    label: "参考文档",
    items: [
      { href: "/api", label: "API", icon: Code2 },
      {
        href: "https://github.com/zinohome/RTVoice",
        label: "文档",
        icon: BookOpen,
        external: true,
      },
    ],
  },
];

export function AppSidebar() {
  const pathname = usePathname();
  return (
    <Sidebar role="navigation" aria-label="主导航">
      <SidebarHeader className="px-4 py-3 border-b">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-5 w-5 text-primary" />
          <span className="font-semibold text-sm">RTVoice 控制台</span>
        </div>
      </SidebarHeader>
      <SidebarContent className="pt-1">
        {GROUPS.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel className="text-[10px] uppercase tracking-widest text-muted-foreground/60">
              {group.label}
            </SidebarGroupLabel>
            <SidebarMenu>
              {group.items.map((item, i) => {
                const Icon = item.icon;
                const active = !item.soon && !item.external && pathname === item.href;
                return (
                  <SidebarMenuItem key={`${item.label}-${i}`}>
                    <SidebarMenuButton
                      render={
                        item.external ? (
                          <a href={item.href} target="_blank" rel="noopener noreferrer" />
                        ) : (
                          <Link href={item.href} />
                        )
                      }
                      isActive={active}
                    >
                      <Icon className="h-4 w-4" />
                      <span>{item.label}</span>
                      {item.soon && (
                        <Badge variant="secondary" className="ml-auto text-[10px]">
                          建设中
                        </Badge>
                      )}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter className="border-t px-2 py-2">
        <ThemeToggle className="w-full" />
      </SidebarFooter>
    </Sidebar>
  );
}
