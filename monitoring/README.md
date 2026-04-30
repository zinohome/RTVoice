# RTVoice 监控栈（可选）

基于 Phase D 暴露的 Prometheus 指标，提供：
- **Prometheus** — 抓取 5 个 service 的 `/metrics`
- **Grafana** — 内置 RTVoice 总览 dashboard，自动加载

## 快速启动

```bash
# 启所有 dev 服务 + 监控栈
docker compose -f docker-compose.yml \
               -f docker-compose.dev.yml \
               -f docker-compose.monitoring.yml \
               --profile dev --profile monitoring up -d
```

启动后：
- **Grafana**：http://127.0.0.1:3000（admin / admin，首次会要求改密码）
  - 默认主页就是 RTVoice dashboard，按时间段切换 + 5/10s 自动刷新
- **Prometheus**：http://127.0.0.1:9091（注：dev livekit 占了 9090，监控栈让到 9091）
  - 用 `Status → Targets` 看 5 个 job 是否 UP

## Dashboard 内容（6 row, ~14 panels）

| Row | 内容 |
|---|---|
| Global Health | Agent FSM 当前状态 / 活跃 STT WS / 24h 轮数 / barge-in / errors |
| Agent E2E | round_seconds p50p95 / first_audio p50p95 / phrases per round / 事件率 |
| STT | decode_seconds p50p95p99 / WS connections + event types |
| TTS | phrase rate / TTFB p50p95 / **phrase RTF**（红线警戒 < 1.0×） |
| Token Server | tokens issued by room / auth failures / HTTP latency |

## 文件结构

```
monitoring/
├── README.md                              ← 本文件
├── prometheus.yml                         ← scrape 配置（5 个 job）
├── grafana-provisioning/
│   ├── datasources/prometheus.yml         ← 自动配 Prometheus datasource
│   └── dashboards/dashboards.yml          ← 自动加载 dashboards/ 目录
└── dashboards/
    └── rtvoice.json                       ← 总览 dashboard
```

## 自定义

### 加新指标

1. 在对应 service 的 main.py 里 `Counter / Gauge / Histogram` 定义
2. 重启 service：`docker compose ... up -d --no-deps <service>`
3. Prometheus 自动抓到（15s scrape interval）
4. 在 Grafana 里加 panel；或编辑 `monitoring/dashboards/rtvoice.json` 后让 provisioning 自动 reload

### 接现有 Prometheus

如果生产已有 Prometheus 中央实例，**不要**起本栈。改在中央 prometheus.yml 加 5 个 job：

```yaml
scrape_configs:
  - job_name: rtvoice
    static_configs:
      - targets:
          - rtvoice-host:8000  # token-server
          - rtvoice-host:9090  # stt-server
          - rtvoice-host:9880  # tts-server
          - rtvoice-host:9100  # agent-worker
```

注意 prod 时 token-server 端口可能仅在 nginx 后；使用内部地址抓取。

### Alerting（v0.6+ 计划）

监控栈基础就绪，未来可加：
- `monitoring/alerts.yml` — Prometheus alerting rules
- `monitoring/alertmanager.yml` — 告警路由（Slack/PagerDuty/邮件）
- 关键指标告警：
  - `rtvoice_agent_pipeline_errors_total` 5min rate > 0.1
  - `rtvoice_agent_round_seconds` p95 > 3s
  - `rtvoice_tts_phrase_rtf` p5 < 0.8
  - `rtvoice_stt_ws_connections_active == 0` 持续 5min（agent 离线）

## 关掉监控栈

```bash
docker compose -f docker-compose.monitoring.yml --profile monitoring down
# 数据卷保留：rtvoice_prometheus_data / rtvoice_grafana_data
```

## 占用资源

- prometheus: 1G 内存上限，15 天 retention 约 50-200MB 磁盘（取决于 scrape 频率）
- grafana: 512M 内存上限，~5MB 磁盘
- 总计监控栈附加 ~1.5GB RAM，不影响主服务

## 安全提醒

- Grafana 默认 admin/admin：首次登录会强制改密码
- 端口默认绑 `127.0.0.1`：公网访问需配 nginx + TLS + 强认证
- 不要把 `rtvoice_grafana_data` 卷暴露到非可信网络（含 dashboard 数据）
