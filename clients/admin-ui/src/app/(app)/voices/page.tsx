"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AudioLines, Plus, Play, Trash2, Loader2, FileAudio, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { apiBlob, apiFetch, apiUpload } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/console-ui";

const DEFAULT_VOICE = "default_zh_female";

export default function VoicesPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<{ voices: string[] }>({
    queryKey: ["console", "voices"],
    queryFn: () => apiFetch<{ voices: string[] }>("/v1/console/voices"),
  });
  const voices = data?.voices ?? [];

  const [addOpen, setAddOpen] = useState(false);
  const [spkId, setSpkId] = useState("");
  const [promptText, setPromptText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [previewing, setPreviewing] = useState<string | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["console", "voices"] });

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
      setSpkId(""); setPromptText(""); setFile(null);
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const delMut = useMutation({
    mutationFn: (id: string) => apiFetch(`/v1/console/voices/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast.success("音色已删除"); refresh(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const preview = async (v: string) => {
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

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex items-start justify-between">
        <PageHeader title="Voice 音色" desc="管理 CosyVoice3 zero-shot 音色：试听、注册（上传参考音频 + 文本）、删除。" />
        <Button onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" />注册音色
        </Button>
      </div>

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {voices.map((v) => {
            const isDefault = v === DEFAULT_VOICE;
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
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => preview(v)} disabled={previewing === v}>
                    {previewing === v ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                    试听
                  </Button>
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
        <DialogContent>
          <DialogHeader>
            <DialogTitle>注册音色</DialogTitle>
            <DialogDescription>
              上传一段清晰人声参考音频（3–30 秒，建议 16kHz mono wav）及其对应文本，
              系统将注册为可用的 zero-shot 音色。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="v-id">音色 ID</Label>
              <Input id="v-id" value={spkId} onChange={(e) => setSpkId(e.target.value)}
                placeholder="例如 en_female_1（字母数字下划线/中文/连字符）" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="v-text">参考音频文本</Label>
              <Input id="v-text" value={promptText} onChange={(e) => setPromptText(e.target.value)}
                placeholder="参考音频里所说的准确文字" />
            </div>
            <div className="space-y-1.5">
              <Label>参考音频文件</Label>
              <label className="flex cursor-pointer items-center gap-3 rounded-md border border-dashed px-4 py-4 hover:bg-accent/50">
                <FileAudio className="h-5 w-5 text-muted-foreground" />
                <span className="text-sm text-muted-foreground">{file ? file.name : "点击选择 wav 文件"}</span>
                <input type="file" accept="audio/wav,audio/*" className="hidden"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>取消</Button>
            <Button onClick={() => addMut.mutate()}
              disabled={addMut.isPending || !spkId.trim() || !promptText.trim() || !file}>
              {addMut.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              注册
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
