# MathWeaver 网页版部署手册

本文用于将 MathWeaver 网页端部署到一台 Linux ECS，并连接阿里云 RDS MySQL。容器拓扑如下：

- 公网 HTTPS 由阿里云 ALB/SLB 终止，再转发到 ECS 的 Nginx 端口；
- Nginx 对外提供 `/`、`/api/v2/*` 和 `/health`；
- 前端仅在容器网络监听 `3000`，后端仅在容器网络监听 `5001`；
- Alembic 由一次性 `migrate` 服务执行，绝不在每个 Gunicorn 副本启动时自动执行；
- 结构化数据写入 RDS 的独立数据库 `mathweaver`；任务文件写入受控 Docker 卷 `mathweaver_artifacts`。

迁移和 Web 启动都会解析 SQLAlchemy URL 并执行目标守卫：MySQL URL 中的 schema、环境变量 `MATHWEAVER_DATABASE_NAME` 和固定生产库名必须同时严格等于 `mathweaver`，否则在建立连接前终止。Compose 会固定注入该库名，不能通过 `.env.production` 改成旧业务库。SQLite 只作为自动化测试或显式旧数据迁移源使用。

> **数据边界：**不得把连接串指向 `uniprism_alphatest_user`，不得修改或迁移该库的任何表。数据库密码、模型 API Key、会话令牌和生成的初始密码均不得提交到 Git、写入镜像构建参数或复制到部署文档。

## 1. 上线前准备

建议服务器至少安装 Docker Engine、Docker Compose 插件和 `curl`。在仓库根目录执行所有 Compose 命令。

推荐使用阿里云 ALB/SLB 托管证书并监听公网 `443`：

1. ALB/SLB 的 `80` 监听器只做 HTTPS 跳转；
2. `443` 监听器挂载正式域名证书；
3. 后端服务器组转发到 ECS 的 `MATHWEAVER_HTTP_PORT`，默认 `8080`；
4. ECS 安全组的该端口仅允许 ALB/SLB 安全组访问；
5. 不向公网开放容器内部的 `3000`、`5001` 或 RDS 的 `3306`。

当前 Nginx 配置监听容器内 HTTP `80`，假定 TLS 在 ALB/SLB 终止。若不使用负载均衡而让 ECS 直接终止 TLS，必须另行配置证书挂载、`listen 443 ssl`、证书续期和 HTTP 到 HTTPS 跳转，不能直接把本配置宣称为公网 HTTPS 已完成。

## 2. 创建独立 RDS 数据库和最小权限账号

确保 RDS 与 ECS 位于可互通的 VPC。RDS 白名单或安全组只允许 ECS 私网地址/安全组访问 `3306`，不要配置 `0.0.0.0/0`。

使用 RDS 管理账号在 DMS 或受控 MySQL 客户端中执行以下模板，并替换尖括号占位符：

```sql
CREATE DATABASE IF NOT EXISTS mathweaver
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'mathweaver_app'@'<ECS_PRIVATE_IP>'
  IDENTIFIED BY '<RANDOM_LONG_PASSWORD>';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES
  ON mathweaver.*
  TO 'mathweaver_app'@'<ECS_PRIVATE_IP>';

FLUSH PRIVILEGES;
```

如果 RDS 的账号由控制台统一创建，则在控制台只授权 `mathweaver` 库的读写和迁移所需 DDL 权限。上线前再次核对目标库名：

```sql
SELECT DATABASE();
```

结果必须为 `mathweaver`，不能是 `uniprism_alphatest_user`。

## 3. 配置仅服务器可读的环境文件

```bash
cp .env.example .env.production
chmod 600 .env.production
```

填写 `.env.production`：

- `MATHWEAVER_DATABASE_URL` 必须使用 RDS 内网地址和 `mathweaver` 库；密码中的特殊字符必须进行 URL 编码；
- `MATHWEAVER_DATABASE_NAME` 保持 `mathweaver`；Compose 还会以更高优先级固定该值；
- 同源部署时 `MATHWEAVER_ALLOWED_ORIGINS` 保持为空，Web API 不返回跨域允许头；确需跨域时填写逗号分隔的完整 Origin，例如 `https://a.example.edu.cn,https://b.example.edu.cn`；
- 模型和向量服务凭据只填写在后端运行时变量中；
- 镜像使用不可变版本标签，例如发布日期加提交号，不使用 `latest`。

`.env.production` 已由 `.gitignore`/`.dockerignore` 排除，但仍应通过服务器权限或密钥管理服务保护。不要把它发送到聊天、工单、日志或前端构建流程。

Compose 把整份文件作为 `mathweaver_backend_env` secret 只挂载给 `migrate` 与 `backend`，不会把内容展开进编排配置，前端也不会挂载该 secret。容器入口先静默读取 `/run/secrets/mathweaver_backend.env`，再降权到镜像内 `mathweaver` 用户并 `exec` 实际命令；入口不会打印变量名对应的值。镜像标签和监听端口等非敏感 Compose 参数请通过部署 shell 或不含密钥的 `.env` 设置，不要再把 `.env.production` 传给 Compose 的 `--env-file`。

## 4. 备份与迁移预演

任何结构或数据迁移前，先在 RDS 控制台创建手动备份/快照并记录备份 ID。正在使用旧 SQLite 时，还要做只读副本：

```bash
install -d -m 700 /srv/mathweaver/migration
cp --preserve=timestamps /path/to/legacy/auth.db /srv/mathweaver/migration/auth.db
chmod 600 /srv/mathweaver/migration/auth.db
test "$(id -u)" -ne 0
```

迁移目录和源文件必须归当前非 root 部署操作员所有。下面两次 SQLite 运行都显式使用该操作员的数值 UID/GID：这样容器能遍历 `700` 目录、读取 `600` 源文件，并在正式迁移时于同目录写入 `.bak`。不要省略 `--user`，也不要用 root 账户执行这组宿主目录迁移命令。

先检查将要执行的 Alembic SQL。`--sql` 仅生成 SQL，不连接 RDS：

```bash
docker compose -f deploy/docker-compose.web.yml \
  run --rm --no-deps --user "$(id -u):$(id -g)" migrate \
  python -m alembic -c migrations/alembic.ini upgrade head --sql
```

如果需要迁移旧 SQLite 业务数据，先进行只读 dry-run：

```bash
docker compose -f deploy/docker-compose.web.yml \
  run --rm --no-deps --user "$(id -u):$(id -g)" \
  -v /srv/mathweaver/migration:/migration:ro \
  migrate python scripts/migrate_sqlite_to_mysql.py \
  --sqlite /migration/auth.db \
  --database-url-env MATHWEAVER_DATABASE_URL \
  --dry-run
```

确认用户、历史、设置和证明工作区计数后，给迁移专用目录最小写权限，并用 `:rw` 挂载执行正式迁移。目录可写仅用于在源文件同目录创建在线备份；脚本仍以 SQLite `mode=ro` 打开源库，不会修改源文件：

```bash
chmod 700 /srv/mathweaver/migration
chmod 600 /srv/mathweaver/migration/auth.db

docker compose -f deploy/docker-compose.web.yml \
  run --rm --no-deps --user "$(id -u):$(id -g)" \
  -v /srv/mathweaver/migration:/migration:rw \
  migrate python scripts/migrate_sqlite_to_mysql.py \
  --sqlite /migration/auth.db \
  --database-url-env MATHWEAVER_DATABASE_URL

ls -l /srv/mathweaver/migration/auth.db.*.bak
sha256sum /srv/mathweaver/migration/auth.db /srv/mathweaver/migration/auth.db.*.bak
```

备份文件应只允许部署账号读取，并应复制到独立备份位置。脚本不会迁移旧会话令牌，并会在目标计数不一致时回滚。不要删除原 SQLite 文件或脚本生成的 `.bak`。

## 5. 构建、迁移和启动

`.env.production` 现在是 Compose file-backed secret，而不是 service `env_file` 或 Compose 插值文件。普通 `config` 只显示 secret 的文件路径和挂载元数据，不读取或展开文件内容；仍应避免把渲染配置无必要地写入公共日志。可直接执行以下结构和插值检查：

```bash
docker compose -f deploy/docker-compose.web.yml config

docker compose -f deploy/docker-compose.web.yml config --no-interpolate
```

如需在流水线只检查有效性而不输出渲染内容：

```bash
docker compose -f deploy/docker-compose.web.yml config --quiet
```

构建镜像。前端只接收固定的同源参数 `VITE_API_ORIGIN=__SAME_ORIGIN__`，RDS 和模型密钥不会进入前端镜像：

```bash
docker compose -f deploy/docker-compose.web.yml build
```

启动整个编排：

```bash
docker compose -f deploy/docker-compose.web.yml up -d
```

`migrate` 服务先执行数据库目标守卫和 `alembic upgrade head` 并正常退出；只有迁移成功后，后端才会启动。Gunicorn 固定使用 `1 worker / 8 threads / 300 秒超时`，确保任务创建、轮询、暂停、取消和恢复都落在同一个持有运行态的 Python 进程。查看状态和迁移日志：

```bash
docker compose -f deploy/docker-compose.web.yml ps -a

docker compose -f deploy/docker-compose.web.yml logs migrate backend proxy
```

如果 `migrate` 非零退出，后端不会启动。先修复数据库地址、权限或迁移问题，不要通过移除依赖关系绕过迁移。

## 6. 健康检查与验收

在 ECS 本机验证 Nginx 和后端：

```bash
curl --fail --show-error http://127.0.0.1:8080/health
curl --fail --show-error http://127.0.0.1:8080/api/v2/ping
```

在 ALB/SLB 配置完成后从外部验证：

```bash
curl --fail --show-error https://<MATHWEAVER_DOMAIN>/health
curl --fail --show-error https://<MATHWEAVER_DOMAIN>/api/v2/ping
curl --head https://<MATHWEAVER_DOMAIN>/
```

Web 模式下 `/health` 和 `/api/v2/ping` 会执行轻量 `SELECT 1`：RDS 可用时返回 `200 {"ok":true}`，不可用时返回稳定的 `503 {"ok":false,"error":"database_unavailable"}`，不会回显地址、用户名、密码或驱动异常。旧桌面模式保持不依赖 MySQL 的存活响应。API 响应含 `Cache-Control: no-store`，页面和 API 均通过同一 HTTPS 域名访问。随后使用测试管理员账号完成一次登录、CSV 小批量导入、任务创建、重启后历史恢复和权限隔离验收；不要用真实学生名单做首次演练。

可进行以下无敏感值检查：

```bash
docker history mathweaver-web:<VERSION>
docker history mathweaver-backend:<VERSION>
docker run --rm mathweaver-web:<VERSION> \
  sh -c "grep -R -n 'mysql+pymysql\|PDFPIPELINE_API_KEY' /app/build || true"
```

静态前端中不应出现数据库连接串、模型密钥或真实内网地址。

## 7. 发布回滚

每次发布前保留上一版前后端镜像标签、RDS 手动快照 ID 和制品卷备份。备份制品卷示例：

```bash
install -d -m 700 /srv/mathweaver/backups
docker run --rm \
  -v mathweaver_artifacts:/data:ro \
  -v /srv/mathweaver/backups:/backup \
  alpine:3.20 tar -czf /backup/artifacts-<VERSION>.tar.gz -C /data .
```

每次发布记录三项兼容矩阵：应用镜像版本、`MATHWEAVER_MIGRATION_IMAGE` 版本和 Alembic revision。迁移镜像应独立保留，不能在普通应用回滚时随 backend 标签一起盲目降级。

**无数据库变更，且旧应用已验证兼容当前 revision：**只回滚前后端应用镜像，保持当前迁移镜像不变，并显式跳过依赖服务，避免旧 Alembic 解析新 revision：

```bash
docker compose -f deploy/docker-compose.web.yml \
  up -d --no-build --no-deps backend frontend proxy
```

随后重新检查 `/health`、登录、历史、任务创建和控制接口。此命令只能在兼容矩阵已验证时使用。

**已经执行不兼容数据库迁移：**不要让旧应用连接新 schema，也不要在事故中盲目执行 `alembic downgrade`。从上线前 RDS 快照恢复到新的 RDS 实例/独立 `mathweaver` 数据库，核对备份 ID、revision 和行数后切换 `MATHWEAVER_DATABASE_URL`，再用上面的 `--no-deps` 命令启动旧应用。数据库降级只允许按已验证的备份恢复流程执行；恢复目标不能覆盖 `uniprism_alphatest_user`。恢复制品卷前先保留当前卷副本。

## 8. 当前容量与运行限制

### 8.1 任务状态和水平扩展

当前任务运行时状态仍保存在单个 Python 进程内，MySQL 只负责持久化结构化进度。因此首期 Gunicorn 固定为一个 worker、八个线程；这能保持任务控制正确性，但不能提供多进程容错或横向吞吐。

因此首期必须遵守：

- 只部署一个后端容器，不进行后端容器水平扩容；
- 发布或重启前先停止接收新任务并等待运行任务结束；
- 对 500 人以上的校内使用场景先做并发和长任务压测，分批开放；
- 后续引入 Redis + Celery/RQ 等共享队列、共享任务状态和独立 worker 后，才允许多容器扩展；
- 本地 Docker 卷不跨 ECS，共享执行前应把 PDF、OCR 输出和导出包迁移到 OSS，MySQL 只保留对象标识与元数据。

500 人以上并不等于允许 500 个长任务同时运行。没有共享队列前必须限流、分批开放并实测长任务容量；一个 worker 的线程数不能替代任务队列。

### 8.2 OCR 与 MinerU

当前 OCR 清单和既有运行包以 Windows 桌面环境为主，不能据此宣称 Linux 容器已经具备生产 OCR/MinerU 能力。上线前必须在目标 ECS 操作系统上单独验证 MinerU、模型文件、字体、GPU/CUDA（若使用）、磁盘空间和超时。

后端镜像会安装 `latexmk`、`xelatex`、`synctex`、中文 TeX 宏包和 Noto CJK 字体，并在镜像构建期真实编译一份最小中文 TeX 和 SyncTeX 文件。TeX Live 会显著增大镜像体积和构建时间；必须在 Linux Docker daemon 上完成镜像构建后才能宣称 TeX 源 PDF/定位功能可用。

在 Linux OCR 运行时完成验证前，可先上线不依赖本地 OCR 的 Markdown/TeX 流程，或把 OCR 放到经过验证的 Windows worker/独立 OCR 服务。不要把 Windows 二进制直接复制进 Linux 后端镜像。

### 8.3 CORS

网页生产环境采用同源代理，`MATHWEAVER_ALLOWED_ORIGINS` 默认留空，此时 Web 模式不发送跨域允许头。只有确有独立可信前端域名时才配置精确 Origin 清单。旧 Electron 在显式 `AI4MATH_DESKTOP=1` 且未配置 Web 数据库连接时保留无凭据 CORS 兼容；生产容器不得设置该桌面标志。

### 8.4 镜像依赖边界

当前 `backend/requirements.txt` 仍同时包含生产运行、测试和桌面打包依赖（如 `pytest`、`pyinstaller`）。这会扩大生产镜像和供应链扫描面，但本轮不拆分依赖文件；后续应拆成 runtime/dev requirements，并对精简镜像补完整导入与流水线 smoke test。

