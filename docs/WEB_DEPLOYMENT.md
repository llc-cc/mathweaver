# MathWeaver 教学正式版部署

本文说明 MySQL 教学版的数据库迁移与正式图谱导入。服务旁路发布、回滚和验收命令将在同一文档后续章节维护。

## 安全边界

- Web 进程必须设置 `MATHWEAVER_DATABASE_URL`，不再创建或读取 `auth.db`。
- 正式 MySQL 必须同时设置 `MATHWEAVER_DATABASE_NAME=mathweaver`；应用会拒绝其他库名。
- 数据库口令只放在服务器 root-only 环境文件中，不写入命令参数、源码、发布记录或终端截图。
- `/api/v2/ping` 仅表示进程存活；`/api/v2/ready` 成功执行主数据库 `SELECT 1` 后才返回 200。

## 迁移数据库

在应用根目录、已激活虚拟环境且环境变量加载完成后执行：

```bash
python -m alembic -c backend/migrations/alembic.ini current
python -m alembic -c backend/migrations/alembic.ini history
python -m alembic -c backend/migrations/alembic.ini upgrade head
```

迁移前必须完成并验证 `mysqldump --single-transaction --routines --triggers` 备份。不得用 `Base.metadata.create_all()` 代替正式迁移。

## 导入正式凸优化图谱

教师账号必须已存在且角色为 `teacher` 或 `admin`。导入命令不接受数据库密码参数，只读取进程环境：

```bash
python -m scripts.import_graph_seed \
  --dataset backend/seeds/convex_optimization \
  --teacher-email '<teacher-email>' \
  --class-title '凸优化'
```

首次成功结果应包含 90 个节点、226 条边以及确定的 history、class、snapshot ID。立即重复执行一次，第二次必须返回相同 ID，且数据库对象计数不增加。

数据包预检会报告 8 个旧版 `global_id` 差异、27/90 的连续原文映射覆盖率以及 225/226 条“定义依赖”。这些是负责人交付数据的已知只读警告，不是自动修复项。

## 旁路发布与回滚

教学正式版使用独立旁路，不修改已有服务：后端仅监听 `127.0.0.1:5002`，前端仅监听 `127.0.0.1:5174`，Nginx 对外候选端口为 `18080`。

发布包必须解压到 `/opt/mathweaver/releases/<git-sha>`。按顺序执行：

```bash
sudo bash scripts/deploy_teaching_release.sh preflight /opt/mathweaver/releases/<git-sha>
sudo bash scripts/deploy_teaching_release.sh migrate /opt/mathweaver/releases/<git-sha>
sudo bash scripts/deploy_teaching_release.sh start /opt/mathweaver/releases/<git-sha>
```

`start` 会把原 `current-teaching` 保存为 `previous-teaching`，再使用临时软链接和原子重命名切换版本。应用或验收失败时执行：

```bash
sudo bash /opt/mathweaver/current-teaching/scripts/deploy_teaching_release.sh rollback
```

回滚只切换教学旁路软链接并重启两个 `mathweaver-teaching-*` 服务，不逆向执行数据库 downgrade。涉及不兼容数据变更时应先停旁路，再从已验证的 MySQL 备份恢复。

冒烟检查从环境读取专用教师账号，不在日志中输出口令或 token：

```bash
sudo --preserve-env=MATHWEAVER_SMOKE_EMAIL,MATHWEAVER_SMOKE_PASSWORD \
  bash scripts/smoke_teaching_release.sh
```

外部 `18080` 不通但服务器本机检查通过时，记录为云安全组或防火墙阻塞；部署脚本不会自行修改 SSH、系统防火墙或云安全组。
