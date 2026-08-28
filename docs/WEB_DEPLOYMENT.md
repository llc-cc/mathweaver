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
