# RTVoice 安全契约

本文档定义 AI 助手（Claude）在本项目开发与部署过程中必须遵守的安全约束。
契约面向两类环境：**开发机**（无 GPU，本机沙盒）与**生产机**（RTX 3060 12GB，运行真实负载）。

---

## 1. 绝对禁止（Hard No）

下列行为在任何环境、任何理由下都不允许执行。如果遇到表面上"必须这样做"的情况，先停下来报告并等待人工授权。

### 1.1 主机文件系统

- **禁止**挂载 `/`、`/etc`、`/var`、`/usr`、`/root`、`/home`、当前用户家目录到容器
- **禁止**对宿主机执行 `rm -rf`、`mv` 到非项目目录之外的路径
- **禁止**修改宿主机系统配置（`/etc/*`、systemd unit、内核参数）
- 唯一允许的宿主路径绑定：项目工作目录内的子路径，且优先 `:ro` 只读

### 1.2 容器特权

- **禁止** `--privileged`
- **禁止** `--cap-add=SYS_ADMIN`、`--cap-add=NET_ADMIN`（GPU 不需要这些）
- **禁止** `--pid=host`、`--ipc=host`、`--uts=host`
- **禁止** `--network=host`，所有端口必须显式 `-p`/`ports:` 映射

### 1.3 数据破坏类命令

- **禁止** `docker system prune -a --volumes`（会删所有未使用卷）
- **禁止** `docker volume rm <非本项目卷>`
- **禁止** `docker image prune -a` 在生产机
- **禁止**直接操作 `/var/lib/docker`
- **禁止** `--volumes` flag 在 `docker compose down` 上，除非用户明确同意

### 1.4 包管理污染

- **禁止**在宿主机上 `apt install`、`pip install`、`npm install -g`
- **禁止**修改宿主机 Python/Node/CUDA 版本
- 所有依赖都进 Dockerfile，环境零侵入

### 1.5 无审查执行

- **禁止** `curl ... | bash`、`wget ... | sh` 风格的下载即执行
- **禁止**执行任何来源不明的二进制
- 所有第三方脚本要么走官方包管理器，要么先下载、审阅、再执行

### 1.6 凭证与暴露

- **禁止**在 Dockerfile、镜像、commit 历史、日志里硬编码 API key/token/密码
- 凭证统一走 `.env` 文件，`.env` 必须在 `.gitignore`
- **默认**所有服务端口绑定 `127.0.0.1`，外网暴露需要用户在 prod 配置里显式开启
- **禁止**关闭/绕过 LiveKit 的 token 鉴权

### 1.7 危险捷径

- **禁止** `--no-verify`（git 钩子）、`chmod 777`、滥用 `sudo`
- **禁止**为了绕过错误而禁用安全检查（SSL 验证、签名校验等）
- 遇到错误**修根因**，不绕过

---

## 2. 工程约束（Soft Yes）

### 2.1 容器化

- 所有服务通过 `docker compose` 编排，不允许散装 `docker run`
- 启动 = `docker compose up -d`，停止 = `docker compose down`，删数据 = `docker compose down -v`（且需用户确认）

### 2.2 镜像版本

- 第三方镜像必须 pin 版本号（如 `livekit/livekit-server:v1.7.2`），禁止 `:latest`
- 自建镜像使用多阶段构建，最终运行镜像不带编译工具链
- 镜像 tag 规则：`rtvoice/<service>:<git-short-sha>` 或 `:vX.Y.Z`

### 2.3 数据卷

- 持久化数据用 named volume（如 `rtvoice_models`、`rtvoice_livekit_data`）
- 不用宿主路径绑定挂载存放数据（除非是只读配置）
- 重要卷在变更前先打 tarball 快照

### 2.4 GPU 资源

- 通过 `deploy.resources.reservations.devices` 声明 GPU，不用旧式 `runtime: nvidia`
- 显式指定可见设备 ID，不默认 `all`
- GPU 只给真正需要的服务（agent worker / TTS / LLM），LiveKit server 不分配

### 2.5 网络

- 内部服务走 docker network，不暴露端口
- 对外暴露端口只在最外层网关（LiveKit、token-server）
- 默认绑 `127.0.0.1`，公网开放需 prod 配置显式声明

### 2.6 资源上限

- 每个服务声明 `mem_limit`、`cpus` 上限，防失控吃光机器
- 每个服务带 `healthcheck`

### 2.7 日志与可观测

- 容器日志用 docker 默认 driver，配 `max-size: 50m`、`max-file: 3` 防爆盘
- 不在日志里输出凭证、用户音频原始数据

---

## 3. 开发机协议

- 当前环境检测：**无 NVIDIA 驱动 / 无 nvidia-container-toolkit**
- 开发机一律使用 CPU 路径或 mock 服务
- 开发机 compose profile：`dev`，使用 `docker-compose.yml + docker-compose.dev.yml`
- 不在开发机上尝试任何 GPU 相关命令（避免误装驱动）

---

## 4. 生产机迁移协议

如果将来在生产 GPU 服务器上操作，按以下顺序执行：

### 4.1 第一阶段：只读探查（不改任何状态）

```bash
docker ps -a
docker images
docker volume ls
docker network ls
df -h
nvidia-smi
ls -la <项目目录>
```

把现状报告给用户，等待确认后才进入第二阶段。

### 4.2 第二阶段：备份（任何变更前）

- 现有容器：`docker commit` 到带时间戳的镜像
- 现有数据卷：`docker run --rm -v <vol>:/data -v $(pwd):/backup alpine tar czf /backup/<vol>-$(date +%F-%H%M).tar.gz /data`
- 现有 compose 配置：`cp -r <dir> <dir>.bak.<date>`

### 4.3 第三阶段：单服务渐进部署

- 一次只改一个 compose service
- 每改一个等 `healthcheck` 转绿再继续
- 出错立即 `docker compose down <service>` + 回滚镜像 tag

### 4.4 第四阶段：声明影响

每个动手命令前必须用一句话说明：
- **影响范围**：哪个服务/卷/端口
- **可逆性**：可逆 / 不可逆
- **回滚方式**：具体命令

### 4.5 邻居容器

- 生产机上若有其他用户/项目的容器或卷，**绝不操作**
- 不删除来源不明的镜像/卷

---

## 5. 升级与回滚

### 5.1 升级流程

1. 在开发机构建新镜像并打 tag
2. 推到镜像仓库（或 `docker save` + scp）
3. 生产机 `docker compose pull` + `docker compose up -d <service>`
4. 验证 healthcheck，监控 5 分钟

### 5.2 回滚流程

- 镜像层：`compose` 文件中 tag 改回上一版，`up -d` 即可
- 数据层：从 tarball 恢复 named volume
- 配置层：`git revert`

---

## 6. 违约处理

如果 AI 助手在执行中触碰了上述任一红线，应：
1. 立即停止后续操作
2. 报告触碰的条目和当前状态
3. 等待用户决定是回滚、继续还是其它处置

用户的 explicit instruction 可以临时豁免某条约束，但豁免范围仅限当次任务，不延续。
