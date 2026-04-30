# 贡献指南

本项目是 voice agent 实战实验项目。如果你想贡献，先读：

- [SECURITY.md](./SECURITY.md)：安全契约（红线）
- [ARCHITECTURE.md](./ARCHITECTURE.md)：系统设计 + ADR
- [ENGINES.md](./ENGINES.md)：选型 rubric
- [CHANGELOG.md](./CHANGELOG.md)：版本演进 + 经验教训摘录

## 工程规范

### 提交

- commit message：`<type>(<scope>): <subject>` + body 含变更摘要
  - type：`feat / fix / docs / chore / refactor / test`
  - scope：版本号（v0.x）或服务名（agent-worker、stt-server 等）
- **不在主分支 push 未验证 commit**：先 dev 测试 → commit → push
- 不修改他人 commit message（不 `git rebase -i` 公开历史）
- 不 force push 到 main

### 安全

- **永不**提交 `.env` 或任何含真实密钥的文件
- 新增配置先看 `.env.example` 里有没有同名 key（防"改 example 不改 .env"陷阱）
- Dockerfile 镜像 pin 具体版本号，禁 `:latest`
- 容器永远非 root 运行
- 端口默认绑 `127.0.0.1`，公网暴露需 PR 描述里明示理由

### 选型决策

新增依赖前：
1. 看 PyPI 依赖图（防 torch+CUDA 等大包；参见 v0.2 silero-vad 教训）
2. 对照 [ENGINES.md](./ENGINES.md) §1 rubric 评估
3. 选型变化记入 CHANGELOG.md 的 Notes

## 本地开发

```bash
./scripts/dev-up.sh                 # 起服务
docker logs -f rtvoice-agent        # 看 agent 状态机
./scripts/test-stt.sh               # 单元测 STT WS
./scripts/dev-down.sh               # 停（保留模型卷）
```

## 测试

```bash
# 单元测试（phrase_split / state_machine）
pip install pytest
pytest services/agent-worker/tests/

# 集成（需起服务）
./scripts/test-stt.sh
./scripts/test-llm.sh
./scripts/test-tts.sh

# Lint
yamllint docker-compose*.yml
shellcheck scripts/*.sh
hadolint services/*/Dockerfile*
```

## 代码风格

- Python：尽量别加注释；用好命名；只在"为什么"非显然时写注释
- 中文注释 OK（项目主语用中文）；commit message 中英都行
- 不引入 lint 工具（black/isort/ruff）除非全队同意；保持心智轻量

## 验证完工的标准

任何 PR 完成必须：
- ✅ `docker compose ... config` 不报错
- ✅ 受影响服务 `healthcheck: healthy`
- ✅ CHANGELOG.md 加版本条目（Added/Changed/Fixed/Notes/验证 五段）
- ✅ commit message 写清"为什么"

如果是性能改动：
- ✅ 在 `docs/benchmarks/` 留 before/after 数据
