"use client";

import { Phone, PhoneOff, Loader2, Mic } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { apiFetch } from "@/lib/api";
import { startMicCapture, type MicRecorder } from "@/lib/audio";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/console-ui";

type Stage = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "error";

interface SessionResp {
  session_id: string;
  ws_url: string;
}

interface Line {
  role: "user" | "assistant";
  text: string;
}

// 客户端 VAD 阈值：归一化 RMS（int16/32768 后）。语音段 RMS 通常 >0.02，
// 静音底噪 <0.01。静音挂起 900ms 视为一句结束；最短语音 250ms 过滤误触发。
const SPEECH_RMS = 0.02;
const SILENCE_HANG_MS = 900;
const MIN_SPEECH_MS = 250;

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

/** 归一化 RMS 能量；int16 PCM bytes → [0,1) 区间能量值。 */
function frameRms(buf: ArrayBuffer): number {
  const pcm = new Int16Array(buf);
  if (pcm.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) {
    const s = pcm[i] / 32768;
    sum += s * s;
  }
  return Math.sqrt(sum / pcm.length);
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

  // VAD/通话状态镜像（onFrame 闭包内读取，避免 state 闭包过期）
  const stageRef = useRef<Stage>("idle");
  const speakingRef = useRef(false);       // 当前 utterance 是否已检测到语音
  const speechStartRef = useRef(0);         // 语音起始时间戳
  const lastVoiceRef = useRef(0);           // 最近一次高于阈值的时间戳

  useEffect(() => { stageRef.current = stage; }, [stage]);

  const resetVad = () => {
    speakingRef.current = false;
    speechStartRef.current = 0;
    lastVoiceRef.current = 0;
  };

  const playAudio = () => {
    if (audioChunks.current.length === 0) {
      // 没有音频也要恢复聆听
      if (stageRef.current !== "idle" && stageRef.current !== "error") setStage("listening");
      resetVad();
      return;
    }
    const total = audioChunks.current.reduce((n, c) => n + c.byteLength, 0);
    const merged = new Uint8Array(total);
    let off = 0;
    for (const c of audioChunks.current) { merged.set(c, off); off += c.byteLength; }
    audioChunks.current = [];
    const url = URL.createObjectURL(pcm16ToWav(merged));
    const a = new Audio(url);
    const resume = () => {
      URL.revokeObjectURL(url);
      // 回放结束后再恢复聆听 + 重置 VAD，避免把 TTS 回放采进麦克风形成自问自答
      if (stageRef.current !== "idle" && stageRef.current !== "error") setStage("listening");
      resetVad();
    };
    a.onended = resume;
    a.onerror = resume;
    a.play().catch(() => resume());
  };

  const onFrame = (frame: ArrayBuffer) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== 1) return;
    // 仅在“聆听中”采集并送流；思考/回复阶段静音，杜绝回声回环
    if (stageRef.current !== "listening") return;

    ws.send(frame);

    const now = Date.now();
    const rms = frameRms(frame);
    if (rms > SPEECH_RMS) {
      if (!speakingRef.current) {
        speakingRef.current = true;
        speechStartRef.current = now;
      }
      lastVoiceRef.current = now;
      return;
    }
    // 静音帧：若此前已有语音且静音超过挂起时长 → 视为一句话结束，自动断句
    if (speakingRef.current && now - lastVoiceRef.current > SILENCE_HANG_MS) {
      const speechMs = lastVoiceRef.current - speechStartRef.current;
      speakingRef.current = false;
      if (speechMs >= MIN_SPEECH_MS) {
        ws.send("audio.eos");
        setStage("thinking");
      }
      // 太短的语音视为噪声，丢弃后继续聆听
    }
  };

  const hangup = () => {
    micRef.current?.stop();
    micRef.current = null;
    if (wsRef.current && wsRef.current.readyState <= 1) wsRef.current.close();
    wsRef.current = null;
    resetVad();
    setStage("idle");
    setPartial("");
    setStreamingReply("");
  };

  const startCall = async () => {
    setErr("");
    setLines([]);
    setPartial("");
    setStreamingReply("");
    resetVad();
    setStage("connecting");
    try {
      const sess = await apiFetch<SessionResp>("/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ prompt, speed: 1.0 }),
      });
      const ws = new WebSocket(sess.ws_url);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = async () => {
        // 通话建立即开麦，全程保持，靠 VAD 自动断句
        try {
          micRef.current = await startMicCapture(onFrame);
          setStage("listening");
        } catch (e) {
          setErr("麦克风访问失败：" + (e as Error).message);
          toast.error("麦克风访问失败：" + (e as Error).message);
          hangup();
          setStage("error");
        }
      };
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
            playAudio(); // 回放结束后自动恢复聆听
            break;
          }
          case "error":
            setErr(m.message ?? "对话出错");
            toast.error(m.message ?? "对话出错");
            // 出错也恢复聆听，保持通话不中断
            if (stageRef.current !== "idle") setStage("listening");
            resetVad();
            break;
        }
      };
    } catch (e) {
      setErr((e as Error).message);
      setStage("error");
      toast.error((e as Error).message);
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => hangup(), []); // 卸载时清理麦克风/WS

  const inCall = stage !== "idle" && stage !== "connecting" && stage !== "error";
  const stageLabel: Record<Stage, string> = {
    idle: "未连接",
    connecting: "连接中…",
    listening: "聆听中 · 请讲话",
    thinking: "思考中…",
    speaking: "回复中…",
    error: "出错",
  };

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Realtime 实时对话" desc="点击开始通话后自动进入连续对话：你说话即识别，停顿后自动获得语音助手回复（STT → LLM → TTS 全链路），无需按住录音。" />

      <Card>
        <CardContent className="space-y-4 pt-6">
          {!inCall ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="rt-prompt">系统提示词（可选）</Label>
                <Input id="rt-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              </div>
              <Button onClick={startCall} disabled={stage === "connecting"}>
                {stage === "connecting" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Phone className="h-4 w-4" />}
                开始通话
              </Button>
            </>
          ) : (
            <div className="flex items-center gap-3">
              <Button variant="destructive" size="lg" onClick={hangup}>
                <PhoneOff className="h-4 w-4" />结束通话
              </Button>
              <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Mic className={"h-4 w-4 " + (stage === "listening" ? "text-emerald-500" : "")} />
                {stage === "listening" ? "正在聆听，说完停顿即自动发送" : "处理中…"}
              </span>
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
