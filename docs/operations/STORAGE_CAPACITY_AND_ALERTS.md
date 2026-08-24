# 存储容量、保留与告警手册

本文定义 MySQL、OSS、本地缓存和存储 outbox 的容量责任及告警门槛。具体上限由服务负责人、DBA 与 OSS 管理员根据压测和预算共同批准，并通过后端 secret 中的环境变量注入。

## 1. 配置所有权

| 配置 | 含义 | 批准责任人 |
| --- | --- | --- |
| `MATHWEAVER_MAX_UPLOAD_BYTES` | 单次 HTTP 上传字节数 | 服务负责人 |
| `MATHWEAVER_MAX_TASK_FILES` | 单任务持久化文件数 | 服务负责人、OSS 管理员 |
| `MATHWEAVER_MAX_TASK_BYTES` | 单任务文件总字节数 | 服务负责人、OSS 管理员 |
| `MATHWEAVER_MAX_NODES` / `MATHWEAVER_MAX_EDGES` | 单任务图数据规模 | 服务负责人、DBA |
| `MATHWEAVER_MAX_HISTORY_JSON_BYTES` | 单条历史 JSON 编码上限 | DBA |
| `MATHWEAVER_MAX_USER_HISTORY_BYTES` | 单用户未删除历史总字节 | 产品负责人、DBA |
| `MATHWEAVER_HISTORY_RETENTION_DAYS` | 历史保留天数 | 产品、合规负责人 |
| `MATHWEAVER_MIN_FREE_DISK_BYTES` | 本地缓存最低剩余空间 | 值班工程师 |

所有值必须是经过校验的正整数或明确的内部监听地址。上线前查询 MySQL `@@max_allowed_packet`，其值必须大于最大历史负载加 1 MiB 安全余量。配置变更需要关联压测证据、回滚值和生效时间。

## 2. 保留与删除顺序

1. 保留期扫描只选择已过期且未删除的历史，并创建可审计的软删除/outbox 操作。
2. API 软删除成功后记录立即对普通查询不可见；不得在 API 请求中同步批量物理删除。
3. storage worker 先根据 outbox 幂等删除 OSS 版本，再清理本地缓存，最后更新完成状态。
4. OSS 生命周期规则只能处理已超过应用孤儿宽限期的不可见版本，不能早于应用软删除和 outbox 重试窗口。
5. 默认孤儿宽限期不少于 7 天；缩短时必须有一次完整协调扫描和恢复演练证据。
6. outbox `failed`、数据库引用但对象缺失、manifest 校验失败的版本禁止由生命周期规则自动清理。

## 3. 告警门槛

| 指标 | Warning | Critical | 首要处置 |
| --- | --- | --- | --- |
| 本地磁盘可用空间 | 低于 20% 或低于 `2 × MAX_TASK_BYTES` 持续 10 分钟 | 低于 10% 或触及 `MIN_FREE_DISK_BYTES` | 暂停新任务，清理已确认远端安全的缓存 |
| RDS 存储使用率 | 超过 70% 持续 30 分钟 | 超过 85% | 扩容并检查历史增长、索引和保留任务 |
| RDS 连接池占用 | 超过 80% 持续 10 分钟 | 超过 95% 持续 5 分钟 | 检查慢事务、泄漏和并发任务 |
| 数据库事务失败率 | 5 分钟内超过 1% | 5 分钟内超过 5% | 停止发布，按稳定错误码定位数据库 |
| outbox 待处理数 | 超过 100 或最老任务超过 15 分钟 | 最老任务超过 60 分钟 | 检查 worker、租约、OSS 权限与限流 |
| outbox `failed` 数 | 任意新增 | 持续增加或影响可见版本 | 保留对象，人工重放前确认幂等目标 |
| OSS 上传/恢复/删除失败率 | 5 分钟内超过 1% | 5 分钟内超过 5% | 检查网络、RAM 前缀权限、区域与配额 |
| 恢复耗时 p95 | 超过 60 秒持续 15 分钟 | 超过 180 秒 | 检查对象数量、带宽与本地磁盘 |
| 缺失数据库引用版本 | 任意 1 条 | 任意可见历史受影响 | 立即阻断发布并从版本/副本恢复 |
| 超过宽限期的孤儿版本 | 任意新增 | 数量连续两次扫描增长 | 运行只读协调扫描，再审批清理 outbox |
| 单任务文件数/字节数 | 达到上限 80% | 达到 100% 并被拒绝 | 指导拆分任务，不临时绕过上限 |
| 单用户历史字节 | 达到上限 80% | 达到 100% 并被拒绝 | 执行保留策略或审批扩容 |

低基数指标标签只能包含操作、状态和稳定错误码；不得包含用户 ID、任务 ID、对象键、文件名、URL 或异常文本。

Prometheus 只能通过 Compose 内部网络抓取 `http://backend:5001/internal/metrics`。Nginx 不代理 `/internal/metrics`，不得另行增加公网路由或把指标响应写入业务日志。

backend、storage worker 与运维命令通过数据卷内的 `PROMETHEUS_MULTIPROC_DIR` 汇总指标。协调扫描必须在已启动的 backend 容器内执行，才能把本次孤儿/缺失计数写入同一指标目录：

```bash
docker compose -f deploy/docker-compose.web.yml exec \
  -e MATHWEAVER_METRICS_PROCESS=reconciliation backend \
  python scripts/reconcile_storage.py
```

生产迁移服务会在启动长期进程前清理上一轮部署遗留的 mmap 文件；不得让 backend 或 worker 在运行期间清理该目录。

## 4. 仪表盘与每日检查

仪表盘至少展示：

- RDS 存储、`max_allowed_packet`、事务失败和连接池占用；
- OSS 上传/恢复/删除结果、恢复耗时、任务字节数和文件数；
- outbox 各状态数量、最老待处理时间、租约超时和重试次数；
- 协调扫描发现的孤儿、缺失版本、manifest/checksum 错误；
- 本地数据卷总量、可用量和最近 24 小时增长率。

值班工程师每日确认 Critical 为零、备份最近成功时间正常、outbox 最老任务未超过阈值。每周由服务负责人审查增长趋势，预测 30/60/90 天容量并形成扩容或保留策略变更记录。

## 5. 告警响应

1. 记录告警开始时间、版本、指标名和稳定错误码，不复制敏感原始异常。
2. 容量或一致性风险未解除前暂停新任务和发布；不要删除未知对象来快速消除告警。
3. 先运行只读数据库/协调检查，确认当前数据库版本指针、manifest 和 outbox 状态。
4. 需要修复时通过显式修复参数创建可审计 outbox，禁止在扫描进程中直接批量删除。
5. 恢复或重放后验证受影响任务的 manifest checksum 和对象哈希，并确认告警回落。
6. Critical 事件在 2 个工作日内完成复盘，更新阈值、容量预测或恢复手册。
