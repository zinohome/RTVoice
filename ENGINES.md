# RTVoice 引擎选型记录

本文档详细记录 STT、TTS、LLM 三类引擎的候选评估、对比维度、选型结论与降级策略。
目的：让未来的项目维护者（包括未来的我）理解"为什么选 A 不选 B"，避免被新热点带跑偏，也便于后续在量化数据出来后做有依据的替换。

**关联文档**：[ARCHITECTURE.md](./ARCHITECTURE.md)、[SECURITY.md](./SECURITY.md)

> **诚实声明**：本文档中标注 ⚠️ 的延迟/显存数字来自社区报告与论文，**未在本项目硬件（RTX 3060 12GB）上实测**。生产机部署后会更新为实测值，并在 `docs/benchmarks/` 留档。

---

## 1. 评估维度（Rubric）

所有候选按以下维度打分。权重反映 voice agent 实时对话场景的需求，不适用于其它形态。

| 维度 | 权重 | 含义 | 不达标后果 |
|---|---|---|---|
| **流式延迟** | ★★★★★ | 首包 TTFB；流式模式是否原生 | 死刑：voice agent 不能等 |
| **中文质量** | ★★★★★ | 中文 ASR 准确率 / TTS 自然度 | 用户体感差，比英文 SOTA 也没用 |
| **显存占用** | ★★★★ | fp16/Q4 部署显存 | 3060 12GB 是硬上限 |
| **吞吐稳定性** | ★★★ | 并发请求下 latency 抖动 | 多人/多轮易抖时影响体感 |
| **生态成熟度** | ★★★ | Docker 镜像、文档、社区 | 影响首次跑通时间和踩坑成本 |
| **可定制性** | ★★ | 微调、音色克隆、提示词控制 | v1 不必需，v2+ 才考虑 |
| **许可证** | ★★ | Apache/MIT 优于 GPL，避免商用顾虑 | 商用受限 |
| **持续维护** | ★★ | 最近 commit 频率、issue 响应 | 决定 1-2 年后是否还能用 |

---

## 2. STT 候选对比

### 2.1 候选清单

| 引擎 | 类型 | 中文模型 | 流式 | GPU | 显存 ⚠️ | 许可 | 维护 |
|---|---|---|---|---|---|---|---|
| **sherpa-onnx** | ONNX Runtime + Kaldi | Paraformer / Zipformer 流式中文 | ✅ 原生 | 可选 | ~500MB | Apache-2.0 | 活跃（k2-fsa） |
| **faster-whisper** | CTranslate2 | whisper-large-v3 多语 | ⚠️ VAD 切块伪流式 | ✅ | ~3GB fp16 / ~1.5GB int8 | MIT | 活跃 |
| **WhisperX** | faster-whisper + alignment | 同 whisper | ❌ 偏批处理 | ✅ | ~4GB+ | BSD-4 | 活跃 |
| **SenseVoice (FunAudioLLM)** | PyTorch / ONNX | 中文优化，多任务（含情感/事件） | ⚠️ 块流式 | ✅ | ~1GB | Apache-2.0 | 活跃（阿里） |
| **Vosk** | Kaldi 传统 | 中文支持但质量旧 | ✅ | ❌ CPU | ~50MB | Apache-2.0 | 维护中 |
| **NVIDIA Parakeet** | NeMo | 仅英文 | ✅ | ✅ | ~2GB | CC-BY-4.0 | 活跃 |

### 2.2 详细评估

#### sherpa-onnx + Paraformer ★★★★★

**优点**：
- 原生流式架构（chunk 320ms），不是用 VAD 切块伪装的流式
- ONNX Runtime 跨平台，CPU/GPU 同代码
- 中文 Paraformer 是阿里 FunASR 系，中文实测 CER 优于 whisper-large-v3
- 显存极低，500MB 量级，可以挤进任何配置
- 模型文件官方仓库直接下载，不需要 HF 翻墙

**缺点**：
- 多语言切换不如 whisper 顺滑
- 文档分散在 k2-fsa.github.io，初次上手要找资料
- 标点恢复需要单独的标点模型

**踩坑预警**：
- whisper-tiny 的 sherpa-onnx 版本 CER 比 faster-whisper 高（[issue #2900](https://github.com/k2-fsa/sherpa-onnx/issues/2900)）— 但**Paraformer 不是 whisper**，这个问题不影响 Paraformer 路线
- 流式模型和离线模型 API 不同，要选对

#### faster-whisper ★★★★

**优点**：
- 生态最成熟，几乎所有教程/集成都有
- CTranslate2 推理快，int8 量化后 1.5GB 显存可跑 large-v3
- 多语言支持最好，中英混合场景胜过 Paraformer

**缺点**：
- **不是真流式**：whisper 架构是 30s 块编码器，"流式"靠 VAD 切块 + 重叠拼接，首字延迟天然高
- 中文准确率不如 Paraformer（社区共识，Whisper 训练数据中文比例低）

**适用场景**：英文为主、对延迟容忍度高、需要标点完整的场景。**不是 voice agent 首选**。

#### SenseVoice ★★★★

**优点**：
- 阿里 FunAudioLLM 出品，中文质量与 Paraformer 同级
- 多任务：转写 + 情感识别 + 事件检测（笑声/掌声）
- 比 whisper 快 5-15×（论文声称）

**缺点**：
- 流式支持是 chunk 模式，不如 sherpa-onnx 原生流式
- 模型相对新，社区集成少
- 多任务输出格式特殊，需要解析

**何时切换**：v2+ 想加情感感知（angry/happy → 不同 LLM 提示词）时考虑。

### 2.3 STT 选型结论

**生产**：`sherpa-onnx + paraformer-zh-streaming` (中等模型)
**开发**：`sherpa-onnx + paraformer-zh-streaming-tiny` (CPU 跑)

**理由**：
1. 流式延迟是 voice agent 死线
2. 中文质量优先级高于英文场景兼容性
3. 显存极低（< 1GB），把 11GB 留给 TTS 和 LLM
4. 同代码 CPU/GPU，dev/prod 切换零改动

**何时重新评估**：
- 中英混合场景占比 > 30% → 考虑切 faster-whisper 或双引擎并行
- 需要情感识别 → 切 SenseVoice
- 实测 CER 不达预期 → 升级到 paraformer-large 或微调

---

## 3. TTS 候选对比

### 3.1 候选清单

| 引擎 | 参数 | 中文 | 流式 TTFB ⚠️ | 显存 ⚠️ | 克隆 | 许可 | 维护 |
|---|---|---|---|---|---|---|---|
| **CosyVoice 2** (0.5B) | 500M | ★★★★★ | 150ms 原生流式 | ~4GB fp16 | 优 | Apache-2.0 | 活跃（阿里） |
| **GPT-SoVITS** v2/v3 | ~330M | ★★★★★ | ~500ms 段流式 | ~3-4GB | ★★★★★（1分钟微调） | MIT | 极活跃（中文圈王） |
| **Fish-Speech S2 Pro** | 4.4B | ★★★★★ | 150ms 原生流式 | ~10GB+ | ★★★★ | CC-BY-NC-SA-4.0 | 活跃（fishaudio） |
| **F5-TTS** | ~330M | ★★★ | ❌ 非自回归无流式 | **~3GB（最省）** | ★★★ | MIT | 活跃 |
| **Kokoro** | 82M | ★★（普通话还行，方言弱） | ✅ 极低（CPU 都行） | < 1GB | ❌ 不支持 | Apache-2.0 | 活跃 |
| **Edge-TTS** | - | ★★★★ | 极低（云端） | 0 | 不支持 | 微软服务 | 服务依赖 |
| **VITS / Bert-VITS2** | 100-200M | ★★★★ | ~300ms | ~2GB | 优（需训练） | AGPL（注意） | 活跃 |
| **IndexTTS 2** | - | ★★★★★ | 中等 | ~5GB | 优（含时长控制） | Apache-2.0 | 活跃（哔哩哔哩） |
| **Qwen3-TTS** | - | ★★★★★（含方言） | 流式 | ≥ 12GB ⚠️ 3060 吃力 | 优 | Apache-2.0 | 活跃（阿里） |

### 3.2 详细评估

#### CosyVoice 2 ★★★★★（首选）

**优点**：
- **架构原生为流式设计**：unified streaming/non-streaming framework，不是把非流式硬切的
- **150ms TTFB**：达到 voice agent 实时对话标准
- 0.5B 版本 ~4GB 显存，3060 一张卡吃得下
- 中文质量与 GPT-SoVITS 同级（阿里生态）
- 论文有详细 benchmark，不是口口相传
- Apache-2.0，商用友好

**缺点**：
- 流式模式 API 较新，社区集成少于 GPT-SoVITS
- 音色克隆需要参考音频，零样本质量有时不稳

**踩坑预警**：
- 0.5B 是 instruct 版本；非 instruct 也叫 CosyVoice 2 但能力差异大，下载注意
- 需要 modelscope 库依赖，国内用户友好

#### GPT-SoVITS ★★★★（备选 / 克隆专用）

**优点**：
- **中文克隆开源天花板**：1 分钟微调 / 5 秒零样本
- 中文圈用户基数最大，教程/整合包最多
- 支持中英日韩粤跨语言

**缺点**：
- **流式不是原生设计**：段流式（按句切），首字延迟比 CosyVoice 2 高
- 推理依赖较重（Python + PyTorch + 多个子模块）
- 非中文输出有杂音
- 项目结构偏研究代码，工程化包装要自己做

**何时启用**：
- v2+ 用户要求自定义音色克隆
- 用户提供高质量参考音频且不在乎流式细微延迟

#### Fish-Speech S2 Pro ★★★★（pass：超出 3060 预算）

**优点**：
- 论文声称超过 OpenAI/Google 闭源 TTS
- 4B Slow AR + 400M Fast AR 双自回归架构

**缺点**：
- **4.4B 参数显存压力大**：fp16 约 10GB+，3060 几乎跑不下来（要留 LLM 显存）
- **CC-BY-NC-SA-4.0 许可**：非商用，未来商用化是隐患
- 模型新，工程化材料少

**结论**：3060 不考虑，等到换 24GB 卡或者 v2 版本出 0.5B/1B 蒸馏版再说。

#### F5-TTS ★★★（pass：流式不行）

非自回归（diffusion-style）架构注定不能流式。3GB 显存最省，但 voice agent 用不上。**适合配音/批量合成场景，不适合本项目**。

#### Kokoro ★★（pass：中文弱，无克隆）

82M 模型，速度极快，但中文是英文模型微调来的，普通话还行，自然度不如专门训练的中文模型。无音色克隆。**适合开发机 mock 替代品**——如果不想自己写 sine wave，可以用 Kokoro 当 mock TTS。

#### Edge-TTS（pass：违反"完全本地"目标）

微软服务，免费但走云端。违反项目设计目标 G4。**唯一用途**：开发阶段调试音频管道用作参考音质标杆。

#### IndexTTS 2 ★★★★（关注名单）

哔哩哔哩出品，支持精确时长控制（视频配音场景刚需）。voice agent 不需要时长控制，但中文质量 SOTA 级。**何时考虑**：CosyVoice 2 实测有问题时的备选。

### 3.3 TTS 选型结论

**生产**：`CosyVoice 2-0.5B`（流式模式）
**开发**：`mock`（Python 吐 sine wave 或预录 PCM 文件）+ 可选 `Kokoro` 作为更真实的 mock

**理由**：
1. 唯一同时满足"原生流式 + 150ms TTFB + 中文 SOTA + 3060 跑得动 + 商用许可友好"五项的开源 TTS
2. 0.5B 参数与 LLM 共存毫无压力
3. 阿里生态（与 STT 的 Paraformer 同源）一致性好

**何时重新评估**：
- 用户强烈要求音色克隆 → 切 GPT-SoVITS
- 实测自然度低于 GPT-SoVITS → 加双引擎选择
- 显卡升级到 24GB+ → 考虑 Fish-Speech S2 Pro 或 IndexTTS 2

---

## 4. LLM 候选对比

### 4.1 候选清单（中文优先 + 3060 12GB 可跑）

| 模型 | 参数 | 中文 | 量化后显存 ⚠️ | 首 token ⚠️ | 函数调用 | 许可 |
|---|---|---|---|---|---|---|
| **Qwen2.5-3B-Instruct** | 3B | ★★★★ | ~3GB Q4_K_M | 200-300ms | ✅ | Apache-2.0 |
| **Qwen2.5-7B-Instruct** | 7B | ★★★★★ | ~5GB Q4_K_M | 300-500ms | ✅ | Apache-2.0 |
| **Qwen3-4B** (新) | 4B | ★★★★★ | ~3.5GB Q4 | 250ms | ✅（含 thinking 模式） | Apache-2.0 |
| **Qwen3-8B** | 8B | ★★★★★ | ~5.5GB Q4 | 350ms | ✅ | Apache-2.0 |
| **GLM-4-9B-chat** | 9B | ★★★★ | ~6GB Q4 | 400ms | ✅ | 自定义（商用要登记） |
| **InternLM2.5-7B** | 7B | ★★★ | ~5GB Q4 | 350ms | ✅ | Apache-2.0 |
| **Llama-3.1-8B** | 8B | ★★ | ~5GB Q4 | 300ms | ✅ | Llama 许可 |
| **MiniCPM-3-4B** | 4B | ★★★★ | ~3GB Q4 | 200ms | ✅ | Apache-2.0 |

### 4.2 推理后端对比

| 后端 | 优势 | 劣势 | 适用 |
|---|---|---|---|
| **vLLM** | 吞吐最高（PagedAttention），OpenAI 兼容 API | 显存占用大（KV cache 预分配） | 生产，多并发 |
| **Ollama** | 一键启动，跨平台，模型管理友好 | 单请求慢于 vLLM | 开发，单用户 |
| **llama.cpp / llama-server** | 极致量化（Q4/Q3），CPU 也能跑 | 并发吞吐弱 | CPU 开发，资源受限 |
| **TensorRT-LLM** | 单卡延迟最低 | 编译复杂，模型转换麻烦 | 极致优化阶段（v1 不上） |
| **SGLang** | 结构化输出快 | 中文模型集成参差 | 需要严格 JSON 输出时 |

### 4.3 详细评估

#### Qwen2.5-3B-Instruct（首选 v0.5）

**优点**：
- 中文 SOTA 级（中文圈实测 3B 段位最强之一）
- 3GB Q4 显存，留给 TTS+STT+KV cache 充足空间
- 函数调用支持完整，未来加 tool use 平滑
- Apache-2.0 商用零顾虑

**缺点**：
- 复杂推理弱于 7B
- 多轮对话上下文一致性偶有问题

**何时升级**：
- 实测对话质量不达预期 → 切 7B 或 Qwen3-8B（显存还够）
- 用户接受首 token 慢 100-200ms 换质量提升

#### Qwen3-4B / Qwen3-8B（关注名单）

Qwen3 系列（2026 年初发布）相对 2.5 提升明显，含 thinking 模式（隐藏思考过程）。voice agent **不要 thinking 模式**（首 token 等思考完会暴增延迟），用 instruct 模式即可。

#### GLM-4-9B-chat（pass：许可顾虑）

中文质量好，但商用登记流程繁琐。开源项目可用，商业产品避开。

### 4.4 LLM 选型结论

**生产 v0.5**：`Qwen2.5-3B-Instruct` Q4_K_M via **vLLM**
**生产 v0.6+**：根据实测可升 `Qwen2.5-7B-Instruct` 或 `Qwen3-4B`
**开发**：`mock`（固定回复几句话）→ 可选 `Ollama + Qwen2.5-1.5B` 跑真模型测体感

**理由**：
1. 3B Q4 仅 3GB，与 4GB TTS + 0.5GB STT 加和约 7-8GB，3060 留余量给 KV cache 增长
2. vLLM 的 OpenAI 兼容 API 让 livekit-agents 标准 LLM 插件直接复用
3. Apache-2.0 + 中文 SOTA + 函数调用三件套齐全

---

## 5. 综合显存预算（生产 3060 12GB）

| 组件 | 模型 | 显存 ⚠️ |
|---|---|---|
| stt-server | sherpa-onnx + Paraformer-zh-streaming | ~500MB |
| tts-server | CosyVoice 2-0.5B fp16 | ~4GB |
| llm-server | Qwen2.5-3B-Instruct Q4_K_M（vLLM） | ~3GB |
| KV cache 缓冲 | 上下文增长 | ~2-3GB |
| 系统/驱动余量 | - | ~1GB |
| **合计** | | **~10-11GB** |

**冗余空间 1-2GB**。如果实测吃紧：
1. STT 切到更小的 Paraformer-mini
2. TTS 启用 int8 量化（CosyVoice 2 是否支持需验证）
3. LLM 切到 Qwen2.5-1.5B 或更激进的 Q3 量化
4. 限制 vLLM `max_model_len`（缩短上下文）

---

## 6. 引擎降级与 fallback 策略

### 6.1 启动期降级

| 触发 | 切换 | 影响 |
|---|---|---|
| TTS 模型加载超时 | mock TTS（预录音频） | 音质降级，对话仍可继续 |
| LLM 模型加载超时 | mock LLM（固定回复） | 智能降级，链路验证 |
| STT 模型加载超时 | 直接报错（核心组件） | 中断，无法对话 |

### 6.2 运行期降级

| 触发 | 切换 |
|---|---|
| TTS 服务连续 3 次 5xx | 切 fallback：播放预录"系统繁忙"提示音 + 异步重连 |
| LLM 流式中断 | 当前轮直接结束，提示用户重说 |
| STT WS 断 | agent 重连，期间用户音频缓冲 ≤ 2s 否则丢弃 |
| GPU OOM | 当轮失败，下轮触发模型重载（清空 KV cache） |

### 6.3 配置级开关

`.env` 提供三档配置：

```ini
# 极致质量（需更大显卡）
QUALITY_PROFILE=premium    # Qwen2.5-7B + CosyVoice 2 + Paraformer-large

# 平衡（3060 默认）
QUALITY_PROFILE=balanced   # Qwen2.5-3B + CosyVoice 2 + Paraformer-medium

# 极限节省
QUALITY_PROFILE=minimal    # Qwen2.5-1.5B + Kokoro + Paraformer-tiny
```

每个 profile 在 `docker-compose.prod.yml` 中映射到不同的模型 ID 与显存上限。

---

## 7. 后续评估计划

### 7.1 v0.6 部署后必做的实测

在 RTX 3060 12GB 实机上跑：

| 指标 | 工具 | 目标 |
|---|---|---|
| STT 中文 CER | 自录 50 句中文测试集 | < 5% |
| STT 流式首字延迟 | 客户端打点 | < 200ms |
| TTS 首包 TTFB | curl 流式 | < 250ms |
| TTS MOS（主观） | 5 人盲听打分 | ≥ 4.0 |
| LLM 首 token | OpenAI API timing | < 400ms |
| LLM 输出速度 | tokens/s | > 30 t/s |
| 端到端 p50 / p95 | agent metric 日志 | 800ms / 1200ms |
| 显存峰值 | nvidia-smi --loop | < 11GB |
| 24h 稳定性 | 长跑回归对话 | 无 OOM/崩溃 |

结果落入 `docs/benchmarks/v0.6-rtx3060.md`。

### 7.2 重新评估触发条件

任一发生时回到本文档重新走选型流程：

- 硬件升级（4090 / 5090 等 24GB+）
- 用户场景变化（加入英文 / 加入克隆 / 加入情感）
- 某引擎 1 年未更新且有等价替代
- 实测核心指标连续 2 个版本未达标

---

## 8. 已排除的方案与理由

| 方案 | 排除理由 |
|---|---|
| OpenAI Whisper API / TTS API | 违反"完全本地"目标 |
| Azure / AWS / Google Cloud | 同上 |
| Coqui TTS | 公司 2024 年解散，维护不可持续 |
| Bark | 输出长度限制 + 显存大 + 已 1 年无更新 |
| StyleTTS2 | 中文支持弱 |
| Mozilla TTS / Tacotron 系 | 已被新模型全面超越，维护停滞 |
| GPT-2/3 微调路线 | 中文 LLM 已远超，无理由倒退 |

---

## 9. 维护说明

- 本文档**不是百科全书**，只列出本项目实际评估或考虑过的方案
- 新热点技术加入前先做"30 分钟阅读"判断是否值得详评——不要把每个 Twitter 上看到的新模型都列进来
- 实测数据出来后，把 ⚠️ 标记的预估值替换为实测值并去掉警告标
- ADR 决策若变更，在本文档末尾追加"变更记录"章节，**不要直接覆盖原决策**——决策史本身有价值
