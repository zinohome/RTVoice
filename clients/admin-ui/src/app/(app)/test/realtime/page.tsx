"use client";

import { Mic, PhoneOff, Loader2, Radio } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { startMicCapture, type MicRecorder } from "@/lib/audio";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/console-ui";

type Stage = "idle" | "connecting" | "ready" | "recording" | "thinking" | "speaking" | "error";

interface SessionResp {
  session_id: string;
  ws_url: string;
}

interface Line {
  role: "user" | "assistant";
  text: string;
}

function pcm16ToWav(pcm: Uint8Array, rate = 24000): Blob {
  const header = new ArrayBuffer(44);
  const dv = new DataView(header);
  const w = (off: number, s: string) => { for (let i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i)); };
  const dataLen = pcm.byteLength;
  w(0, "RIFF"); dv.setUint32(4, 36 + dataLen, true); w(8, "WAVE"); w(12, "fmt ");
  dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, rate, true); dv.setUint32(28, rate * 2, true);
  dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
  w(36, "data"); dv.setUint32(40, dataLen, true);
  const body = pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength) as ArrayBuffer;
  return new Blob([header, body], { type: "audio/wav" });
}

export default function RealtimeTestPage() {
  const [stage, setStage] = useState<Stage>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [partial, setPartial] = useState("");
  const [streamingReply, setStreamingReply] = useState("");
  const [err, setErr] = useState("");
  const [prompt, setPrompt] = useState("你是语音助手。用中文简短回答（≤2 句）。");

  const wsRef = useRef<WebSocket | null>(null);
  const micRef = useRef<MicRecorder | null>(null);
  const audioChunks = useRef<Uint8Array[]>([]);
  const replyRef = useRef("");

  const playAudio = () => {
    if (audioChunks.current.length === 0) return;
    const total = audioChunks.current.reduce((n, c) => n + c.byteLength, 0);
    const merged = new Uint8Array(total);
    let off = 0;
    for (const c of audioChunks.current) { merged.set(c, off); off += c.byteLength; }
    audioChunks.current = [];
    const url = URL.createObjectURL(pcm16ToWav(merged));
    const a = new Audio(url);
    a.play().catch(() => {});
    a.onended = () => URL.revokeObjectURL(url);
  };

  const hangup = () => {
    micRef.current?.stop();
    micRef.current = null;
    if (wsRef.current && wsRef.current.readyState <= 1) wsRef.current.close();
    wsRef.current = null;
    setStage("idle");
    setPartial("");
    setStreamingReply("");
  };

  const connect = async () => {
    setErr("");
    setLines([]);
    setStage("connecting");
    try {
      const sess = await apiFetch<SessionResp>("/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ prompt, speed: 1.0 }),
      });
      const ws = new WebSocket(sess.ws_url);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => setStage("ready");
      ws.onerror = () => { setErr("WebSocket 连接失败"); setStage("error"); };
      ws.onclose = () => setStage((s) => (s === "error" ? s : "idle"));
      ws.onmessage = (ev) => {
        if (ev.data instanceof ArrayBuffer) {
          audioChunks.current.push(new Uint8Array(ev.data));
          return;
        }
        let m: { type?: string; text?: string; message?: string };
        try { m = JSON.parse(ev.data as string); } catch { return; }
        switch (m.type) {
          case "transcript.partial":
            setPartial(m.text ?? "");
            break;
          case "transcript.final":
            if (m.text) setLines((p) => [...p, { role: "user", text: m.text! }]);
            setPartial("");
            replyRef.current = "";
            setStreamingReply("");
            setStage("thinking");
            break;
          case "response.text":
            replyRef.current += m.text ?? "";
            setStreamingReply(replyRef.current);
            setStage("speaking");
            break;
          case "response.done": {
            const full = m.text ?? replyRef.current;
            if (full) setLines((p) => [...p, { role: "assistant", text: full }]);
            setStreamingReply("");
            replyRef.current = "";
            playAudio();
            setStage("ready");
            break;
          }
          case "error":
            setErr(m.message ?? "对话出错");
            toast.error(m.message ?? "对话出错");
            setStage("ready");
            break;
        }
      };
    } catch (e) {
      setErr((e as Error).message);
      setStage("error");
      toast.error((e as Error).message);
    }
  };

  const startTalk = async () => {
    if (!wsRef.current || wsRef.current.readyState !== 1) return;
    audioChunks.current = [];
    try {
      const rec = await startMicCapture((frame) => {
        if (wsRef.current?.readyState === 1) wsRef.current.send(frame);
      });
      micRef.current = rec;
      setStage("recording");
    } catch (e) {
      toast.error("麦克风访问失败：" + (e as Error).message);
    }
  };

  const endTalk = () => {
    micRef.current?.stop();
    micRef.current = null;
    if (wsRef.current?.readyState === 1) {
      wsRef.current.send("audio.eos");
      setStage("thinking");
    }
  };

  const connected = stage !== "idle" && stage !== "connecting" && stage !== "error";
  const stageLabel: Record<Stage, string> = {
    idle: "未连接", connecting: "连接中…", ready: "已就绪 · 按住说话",
    recording: "录音中…松开发送", thinking: "思考中…", speaking: "回复中…", error: "出错",
  };

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Realtime 实时对话" desc="建立会话后按住麦克风说话，松开即获得语音助手回复（STT → LLM → TTS 全链路）。" />

      <Card>
        <CardContent className="space-y-4 pt-6">
          {!connected ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="rt-prompt">系统提示词（可选）</Label>
                <Input id="rt-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              </div>
              <Button onClick={connect} disabled={stage === "connecting"}>
                {stage === "connecting" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Radio className="h-4 w-4" />}
                建立会话
              </Button>
            </>
          ) : (
            <div className="flex items-center gap-3">
              <Button
                size="lg"
                variant={stage === "recording" ? "destructive" : "default"}
                onMouseDown={startTalk}
                onMouseUp={endTalk}
                onMouseLeave={() => { if (stage === "recording") endTalk(); }}
                onTouchStart={(e) => { e.preventDefault(); startTalk(); }}
                onTouchEnd={(e) => { e.preventDefault(); endTalk(); }}
                disabled={stage === "thinking" || stage === "speaking"}
              >
                <Mic className="h-4 w-4" />
                {stage === "recording" ? "松开发送" : "按住说话"}
              </Button>
              <Button variant="outline" onClick={hangup}>
                <PhoneOff className="h-4 w-4" />结束
              </Button>
            </div>
          )}

          <div className="flex items-center gap-2">
            <Badge variant={stage === "error" ? "destructive" : "secondary"}>{stageLabel[stage]}</Badge>
            {(stage === "thinking" || stage === "speaking") && (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
            )}
          </div>

          {(lines.length > 0 || partial || streamingReply) && (
            <div className="space-y-3 rounded-md border bg-muted/40 p-4">
              {lines.map((l, i) => (
                <div key={i} className={l.role === "user" ? "text-right" : "text-left"}>
                  <span className={
                    "inline-block max-w-[80%] rounded-lg px-3 py-1.5 text-sm " +
                    (l.role === "user" ? "bg-primary text-primary-foreground" : "bg-card border")
                  }>
                    {l.text}
                  </span>
                </div>
              ))}
              {partial && <p className="text-right text-sm italic text-muted-foreground">{partial}</p>}
              {streamingReply && (
                <div className="text-left">
                  <span className="inline-block max-w-[80%] rounded-lg border bg-card px-3 py-1.5 text-sm">
                    {streamingReply}
                  </span>
                </div>
              )}
            </div>
          )}
          {err && <p className="text-sm text-destructive">{err}</p>}
        </CardContent>
      </Card>
    </div>
  );
}
