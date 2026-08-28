# PDF Pipeline

面向数学教材的 **PDF→Markdown→知识图谱** 全流程管线，包含前端可视化与后端处理服务：

- **后端（Python/Flask）**：OCR/解析、条目抽取、关系构建、存入 Neo4j。
- **前端（React Router + Vite）**：上传 PDF、查看处理进度、图谱可视化与条目浏览。

> 说明：本仓库同时包含前端应用与后端处理服务，请分别启动。

## 功能特性

- PDF OCR 与 Markdown 生成
- 定义/定理等知识节点抽取
- 节点关系自动构建并写入 Neo4j
- 处理任务的异步状态查询与取消
- 图谱可视化与条目展示

## 目录结构（核心）

```
app/                # 前端 React Router 应用
backend/            # 后端 Flask 服务 + PDF 处理脚本
public/             # 前端静态资源
Dockerfile          # 前端/部署相关
package.json        # 前端依赖与脚本
```

## 环境要求

- Node.js 18+（前端）
- Python 3.10+（后端）
- Neo4j 数据库（用于知识图谱存储）

## 前端启动

在项目根目录：

```bash
npm install
npm run dev
```

默认访问地址：`http://localhost:5173`

## 后端启动

在项目根目录：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
python backend/server.py
```

后端默认端口与路由以 [backend/server.py](backend/server.py) 为准。

## 关键配置

### Neo4j 连接信息

- 前端配置：在 [app/config.ts](app/config.ts) 中配置 `neo4j_info`。
- 后端配置：在 [backend/server.py](backend/server.py) 中初始化 `Neo4jHandler`。

建议在实际部署时改为读取环境变量或配置文件，避免明文凭据。

### LLM / API 配置

后端处理 PDF 时需要传入 `apiUrl`、`modelName` 与 `apiKey`，用于调用大模型服务。

### 教育空间教师账号

教师账号固定维护在后端白名单 [backend/education_teacher_accounts.py](backend/education_teacher_accounts.py) 中，服务启动时会自动同步，不需要每次设置环境变量。

密码只保存为 Werkzeug 哈希。人工添加账号时，先生成密码哈希：

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))"
```

然后在 `TEACHER_ACCOUNTS` 中新增 `{ "email": "...", "password_hash": "生成的哈希" }`，重启后端即可生效。不要把明文密码提交到仓库；未列入白名单的账号只能进入学生端。

## 处理流程（简述）

1. 上传 PDF
2. OCR → Markdown
3. 抽取节点与关系
4. 写入 Neo4j
5. 前端可视化展示

## 相关说明

- 更完整的后端流程说明见 [backend/README.md](backend/README.md)。
- 前端使用 React Router + Vite；后端为 Flask 服务。

## License

如需指定 License，请在此补充。
