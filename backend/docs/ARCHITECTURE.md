# Backend Architecture README

这份文档专门说明 `backend/` 在本次模块化重构之后的目录结构、入口职责，以及各文件夹里放的内容。

## 1. 设计目标

这次重构主要解决三个问题：

1. `extractor.py` 过长，提取、关系、后处理、入口逻辑混在一起
2. `analysis_layer.py` 与主流程有重复的 LLM / I/O / CLI 包装代码
3. `backend` 顶层文件过多，入口文件、测试脚本、资源文件、文档混放

重构后的原则是：

- 顶层只保留真正的入口文件
- 主流程实现放入 `pipeline/`
- `pipeline/` 内按“阶段”拆分，而不是按零散工具拆分
- `analysis` 作为独立阶段与其他阶段并列
- 公共复用逻辑只收敛到少量稳定模块中

---

## 2. 顶层目录总览

当前 `backend/` 顶层主要可以分成 5 类：

### A. 入口文件

- [main.py](/Users/clara/pdfPipeline/backend/main.py)
  - PDF / Markdown 主入口
  - 负责 OCR、清洗、调用 extractor
  - 支持 `--enable-analysis`

- [extractor.py](/Users/clara/pdfPipeline/backend/extractor.py)
  - 节点/边提取的兼容入口
  - 对外暴露 `process_md(...)`
  - 内部转发到 `pipeline.orchestrator`

- [analysis_layer.py](/Users/clara/pdfPipeline/backend/analysis_layer.py)
  - analysis 独立兼容入口
  - 对外暴露：
    - `run_analysis_layer(...)`
    - `process_node_file(...)`

- [server.py](/Users/clara/pdfPipeline/backend/server.py)
  - Flask 服务入口
  - 对接前端 API

- [run_jobs.py](/Users/clara/pdfPipeline/backend/run_jobs.py)
  - 批量任务入口
  - 统一读取配置文件和环境变量
  - 适合日常跑多个语料

### B. 配置文件

- [run_jobs_config.json](/Users/clara/pdfPipeline/backend/run_jobs_config.json)
  - 当前批处理任务配置
  - 配输入文件、输出目录、是否启用 analysis

- [run_jobs_config.example.json](/Users/clara/pdfPipeline/backend/run_jobs_config.example.json)
  - 批处理配置模板

- [.env](/Users/clara/pdfPipeline/backend/.env)
  - 本地环境变量
  - 主要放 API URL / model / key / Neo4j 凭据
  - ⚠️ 已加入 `.gitignore`，不会被提交

- [.env.example](/Users/clara/pdfPipeline/backend/.env.example)
  - `.env` 模板

- [requirements.txt](/Users/clara/pdfPipeline/backend/requirements.txt)
  - Python 依赖

### C. 核心实现目录

- [pipeline/](/Users/clara/pdfPipeline/backend/pipeline)
  - 提取流水线主实现

- [JoinAgent/](/Users/clara/pdfPipeline/backend/JoinAgent)
  - 原有 LLM / parser / multi-process / formatter 支撑库

### D. 工具与集成目录

- [tools/](/Users/clara/pdfPipeline/backend/tools)
  - 运行期工具脚本

- [integrations/](/Users/clara/pdfPipeline/backend/integrations)
  - 外部系统集成，例如 Neo4j

### E. 数据 / 文档 / 测试资源目录

- [books/](/Users/clara/pdfPipeline/backend/books)
  - 本地 markdown 源语料（仅 `.md` 文件）
  - 阶段中间产物现已隔离到最终输出目录旁的 `_stage_cache/` 子目录（见下文）

- [uploads/](/Users/clara/pdfPipeline/backend/uploads)
  - 上传文件及其输出

- [test_output/](/Users/clara/pdfPipeline/backend/test_output)
  - 测试输出目录

- [docs/](/Users/clara/pdfPipeline/backend/docs)
  - 文档

- [scripts/](/Users/clara/pdfPipeline/backend/scripts)
  - 测试脚本、一次性脚本

- [assets/](/Users/clara/pdfPipeline/backend/assets)
  - 样式、静态资源

- [samples/](/Users/clara/pdfPipeline/backend/samples)
  - 示例文件

- [notebooks/](/Users/clara/pdfPipeline/backend/notebooks)
  - Notebook 草稿和实验

---

## 3. `pipeline/` 目录说明

`pipeline/` 是重构后的核心目录，负责承载“真正的业务流程实现”。

### 3.1 顶层文件

- [config.py](/Users/clara/pdfPipeline/backend/pipeline/config.py)
  - 环境变量加载
  - LLM 配置解析
  - API URL 规范化
  - bool 配置解析

- [context.py](/Users/clara/pdfPipeline/backend/pipeline/context.py)
  - 运行上下文对象 `PipelineContext`
  - 保存：
    - `llm`
    - `parser`
    - `divider`
    - `api_url / model_name / api_key`
    - `output_dir`（中间产物目录，自动指向 `<最终输出目录>/_stage_cache/`；若未提供最终输出路径，则回退到输入目录）
    - `checkpoint_root`
    - 线程数和 checkpoint 参数

- [orchestrator.py](/Users/clara/pdfPipeline/backend/pipeline/orchestrator.py)
  - 流水线总编排器
  - 负责串联各阶段
  - 控制 `enable_analysis`

- [__init__.py](/Users/clara/pdfPipeline/backend/pipeline/__init__.py)
  - 仅作为 package 标记，不承载复杂逻辑

---

## 4. `pipeline/common/` 目录说明

这里放的是“跨多个阶段稳定复用”的共性逻辑。

- [io.py](/Users/clara/pdfPipeline/backend/pipeline/common/io.py)
  - JSON 读写
  - 输出目录创建
  - stage dump 保存
  - analysis 默认输出路径生成

- [llm_task.py](/Users/clara/pdfPipeline/backend/pipeline/common/llm_task.py)
  - `MultiProcessor` 的统一薄包装
  - 让各阶段不用重复写初始化样板

- [latex.py](/Users/clara/pdfPipeline/backend/pipeline/common/latex.py)
  - LaTeX 命令冻结与解冻
  - mapping 合并

- [node.py](/Users/clara/pdfPipeline/backend/pipeline/common/node.py)
  - node getter
  - node 清洗与规范化
  - `global_id` 生成
  - 关系抽取时使用的 node 访问辅助函数

原则：
- 只有明显跨阶段共用的稳定逻辑才放进来
- 具体阶段的专属数据变换，仍留在各自阶段目录

---

## 5. `pipeline/stages/` 目录说明

这里是这次模块化的核心：**每个业务阶段一个目录**。

每个阶段一般包含：

- `templates.py`
  - `data_template`
  - `prompt_template`
  - `correction_prompt`
  - `validator`

- `stage.py`
  - 该阶段的输入适配
  - LLM 调用
  - 结果整理与输出

### 5.1 `correct_text/`

- 作用：对分割后的 markdown 文本做初步纠错
- 主要产物：
  - `freeze_text_dict.json`
  - `corrected_text_dict.json`

### 5.2 `segment_blocks/`

- 作用：识别数学内容边界，按逻辑单元切块
- 主要产物：
  - `problem_dict.json`
  - `mapping_dict`（内存中）

### 5.3 `extract_statements/`

- 作用：从切好的块中抽取逻辑单元内容、证明、标签
- 主要产物：
  - `unsplit_statement_dict.json`

### 5.4 `split_nodes/`

- 作用：把一个块中可能包含的多个独立结论拆成多个节点
- 主要产物：
  - `statement_without_title_dict.json`

### 5.5 `generate_titles/`

- 作用：给每个节点生成中英文标题
- 主要产物：
  - `definition_axiom_dict.json`
  - `structured_input_dict.json`

### 5.6 `extract_logic_tuples/`

- 作用：
  - 把节点转成结构化逻辑元组
  - 处理节点排序
  - 合并定义类节点与普通节点
  - 执行解冻和节点清洗
- 主要产物：
  - `node_dict.json`
  - `pre_unfreeze_node_dict.json`
  - `merged_mapping.json`

### 5.7 `analysis/`

- 作用：
  - 对已有 extraction 结果做二次诊断
  - 输出 `analysis_layer` 与 `repair_suggestion`
- 主要产物：
  - `analysis_checkpoint/`
  - `analysis_debug/`
  - 调试文件：
    - `analysis_input.json`
    - `analysis_result.json`
    - `analysis_missing_indices.json`

### 5.8 `build_relations/`

- 作用：
  - 节点规范化
  - 显式引用关系抽取
  - 语义候选 pair 构造
  - LLM 关系判断
  - 合并边结果
- 主要产物：
  - `edge_list`（最终输出）

### 5.9 `finalize_output/`

- 作用：
  - 清理临时字段
  - 把 node / edge 写入最终输出路径

---

## 6. `JoinAgent/` 目录说明

这个目录不是本次重构新增的，而是原有底层依赖。

主要包括：

- `LLM_API/`
  - LLM 调用实现
- `LLM_Parser/`
  - parse_list / parse_dict / parse_pads
- `Multi_Process/`
  - 多线程任务执行器 `MultiProcessor`
- `Lean_Processor/`
  - 文本切分、格式处理工具
- `PDF_Processor/`
  - PDF 处理相关辅助

你可以把它理解为：
- `pipeline/` 负责业务流程编排
- `JoinAgent/` 负责基础能力支撑

---

## 7. 其他目录说明

### `tools/`

- [cleaner.py](/Users/clara/pdfPipeline/backend/tools/cleaner.py)
  - OCR 后文件清洗与归档

### `integrations/`

- [neo4j_handler.py](/Users/clara/pdfPipeline/backend/integrations/neo4j_handler.py)
  - Neo4j 数据写入与读取

### `scripts/`

- [test_c1_order.py](/Users/clara/pdfPipeline/backend/scripts/test_c1_order.py)
  - 顺序保持测试

- [test_neo4j.py](/Users/clara/pdfPipeline/backend/scripts/test_neo4j.py)
  - Neo4j 连接测试

### `docs/`

- [README.md](/Users/clara/pdfPipeline/backend/docs/README.md)
  - 使用说明

- [ARCHITECTURE.md](/Users/clara/pdfPipeline/backend/docs/ARCHITECTURE.md)
  - 当前这份架构说明

### `assets/`

- 存放样式资源、静态素材

### `samples/`

- 存放示例输入

### `notebooks/`

- 存放试验性 notebook

### `books/`
- 本地 markdown 源语料
- 中间产物已隔离到 `<最终输出目录>/_stage_cache/`（自动创建，已加入 `.gitignore`）

### `uploads/`

- 来自 API / 前端上传的输入文件和输出目录

### `test_output/`

- 测试运行输出
- 例如单独跑 `combined_algebra.md` 时的 node / edge / analysis 结果

---

## 8. 现在的调用关系

### 主流程

`run_jobs.py`
-> `main.py`
-> `extractor.py`
-> `pipeline/orchestrator.py`
-> `pipeline/stages/*`

### analysis 单独调用

`analysis_layer.py`
-> `pipeline/stages/analysis/stage.py`

### 服务调用

`server.py`
-> `main.py`
-> `extractor.py`
-> `pipeline/orchestrator.py`

---

## 9. 新增模块时应该怎么放

如果以后你还要新增一个阶段，推荐按下面方式做：

1. 在 `pipeline/stages/` 下新建一个并列目录
2. 把该阶段的 prompt / template 放进 `templates.py`
3. 把执行逻辑放进 `stage.py`
4. 在 `pipeline/orchestrator.py` 中插入这个阶段
5. 只有当该阶段的逻辑明显跨多个阶段复用时，才考虑抽到 `common/`

这样可以保证：

- 新阶段有清晰边界
- 目录结构不会再次回到“大文件堆叠”
- 以后调试时能直接定位到“问题属于哪个阶段”

---

## 10. 凭据管理

所有敏感凭据统一从 `.env` 文件加载，**不在代码中硬编码**。

| 变量 | 用途 | 使用方 |
|------|------|--------|
| `PDFPIPELINE_API_URL` | LLM API 地址 | `pipeline/config.py` → `SimpleLLM` |
| `PDFPIPELINE_MODEL_NAME` | LLM 模型名 | 同上 |
| `PDFPIPELINE_API_KEY` | LLM API 密钥 | 同上 |
| `NEO4J_URI` | Neo4j 连接地址 | `server.py` |
| `NEO4J_USER` | Neo4j 用户名 | `server.py` |
| `NEO4J_PASSWORD` | Neo4j 密码 | `server.py` |

首次使用时，复制 `.env.example` 为 `.env` 并填入实际值。

---

## 11. 中间产物隔离
不再落在输入文件同目录，而是自动写入 `<最终输出目录>/_stage_cache/`；若未提供最终输出路径，则回退到输入目录。
各 stage 的中间 JSON 产物（`corrected_text_dict.json`、`freeze_text_dict.json` 等）

- 中间产物：`<最终输出目录>/_stage_cache/corrected_text_dict.json`、`<最终输出目录>/_stage_cache/checkpoint/` 等


例如：
- 输入：`books/combined_algebra.md`
中间产物：`<最终输出目录>/_stage_cache/corrected_text_dict.json`、`<最终输出目录>/_stage_cache/checkpoint/` 等
- 最终输出（node/edge JSON）：由 `output_root` 或 `output_node_path` / `output_edge_path` 控制

`_stage_cache/` 已加入 `.gitignore`，不会被提交。

---

## 12. 一句话总结

现在这套结构的核心思想是：

- 顶层保留入口
- `pipeline/` 承载主业务
- `stages/` 按阶段拆分
- `common/` 只放稳定复用件
- `analysis` 不再是外挂脚本，而是一个与其他阶段并列的正式阶段

如果你后面继续演化这套系统，这份目录组织可以继续支撑新增 extraction 子阶段、diagnostic 阶段、或者新的 relation 阶段，而不用再回到单文件巨型脚本模式。
