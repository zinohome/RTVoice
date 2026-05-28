"use client";

import { useQuery } from "@tanstack/react-query";
import { Play, Loader2 } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { apiBlob, apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/console-ui";

export default function TtsTestPage() {
  const { data: voicesData } = useQuery<{ voices: string[] }>({
    queryKey: ["console", "voices"],
    queryFn: () => apiFetch<{ voices: string[] }>("/v1/console/voices"),
  });
  const voices = voicesData?.voices ?? [];

  const [text, setText] = useState("你好，欢迎使用 RTVoice 实时语音合成。");
  const [voice, setVoice] = useState("default_zh_female");
  const [speed, setSpeed] = useState(1.0);
  const [busy, setBusy] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  const synth = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      const blob = await apiBlob("/v1/console/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice, speed }),
      });
      const url = URL.createObjectURL(blob);
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      setAudioUrl(url);
      toast.success(`合成完成（${(blob.size / 1024).toFixed(0)} KB）`);
      setTimeout(() => audioRef.current?.play().catch(() => {}), 100);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="TTS 合成" desc="输入文本，选择音色与语速，合成并试听。" />
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="space-y-1.5">
            <Label htmlFor="tts-text">文本</Label>
            <textarea
              id="tts-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="tts-voice">音色</Label>
              <select
                id="tts-voice"
                value={voice}
                onChange={(e) => setVoice(e.target.value)}
                className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {voices.length === 0 && <option value="default_zh_female">default_zh_female</option>}
                {voices.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="tts-speed">语速：{speed.toFixed(2)}×</Label>
              <input
                id="tts-speed"
                type="range"
                min={0.5}
                max={2}
                step={0.05}
                value={speed}
                onChange={(e) => setSpeed(Number(e.target.value))}
                className="w-full"
              />
            </div>
          </div>
          <Button onClick={synth} disabled={busy || !text.trim()}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            合成并播放
          </Button>
          {audioUrl && (
            <audio ref={audioRef} src={audioUrl} controls className="w-full" />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
