# Task 2 独立代码审查

## 结论

**CHANGES REQUESTED — 未批准。**

核心认证仓储、令牌摘要、过期/撤销、`last_used_at`、Web/桌面启动分支、统一登录错误消息和 API 用户形状均已按规格实现；但回归测试存在可复现的顺序依赖，且公开登录入口缺少请求体类型校验。当前开放问题共 **3** 个：中等 2 个、低 1 个。

## 问题（按严重级别）

### 中等（阻止批准）1：旧回归脚本的 SQLAlchemy 测试库只在模块导入时建表，合并运行时会失败

- 位置：`backend/scripts/test_agent_import.py:26`、`backend/scripts/test_paused_history_resume.py:30`、`backend/tests/conftest.py:17-26`
- 两个旧脚本在模块导入时执行一次 `Base.metadata.create_all(get_engine())`。当认证/模型测试随后通过 `configure_database()` 切换引擎并在夹具退出时删除表后，旧脚本不会针对当前引擎重新建表。
- `backend/tests/conftest.py` 位于 `tests/` 子目录，其 `database` 夹具不适用于同级的 `scripts/` 测试；模块级建表因此不能替代每个脚本测试自身的数据库隔离。
- 单文件命令因导入顺序偶然通过，但真实合并命令稳定失败：

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
  ```

  实测结果：`5 failed, 34 passed, 1 warning`。五个失败都从 `sqlite3.OperationalError: no such table: users` 开始；异常路径还使多个临时 `history.db` 在 Windows 上以 `WinError 32` 清理失败。
- 影响：交付报告的分文件通过不能证明回归套件可组合运行，后续 CI 或完整验证会失败，测试也会污染全局引擎状态。
- 要求：为两个脚本提供按测试建立/清理 SQLAlchemy 引擎和表结构的夹具，并恢复所有被改写的全局状态；修复后必须让上述合并命令通过，同时确认临时 SQLite 文件句柄可正常释放。

### 中等（阻止批准）2：公开登录接口未校验 JSON 对象和字段类型，错误输入会成为 500

- 位置：`backend/api_v2.py:431-436`、`backend/storage/auth_repository.py:25-26`
- `request.get_json(silent=True) or {}` 可能返回列表；代码随后直接调用 `body.get()`。`identifier` 也未经字符串校验直接传给仓储并调用 `.strip()`。
- 实测：向 `/api/v2/auth/login` 发送 JSON 数组得到 500；发送 `{"identifier": 123, "password": "x"}` 也得到 500，并在 Flask 错误日志中打印堆栈。
- 影响：匿名调用者可以用语法合法但类型错误的 JSON 稳定触发服务端异常，违反 API 入口请求体校验要求，也使客户端收到不可控的内部错误。
- 要求：在路由边界验证请求体必须为对象且 `identifier`/`password` 必须为字符串，返回明确且稳定的 4xx；增加数组、数字字段等回归测试。仓储仍可保留字符串接口，不应承担 Flask 请求解析。

### 低 3：MySQL 上的学号比较不是严格区分大小写的“精确匹配”

- 位置：`backend/storage/auth_repository.py:28-34`；依赖模型 `backend/storage/models.py:23,38`
- 仓储使用 `User.student_no == normalized`，但表默认排序规则是 `utf8mb4_unicode_ci`。在 MySQL 中该排序规则不区分大小写，因此字母型学号 `Ab01` 会被 `ab01` 命中，与规格要求的 exact `User.student_no` 不一致。当前 SQLite 测试无法暴露此差异。
- 影响：若学校学号包含字母，登录标识的确定性规则会因数据库方言而变化。
- 要求：为 `student_no` 使用区分大小写的列排序规则或显式二进制比较，并补一个能验证 MySQL 比较语义的测试；邮箱分支继续保持小写、不区分大小写的既定规则。

## 已确认符合规格的部分

- Web 模式缺少 `MATHWEAVER_DATABASE_URL` 时启动失败；仅 `AI4MATH_DESKTOP=1` 且无 URL 时保留旧 SQLite 认证。
- Web 公开注册稳定返回 404，桌面兼容分支未被误删。
- 邮箱查询会去首尾空白并转小写；数字学号能保留前导零。
- 未知账号、错误密码和停用账号均返回相同的 `401` 与 `学号、邮箱或密码错误`。
- 使用 Werkzeug `check_password_hash` 验证密码，没有直接比较密码哈希。
- 原始会话令牌由 `secrets.token_urlsafe(32)` 生成；持久化边界只接收 SHA-256 摘要。检索未发现新增代码记录密码、原始 Bearer token、密码哈希或数据库 URL。
- 有效会话同时检查未撤销、未过期和用户启用状态；成功认证更新 `last_used_at`；注销幂等；批量撤销可保留指定摘要。
- TTL 默认值、正整数校验及自定义过期时间符合规格。
- `/auth/login` 与 `/auth/me` 的 Web 用户 JSON 形状精确包含六个要求字段；`_current_user()` 转为字典后与尚未迁移的旧路由下标读取方式兼容。
- 未发现密码修改/重置、管理员导入或历史数据迁移等后续任务被提前实现。
- 所有 `backend/scripts/test_*.py` 中已无为创建测试用户而调用公开 `/auth/register` 的残留。

## 亲自运行的验证

在实现目录 `backend` 下分别运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_mysql.py -q
# 18 passed in 10.37s

.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py -q
# 12 passed in 0.97s

.\.venv\Scripts\python.exe -m pytest scripts/test_agent_import.py -q
# 5 passed, 2 warnings in 2.56s

.\.venv\Scripts\python.exe -m pytest scripts/test_paused_history_resume.py -q
# 4 passed, 5 warnings in 3.62s

.\.venv\Scripts\python.exe -m py_compile storage/auth_repository.py services/auth_service.py api_v2.py tests/test_auth_mysql.py scripts/test_agent_import.py scripts/test_paused_history_resume.py
# exit 0
```

合并回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_models.py tests/test_auth_mysql.py scripts/test_agent_import.py scripts/test_paused_history_resume.py -q
# 5 failed, 34 passed, 1 warning in 11.68s
```

附加请求体检查通过 Flask 测试客户端复现：JSON 数组与数字 `identifier` 均返回 500。

交付报告给出的三个 SHA-256 与当前实现文件一致：

- `storage/auth_repository.py`: `AC728E258427FD047C364AD417C05519040E8FB0D0A3DF06F0381545A435B4A9`
- `services/auth_service.py`: `672B5323304D70AD30AB5612098100481C0385C1F7A6F64B9020261D8EACF3FC`
- `api_v2.py`: `85F7F7529813E5C9F4512121BBDCD1E33BDFE5E3F46F46346266014274BEDB17`

## 已知但不归因于本任务的问题

- 单文件旧脚本仍报告 7 个既有 `datetime.utcnow()` 弃用警告，调用点位于本任务未迁移的历史/任务代码。
- 本任务按规格使用显式 SQLAlchemy SQLite URL 测试，尚未连接真实 MySQL；真实实例连通性属于部署/后续集成验证，但学号排序规则问题应在进入该阶段前修正。
