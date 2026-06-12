"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AudioLines, Plus, Play, Trash2, Loader2, FileAudio,
  ShieldCheck, Star, ChevronRight, RotateCcw,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { apiBlob, apiFetch, apiUpload } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/console-ui";

interface PreviewResult {
  audio_b64: string;
  transcript: string;
  original_duration: number;
  effective_duration: number;
}

function b64ToBlob(b64: string, mime = "audio/wav"): Blob {
  const bytes = atob(b64);
  const buf = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) buf[i] = bytes.charCodeAt(i);
  return new Blob([buf], { type: mime });
}

export default function VoicesPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<{ voices: string[] }>({
    queryKey: ["console", "voices"],
    queryFn: () => apiFetch<{ voices: string[] }>("/v1/console/voices"),
  });
  const voices = data?.voices ?? [];

  const { data: cfgData, isLoading: cfgLoading } = useQuery<{ default_voice: string }>({
    queryKey: ["console", "config"],
    queryFn: () => apiFetch<{ default_voice: string }>("/v1/console/config"),
  });
  const defaultVoice = cfgData?.default_voice ?? "";

  // dialog state
  const [addOpen, setAddOpen] = useState(false);
  const [step, setStep] = useState<1 | 2>(1);
  const [spkId, setSpkId] = useState("");
  const [promptText, setPromptText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // existing voice preview (TTS playback)
  const [previewing, setPreviewing] = useState<string | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["console", "voices"] });
    qc.invalidateQueries({ queryKey: ["console", "config"] });
  };

  const resetDialog = () => {
    setStep(1);
    setSpkId("");
    setPromptText("");
    setFile(null);
    setPreview(null);
    if (previewUrl) { URL.revokeObjectURL(previewUrl); setPreviewUrl(null); }
  };

  // revoke blob URL when dialog closes
  useEffect(() => {
    if (!addOpen) resetDialog();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addOpen]);

  const setDefaultMut = useMutation({
    mutationFn: (voice: string) =>
      apiFetch<{ default_voice: string }>("/v1/console/config/voice", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice }),
      }),
    onSuccess: (r) => {
      toast.success(`默认音色已设为「${r.default_voice}」`);
      qc.invalidateQueries({ queryKey: ["console", "config"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  // Step 1 → 2: process audio, call preview API
  const previewMut = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("file", file as File);
      return apiUpload<PreviewResult>("/v1/console/voices/preview", fd);
    },
    onSuccess: (r) => {
      setPreview(r);
      setPromptText(r.transcript);
      const blob = b64ToBlob(r.audio_b64);
      const url = URL.createObjectURL(blob);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      // 清除旧 Audio 实例，确保试听始终播放最新处理的音频
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      setPreviewUrl(url);
      setStep(2);
    },
    onError: (e: Error) => toast.error(`音频处理失败：${e.message}`),
  });

  // Step 2 → register
  const addMut = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("spk_id", spkId);
      fd.append("prompt_text", promptText);
      fd.append("file", file as File);
      return apiUpload<{ spk_id: string; voice_count: number }>("/v1/console/voices", fd);
    },
    onSuccess: (r) => {
      toast.success(`音色「${r.spk_id}」注册成功`);
      setAddOpen(false);
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const delMut = useMutation({
    mutationFn: (id: string) => apiFetch(`/v1/console/voices/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast.success("音色已删除"); refresh(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const playVoice = async (v: string) => {
    setPreviewing(v);
    try {
      const blob = await apiBlob("/v1/console/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: "你好，这是音色试听示例。", voice: v, speed: 1.0 }),
      });
      const url = URL.createObjectURL(blob);
      const a = new Audio(url);
      a.play().catch(() => {});
      a.onended = () => URL.revokeObjectURL(url);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPreviewing(null);
    }
  };

  const playProcessed = () => {
    if (!previewUrl) return;
    if (audioRef.current) {
      audioRef.current.currentTime = 0;
      audioRef.current.play().catch(() => {});
    } else {
      const a = new Audio(previewUrl);
      audioRef.current = a;
      a.play().catch(() => {});
    }
  };

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex items-start justify-between">
        <PageHeader title="Voice 音色" desc="管理 CosyVoice3 zero-shot 音色：试听、注册（上传参考音频 + 文本）、删除。" />
        <Button onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" />注册音色
        </Button>
      </div>

      {isLoading || cfgLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {voices.map((v) => {
            const isDefault = v === defaultVoice;
            return (
              <Card key={v} className="flex flex-col gap-3 p-4">
                <div className="flex items-center gap-2">
                  <AudioLines className="h-4 w-4 text-primary shrink-0" />
                  <span className="font-medium truncate">{v}</span>
                  {isDefault && (
                    <Badge variant="secondary" className="ml-auto text-[10px]">
                      <ShieldCheck className="h-3 w-3" />默认
                    </Badge>
                  )}
                </div>
                <div className="flex gap-2 flex-wrap">
                  <Button variant="outline" size="sm" onClick={() => playVoice(v)} disabled={previewing === v}>
                    {previewing === v ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                    试听
                  </Button>
                  {!isDefault && (
                    <Button variant="outline" size="sm"
                      onClick={() => setDefaultMut.mutate(v)}
                      disabled={setDefaultMut.isPending}>
                      {setDefaultMut.isPending && setDefaultMut.variables === v
                        ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        : <Star className="h-3.5 w-3.5" />}
                      设为默认
                    </Button>
                  )}
                  {!isDefault && (
                    <Button variant="outline" size="sm"
                      onClick={() => { if (confirm(`确认删除音色「${v}」？`)) delMut.mutate(v); }}
                      disabled={delMut.isPending}>
                      <Trash2 className="h-3.5 w-3.5" />删除
                    </Button>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {step === 1 ? "注册音色 — 上传音频" : "注册音色 — 确认信息"}
            </DialogTitle>
            <DialogDescription>
              {step === 1
                ? "上传任意格式音频，系统自动转为 16kHz mono、去除前导静音并截取 8 秒。"
                : "请确认或修改 STT 自动识别的参考文本，然后填写音色 ID 完成注册。"}
            </DialogDescription>
          </DialogHeader>

          {step === 1 && (
            <div className="space-y-4 py-2">
              <div className="space-y-1.5">
                <Label>参考音频文件</Label>
                <label className="flex cursor-pointer items-center gap-3 rounded-md border border-dashed px-4 py-4 hover:bg-accent/50">
                  <FileAudio className="h-5 w-5 text-muted-foreground shrink-0" />
                  <span className="text-sm text-muted-foreground truncate">
                    {file ? file.name : "点击选择音频文件（wav / mp3 / flac 等）"}
                  </span>
                  <input type="file" accept="audio/*" className="hidden"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                </label>
              </div>
            </div>
          )}

          {step === 2 && preview && (
            <div className="space-y-4 py-2">
              {/* 处理后音频信息 + 播放 */}
              <div className="rounded-lg border bg-muted/30 px-4 py-3 space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">原始时长</span>
                  <span>{preview.original_duration.toFixed(1)}s</span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">处理后时长</span>
                  <span className="font-medium">{preview.effective_duration.toFixed(1)}s</span>
                </div>
                <Button variant="outline" size="sm" className="w-full mt-1" onClick={playProcessed}>
                  <Play className="h-3.5 w-3.5" />
                  试听处理后音频
                </Button>
              </div>

              {/* STT 自动回填文本，可编辑 */}
              <div className="space-y-1.5">
                <Label htmlFor="v-text">
                  参考文本
                  {preview.transcript
                    ? <span className="ml-2 text-xs text-green-600">（STT 自动识别）</span>
                    : <span className="ml-2 text-xs text-amber-600">（STT 未识别，请手动填写）</span>}
                </Label>
                <Textarea
                  id="v-text"
                  rows={3}
                  value={promptText}
                  onChange={(e) => setPromptText(e.target.value)}
                  placeholder="音频中所说的准确文字"
                />
              </div>

              {/* 音色 ID */}
              <div className="space-y-1.5">
                <Label htmlFor="v-id">音色 ID</Label>
                <Input
                  id="v-id"
                  value={spkId}
                  onChange={(e) => setSpkId(e.target.value)}
                  placeholder="例如 en_female_1（字母数字下划线/中文/连字符）"
                />
              </div>
            </div>
          )}

          <DialogFooter className="gap-2">
            {step === 1 ? (
              <>
                <Button variant="outline" onClick={() => setAddOpen(false)}>取消</Button>
                <Button
                  onClick={() => previewMut.mutate()}
                  disabled={previewMut.isPending || !file}
                >
                  {previewMut.isPending
                    ? <Loader2 className="h-4 w-4 animate-spin" />
                    : <ChevronRight className="h-4 w-4" />}
                  处理音频
                </Button>
              </>
            ) : (
              <>
                <Button variant="outline" onClick={() => setStep(1)}>
                  <RotateCcw className="h-3.5 w-3.5" />重新上传
                </Button>
                <Button
                  onClick={() => addMut.mutate()}
                  disabled={addMut.isPending || !spkId.trim() || !promptText.trim()}
                >
                  {addMut.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                  确认注册
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
