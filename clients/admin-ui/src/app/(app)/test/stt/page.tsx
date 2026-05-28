"use client";

import { Mic, Square, Upload, Loader2, FileAudio } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { wsUrl } from "@/lib/api";
import { decodeFileTo16kPCM, startMicCapture, type MicRecorder } from "@/lib/audio";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/console-ui";

type Mode = "file" | "mic";
type Phase = "idle" | "connecting" | "listening" | "finalizing" | "done" | "error";

const ASR_PATH = "/v1/console/asr";

export default function SttTestPage() {
  const [mode, setMode] = useState<Mode>("file");
  const [phase, setPhase] = useState<Phase>("idle");
  const [partial, setPartial] = useState("");
  const [final, setFinal] = useState("");
  const [err, setErr] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const micRef = useRef<MicRecorder | null>(null);

  const reset = () => {
    setPartial("");
    setFinal("");
    setErr("");
  };

  const closeAll = () => {
    micRef.current?.stop();
    micRef.current = null;
    if (wsRef.current && wsRef.current.readyState <= 1) wsRef.current.close();
    wsRef.current = null;
  };

  const openWs = (): Promise<WebSocket> =>
    new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl(ASR_PATH));
      ws.binaryType = "arraybuffer";
      ws.onopen = () => resolve(ws);
      ws.onerror = () => reject(new Error("WebSocket 连接失败"));
      ws.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data as string);
          if (m.type === "partial") setPartial(m.text ?? "");
          else if (m.type === "final") {
            setFinal((prev) => (prev ? prev + " " : "") + (m.text ?? ""));
            setPartial("");
          } else if (m.type === "error") {
            setErr(m.message ?? "识别出错");
            setPhase("error");
          }
        } catch {
          /* ignore non-JSON */
        }
      };
      ws.onclose = () => {
        setPhase((p) => (p === "error" ? p : "done"));
      };
    });

  // ── 文件识别 ──
  const runFile = async () => {
    if (!file) return;
    reset();
    setPhase("connecting");
    try {
      const pcm = await decodeFileTo16kPCM(file);
      const ws = await openWs();
      wsRef.current = ws;
      setPhase("listening");
      // 分块发送（每 32000 字节 ≈ 1s）
      const bytes = new Uint8Array(pcm);
      const CHUNK = 32000;
      for (let i = 0; i < bytes.length; i += CHUNK) {
        ws.send(bytes.subarray(i, i + CHUNK));
      }
      setPhase("finalizing");
      ws.send("EOS");
    } catch (e) {
      setErr((e as Error).message);
      setPhase("error");
      toast.error((e as Error).message);
      closeAll();
    }
  };

  // ── 麦克风识别 ──
  const startMic = async () => {
    reset();
    setPhase("connecting");
    try {
      const ws = await openWs();
      wsRef.current = ws;
      const rec = await startMicCapture((frame) => {
        if (ws.readyState === 1) ws.send(frame);
      });
      micRef.current = rec;
      setPhase("listening");
    } catch (e) {
      setErr((e as Error).message);
      setPhase("error");
      toast.error((e as Error).message);
      closeAll();
    }
  };

  const stopMic = () => {
    micRef.current?.stop();
    micRef.current = null;
    if (wsRef.current?.readyState === 1) {
      setPhase("finalizing");
      wsRef.current.send("EOS");
    }
  };

  const phaseLabel: Record<Phase, string> = {
    idle: "待机",
    connecting: "连接中…",
    listening: mode === "mic" ? "录音识别中…" : "识别中…",
    finalizing: "生成结果…",
    done: "完成",
    error: "出错",
  };
  const busy = phase === "connecting" || phase === "listening" || phase === "finalizing";

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="STT 转写" desc="支持上传音频文件或麦克风实时录音，转写为文本。" />

      <div className="mb-4 inline-flex rounded-md border p-0.5">
        {(["file", "mic"] as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => { if (!busy) { setMode(m); reset(); setPhase("idle"); } }}
            disabled={busy}
            className={
              "rounded px-3 py-1.5 text-sm transition-colors " +
              (mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent")
            }
          >
            {m === "file" ? "文件上传" : "麦克风录音"}
          </button>
        ))}
      </div>

      <Card>
        <CardContent className="space-y-4 pt-6">
          {mode === "file" ? (
            <div className="space-y-3">
              <label className="flex cursor-pointer items-center gap-3 rounded-md border border-dashed px-4 py-6 hover:bg-accent/50">
                <FileAudio className="h-5 w-5 text-muted-foreground" />
                <span className="text-sm text-muted-foreground">
                  {file ? file.name : "点击选择音频文件（wav / mp3 / webm 等）"}
                </span>
                <input type="file" accept="audio/*" className="hidden"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </label>
              <Button onClick={runFile} disabled={!file || busy}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                开始识别
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              {phase !== "listening" ? (
                <Button onClick={startMic} disabled={phase === "connecting" || phase === "finalizing"}>
                  <Mic className="h-4 w-4" />开始录音
                </Button>
              ) : (
                <Button variant="destructive" onClick={stopMic}>
                  <Square className="h-4 w-4" />停止并识别
                </Button>
              )}
            </div>
          )}

          <div className="flex items-center gap-2">
            <Badge variant={phase === "error" ? "destructive" : "secondary"}>{phaseLabel[phase]}</Badge>
            {busy && phase !== "connecting" && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
          </div>

          {(partial || final) && (
            <div className="space-y-2 rounded-md border bg-muted/40 p-4">
              {final && <p className="text-sm leading-relaxed">{final}</p>}
              {partial && <p className="text-sm italic text-muted-foreground">{partial}</p>}
            </div>
          )}
          {phase === "done" && !final && !partial && (
            <p className="text-sm text-muted-foreground">未识别到语音内容。</p>
          )}
          {err && <p className="text-sm text-destructive">{err}</p>}
        </CardContent>
      </Card>
    </div>
  );
}
