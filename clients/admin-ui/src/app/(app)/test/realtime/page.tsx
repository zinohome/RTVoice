"use client";

import { Phone, PhoneOff, Loader2, Mic } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
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
  const { data: voicesData } = useQuery<{ voices: string[] }>({
    queryKey: ["console", "voices"],
    queryFn: () => apiFetch<{ voices: string[] }>("/v1/console/voices"),
  });
  const voices = voicesData?.voices ?? [];

  const [stage, setStage] = useState<Stage>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [partial, setPartial] = useState("");
  const [streamingReply, setStreamingReply] = useState("");
  const [err, setErr] = useState("");
  const [prompt, setPrompt] = useState("你是语音助手。用中文简短回答（≤2 句）。");
  const [voice, setVoice] = useState("default_zh_female");
  const [speed, setSpeed] = useState(1.0);

  const wsRef = useRef<WebSocket | null>(null);
  const micRef = useRef<MicRecorder | null>(null);
  const replyRef = useRef("");

  // TTS 渐进播放：收到一块 PCM 即排进 Web Audio 队列边到边放，而不是缓冲到
  // response.done 再整段播放——后者导致"好几句长内容一起发出"。
  const playCtxRef = useRef<AudioContext | null>(null);
  const nextStartRef = useRef(0);        // 下一块的调度起点（秒）
  const pendingSrcRef = useRef(0);        // 已排程未播完的源数量
  const doneRef = useRef(false);          // 本轮 response.done 是否已到
  // 流式回复文本用 rAF 合并渲染，避免每个 LLM token 都触发一次 React 重渲染。
  const replyRafRef = useRef<number | null>(null);

  // VAD/通话状态镜像（onFrame 闭包内读取，避免 state 闭包过期）
  const stageRef = useRef<Stage>("idle");
  const speakingRef = useRef(false);       // 当前 utterance 是否已检测到语音
  const speechStartRef = useRef(0);         // 语音起始时间戳
  const lastVoiceRef = useRef(0);           // 最近一次高于阈值的时间戳

  useEffect(() => { stageRef.current = stage; }, [stage]);

  // 音色列表加载后，若当前选中值不在列表中则自动选第一个
  useEffect(() => {
    if (voices.length > 0 && !voices.includes(voice)) {
      setVoice(voices[0]);
    }
  }, [voices]); // eslint-disable-line react-hooks/exhaustive-deps

  const resetVad = () => {
    speakingRef.current = false;
    speechStartRef.current = 0;
    lastVoiceRef.current = 0;
  };

  const ensurePlayCtx = (): AudioContext => {
    if (!playCtxRef.current || playCtxRef.current.state === "closed") {
      const AC =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      playCtxRef.current = new AC();
    }
    if (playCtxRef.current.state === "suspended") void playCtxRef.current.resume();
    return playCtxRef.current;
  };

  // 全部已排程音频播完 + 本轮 response.done 已到 → 恢复聆听。保留回声防护：
  // 思考/回放期间麦克风静音（onFrame 看 stage），直到这里才切回 listening + 重置 VAD。
  const maybeFinishPlayback = () => {
    if (doneRef.current && pendingSrcRef.current <= 0) {
      doneRef.current = false;
      nextStartRef.current = 0;
      if (stageRef.current !== "idle" && stageRef.current !== "error") setStage("listening");
      resetVad();
    }
  };

  // 收到一块 TTS PCM（24k int16 LE mono）→ 接到播放队列尾部，边到边放。
  const enqueuePcm = (bytes: ArrayBuffer) => {
    const i16 = new Int16Array(bytes);
    if (i16.length === 0) return;
    const ctx = ensurePlayCtx();
    const buf = ctx.createBuffer(1, i16.length, 24000);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime, nextStartRef.current);
    src.start(startAt);
    nextStartRef.current = startAt + buf.duration;
    pendingSrcRef.current += 1;
    src.onended = () => {
      pendingSrcRef.current -= 1;
      maybeFinishPlayback();
    };
  };

  // 新一轮开始：清空上一轮的播放调度状态。
  const resetPlayback = () => {
    doneRef.current = false;
    nextStartRef.current = 0;
    pendingSrcRef.current = 0;
  };

  const scheduleReplyRender = () => {
    if (replyRafRef.current != null) return;
    replyRafRef.current = requestAnimationFrame(() => {
      replyRafRef.current = null;
      setStreamingReply(replyRef.current);
    });
  };

  const onFrame = (frame: ArrayBuffer) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== 1) return;
    // 仅在"聆听中"采集并送流；思考/回复阶段静音，杜绝回声回环
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
    if (replyRafRef.current != null) { cancelAnimationFrame(replyRafRef.current); replyRafRef.current = null; }
    resetPlayback();
    if (playCtxRef.current && playCtxRef.current.state !== "closed") void playCtxRef.current.close();
    playCtxRef.current = null;
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
    resetPlayback();
    // 在用户手势（点击开始通话）内创建/恢复播放 AudioContext，满足浏览器自动播放策略。
    ensurePlayCtx();
    setStage("connecting");
    try {
      const sess = await apiFetch<SessionResp>("/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ prompt, voice, speed }),
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
          enqueuePcm(ev.data); // 边到边放，不再缓冲到 done 整段播
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
            resetPlayback();
            setStage("thinking");
            break;
          case "response.text":
            replyRef.current += m.text ?? "";
            scheduleReplyRender();
            setStage("speaking");
            break;
          case "response.done": {
            const full = m.text ?? replyRef.current;
            if (full) setLines((p) => [...p, { role: "assistant", text: full }]);
            if (replyRafRef.current != null) { cancelAnimationFrame(replyRafRef.current); replyRafRef.current = null; }
            setStreamingReply("");
            replyRef.current = "";
            // 标记本轮回复结束；待已排程音频全部播完后由 maybeFinishPlayback 恢复聆听
            doneRef.current = true;
            maybeFinishPlayback();
            break;
          }
          case "error":
            setErr(m.message ?? "对话出错");
            toast.error(m.message ?? "对话出错");
            // 出错也恢复聆听，保持通话不中断
            resetPlayback();
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
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="rt-voice">音色</Label>
                  <select
                    id="rt-voice"
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
                  <Label htmlFor="rt-speed">语速：{speed.toFixed(2)}×</Label>
                  <input
                    id="rt-speed"
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
