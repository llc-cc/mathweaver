# MathWeaver 教学版 MySQL 与凸优化图谱正式部署设计

## 1. 目标

以 `pdfPipeline-teaching-briefing-redesign` 为唯一正式功能基线，将教学空间、学习过程和图谱导入功能从本地 SQLite 迁移到服务器 MySQL，并将 `test_result_round1` 中的凸优化教材、90 个知识节点和 226 条关系作为首套正式图谱数据部署上线。

上线后应满足：教师可创建班级并发布基于图谱的学习任务，学生只能访问所属班级，所有图谱、作业、学习证据和证明工作区数据在服务重启后仍可恢复，线上请求不再读写 SQLite。

## 2. 已确认现状

- 正式源码具备完整教学版前端和约 22 张教学/学习 SQLite 表，但数据库访问集中在 `backend/api_v2.py` 和 `backend/student_context.py` 的原始 SQLite SQL 中。
- MySQL 分支具备 SQLAlchemy 2、Repository、Alembic、用户认证、历史记录和对象存储边界，可作为持久化基础设施来源。
- 服务器为 Alibaba Cloud Linux 3，SSH 入口为 `101.132.100.225:22022`。
- 服务器 MySQL 8.0.36 中已有 `mathweaver` 库和 10 张基础表；图谱历史、课程、班级和证明工作区当前为空。
- 现有测试后端位于 `/opt/mathweaver`，Gunicorn 监听 `127.0.0.1:5001`。
- 公网 3000 端口属于另一套 Next.js 服务，本次部署不得占用或覆盖。
- 三个图谱文件结构可被现有 `/api/v2/agent-import` 接口接受，全部边端点可匹配到节点。

## 3. 方案选择

采用“教学版功能基线 + MySQL 基础设施合并 + Repository 分域迁移”的方案。

不采用以下方案：

- 不直接部署 SQLite，因为负责人已明确线上不再使用本地 SQLite。
- 不机械替换 SQL 占位符，因为教学版存在数百处 SQLite 参数和多种 SQLite 专用语法，无法可靠覆盖事务、冲突处理和类型差异。
- 不保留线上双写或 SQLite 回退，避免两套数据库形成不一致事实来源。

## 4. 总体架构

```text
React Router 教学版前端
        |
        v
Flask /api/v2
        |
        +--> AuthService / EducationService / GraphImportService
        |          |
        |          v
        |    SQLAlchemy Repository
        |          |
        |          v
        |       MySQL 8.0
        |
        +--> Pipeline / OCR / LLM
                   |
                   v
          任务产物目录（可后续切换 OSS）
```

API 负责身份校验、输入校验和响应组装；Service 负责业务决策和事务编排；Repository 负责 SQLAlchemy 查询；模型和 Alembic 迁移负责数据库契约。不得在前端、路由函数或 Pipeline 阶段直接拼接 MySQL SQL。

## 5. 数据模型

### 5.1 复用基础表

继续使用服务器已有表：

- `users`
- `login_sessions`
- `courses`
- `teaching_classes`
- `class_memberships`
- `history`
- `user_settings`
- `proof_workspaces`
- `audit_logs`
- `alembic_version`

教学版的 `education_classes` 和 `education_memberships` 不再重复建表，其语义分别合并到 `teaching_classes` 和 `class_memberships`。教师身份以 `users.role` 和教师白名单同步结果共同约束，明文密码不得进入源码或数据库。

### 5.2 新增教学表

通过 SQLAlchemy 模型和单一 Alembic 迁移新增：

- `education_snapshots`
- `education_assignments`
- `education_student_paths`
- `education_node_progress`
- `education_diagnostics`
- `education_assessment_nodes`
- `education_assessment_questions`
- `education_assessment_attempts`
- `education_assignment_submissions`
- `education_submission_question_grades`
- `education_ai_usage`
- `education_ai_tasks`
- `education_node_identities`
- `education_node_occurrences`
- `learning_interactions`
- `learning_evidence`
- `learning_evidence_nodes`
- `learning_evidence_feedback`
- `student_node_models`
- `learning_context_summaries`

所有外键明确 `ON DELETE` 策略；状态字段使用数据库约束和应用枚举双重校验；时间统一存 UTC；JSON 内容使用 MySQL `JSON` 类型；长文本使用 `TEXT`；班级、用户、作业、节点和时间查询建立组合索引。

### 5.3 图谱存储策略

`history` 保存用户导入或 Pipeline 生成的图谱任务，`education_snapshots` 保存教师发布时的不可变图谱快照。两者继续保存完整 `nodes_json`、`edges_json` 和 `source_markdown`，以保持现有前端与教学算法契约。

`education_node_identities` 使用班级内唯一的 `global_id` 表示规范知识节点，`education_node_occurrences` 记录每个快照中的整数节点编号与规范节点之间的映射。第一期不新增逐节点正文表和逐边关系表，避免在现有 JSON 图谱算法之外建立第二套事实来源。

## 6. 凸优化图谱导入

正式数据包由以下文件组成：

- `bv_cvxbook_1.1-2.3.md`
- `node_fixed_round4.json`
- `edge_fixed_round1.json`

导入前执行只读预检：JSON 可解析、节点 ID 唯一、边端点完整、标题和正文非空、关系理由非空，并生成数据质量报告。预检失败时不得创建半成品数据库记录。

导入事务流程：

1. 使用教师账号调用现有图谱导入服务。
2. 创建一条完成状态的 `history` 记录，保存原文、90 个节点和 226 条边。
3. 创建教学班级或选择现有班级。
4. 从该历史记录创建不可变 `education_snapshots` 记录。
5. 批量建立 `education_node_identities` 与 `education_node_occurrences`。
6. 记录导入审计日志和数据包校验摘要。

负责人交付的三个原始文件保持不变。当前发现的 8 个旧版 `global_id` 规则差异和 225/226 条关系均为“定义依赖”的分布只进入质量报告，不在首次上线时擅自改写。

## 7. 认证与权限

- Web 端仅使用 MySQL 用户、会话和权限数据。
- 教师可创建和管理自己的班级、图谱快照和作业。
- 学生只能通过邀请码加入班级，并只能读取所属班级已发布内容。
- 所有图谱、作业、提交、评分、证明和学习证据接口都必须同时校验当前用户与资源归属。
- Session 只存令牌摘要；LLM API Key 使用现有安全存储边界，不输出到日志。
- 初始教师密码在首次验收后必须更换，不得作为正式长期凭据。

## 8. 错误处理与事务

- 导入、发布、提交、评分和证据更新使用明确事务边界。
- 唯一键冲突返回可识别的 409，不依赖字符串匹配数据库错误。
- 外键或权限失败不得留下孤立记录。
- 批量节点身份写入采用幂等 upsert，并以班级和 `global_id` 唯一约束兜底。
- Pipeline 大文件产物失败时保留可审计任务状态，但不将半成品标记为完成。
- 数据库暂时不可用时返回 503，禁止静默回退 SQLite。

## 9. 部署拓扑

首次正式候选版本采用旁路部署：

- 新后端：`127.0.0.1:5002`
- 新前端：`127.0.0.1:5174`
- Nginx 验收入口：`http://101.132.100.225:18080`
- 数据库：现有 MySQL `mathweaver`

旁路验收不会覆盖公网 3000 端口或现有 `127.0.0.1:5001` 服务。获得正式域名和 DNS 控制权后，再由 Nginx 绑定独立子域名并启用 HTTPS；在此之前，18080 只作为受控验收入口，不宣称完成公网生产发布。

部署步骤必须按以下顺序执行：

1. 备份 MySQL `mathweaver` 和 `/opt/mathweaver` 当前代码、配置及服务定义。
2. 在独立版本目录上传构建产物，不原地覆盖。
3. 运行数据库连接检查和 Alembic dry-run 审计。
4. 执行迁移并校验表、索引和外键。
5. 启动新后端并完成内部健康检查。
6. 启动新前端并完成 API 代理检查。
7. 配置 Nginx 18080 旁路入口并验证。
8. 导入凸优化图谱，完成教师和学生端验收。
9. 验收通过后保留版本化回滚点，再安排正式域名切换。

## 10. 回滚与安全

- 每次部署使用独立版本目录和固定服务文件，失败时切回上一版本，不删除历史版本。
- 迁移前必须生成数据库备份并验证备份文件非空。
- 只允许向前兼容的新增表、列和索引进入首次迁移；破坏性迁移单独评审。
- 服务器磁盘当前使用率约 79%，构建产物和备份必须设置保留上限。
- 服务器已出现大量 SSH 密码扫描。正式发布前必须改用 SSH 密钥、轮换 root 密码并关闭 root 密码登录。
- MySQL 不直接暴露公网；应用仅通过服务器内部网络访问数据库。

## 11. 测试与验收

### 后端

- MySQL 模型和迁移测试。
- Repository 的成功、空结果、唯一冲突、权限拒绝和事务回滚测试。
- 教师建班、学生入班、图谱快照、作业发布、诊断、提交、评分和学习证据 API 回归测试。
- Web 模式下拦截所有 SQLite 连接，确保线上路径不会访问 SQLite。
- 图谱数据预检和幂等导入测试。

### 前端

- TypeScript 类型检查。
- 教学空间、图谱导入、历史记录、图谱展示和证明工作区相关 Vitest。
- 手工验证 90 个节点和 226 条关系的展示、筛选和原文浏览。

### 部署验收

- 服务重启后图谱和学习记录仍可访问。
- 未认证、跨用户和跨班级访问均被拒绝。
- 3000、5001 和服务器其他容器不受影响。
- 数据库备份和版本回滚演练成功。

## 12. 已知限制

- 首次上线不重做关系推理，也不修正负责人交付数据的语义分类。
- 首次上线不把节点和边拆为通用逐条图谱表；现有教学算法继续以快照 JSON 为输入。
- 正式 HTTPS 上线依赖可控域名和 DNS；域名提供前仅开放受控验收入口。
- OCR、LLM 和对象存储的生产凭据由部署环境提供，不写入源码或设计文档。
