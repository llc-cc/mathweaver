# Math PDF Knowledge Pipeline (数学教材知识图谱提取流水线)

这是一个全自动化的数据处理工具，专为数学教材设计。它能将 PDF 文档转换为 Markdown，利用 LLM（大语言模型）进行语义分析，最终提取出**定义、定理**等知识节点以及它们之间的**逻辑关系**，生成结构化的知识图谱数据。

## 📂 项目目录结构

你的项目文件夹应该包含以下核心文件：

```text
backend/
│
├── main.py                 # [总指挥入口] PDF -> Markdown -> Extractor
├── extractor.py            # [兼容入口] 暴露 process_md
├── analysis_layer.py       # [兼容入口] 暴露 analysis 后处理
├── server.py               # [服务入口] Flask API
├── pipeline/               # [主实现] 按阶段组织的提取流水线
├── tools/cleaner.py        # [工具脚本] 负责文件归档和清理临时目录
├── integrations/           # [外部集成] 例如 Neo4j
├── JoinAgent/              # [依赖包] 本地大模型代理模块
└── docs/README.md          # [说明书] 本文件
````

## 📥 安装与配置

本项目依赖开源工具 **MinerU** 作为核心 OCR 引擎。在开始之前，请按照以下步骤获取代码并配置环境。

### 1\. 获取项目代码

首先，你需要从 GitHub 上克隆本项目到本地：

```bash
git clone [https://github.com/your-username/MyPDFPipeline.git](https://github.com/your-username/MyPDFPipeline.git)
cd MyPDFPipeline
```

### 2\. 环境准备 (MinerU & 依赖)

`main.py` 与桌面 API 共用同一个 OCR 组件清单、便携 Python 运行时、本地模型配置和 Markdown 结果契约。请先通过 MathWeaver 桌面端安装并完成 OCR 组件自测；不再使用 `uv run mineru` 或 `backend/.venv` 中的 MinerU。

  * **步骤 A: 检查 OCR 组件**

    桌面端 OCR 状态必须为 `ready`，组件版本和模型文件由 `backend/assets/ocr/manifest.json` 与 `%LOCALAPPDATA%\MathWeaver\ocr\current.json` 校验。组件缺失时，`main.py` 会返回与桌面 API 一致的错误，不会在命令行静默下载。

  * **步骤 B: 开发运行时覆盖（可选）**

    开发测试可设置 `MATHWEAVER_OCR_RUNTIME_DIR` 和 `MATHWEAVER_OCR_MANIFEST`，入口仍然经过同一个 `OcrManager` 解析和本地模型环境构造。

  * **步骤 C: 本地依赖**
    请确保你本地的基础 Python 环境（运行 `main.py` 的环境）已经安装了 `extractor.py` 和 `JoinAgent` 所需的库（如 `dashscope`, `pandas` 等）。

  * **步骤 D: 配置凭据**
    复制 `.env.example` 为 `.env`，填入你的 API key 和 Neo4j 凭据：
    ```bash
    cp .env.example .env
    # 编辑 .env 填入实际值
    ```
    ⚠️ `.env` 已加入 `.gitignore`，不会被提交到 Git。

## 💻 如何使用

在 `backend` 目录中直接运行 `main.py` 并传入 PDF 文件路径即可；它会复用桌面端已安装的 OCR 运行时，生成与桌面 API 相同的 `importedText` Markdown 结果：

```bash
# 语法: python main.py "PDF文件的完整路径"

# 示例
python main.py "D:\Books\LinearAlgebra.pdf"
```

## ⚙️ 自动化流程说明

脚本将自动执行以下三个步骤：

1.  **OCR 转换 (MinerU)**:
      * 调用 MinerU 将 PDF 识别为 Markdown 格式。
      * *智能跳过机制*：如果检测到目标文件已存在，会自动跳过此耗时步骤。
2.  **目录清洗 & 归档**:
      * 自动创建一个名为 `[文件名]_output` 的文件夹。
      * 将生成的 Markdown 移动到该文件夹中。
      * 自动清理 MinerU 产生的临时垃圾文件。
      * *兼容性*：如果发现旧版本生成的 Markdown 在外层，会自动帮你移动到新文件夹归档。
3.  **知识提取 (Extractor)**:
      * 读取 Markdown，提取数学实体和逻辑关系。
      * 将结果保存为两个 JSON 文件。

## 📂 输出文件说明

假设你处理的文件名为 `Game_5.pdf`，脚本运行结束后，会在同级目录下生成一个 **`Game_5_output`** 文件夹，其中包含以下三个核心文件：

### 1\. `Game_5.md` (基础素材)

  * **类型**: Markdown 文本
  * **来源**: 由 MinerU OCR 生成。
  * **用途**: 包含了PDF中的所有文本内容、公式（LaTeX格式），是后续所有分析的数据源。

### 2\. `Game_5_node.json` (知识节点)

  * **类型**: JSON 列表
  * **内容**: 书中提取出的所有逻辑单元。
  * **结构示例**:
    ```json
    [
        {
            "env": "定理",
            "title": "代数基本定理",
            "content": "设 f 为复系数 n 次多项式..."
        },
        ...
    ]
    ```

### 3\. `Game_5_edge.json` (逻辑关系)

  * **类型**: JSON 列表
  * **内容**: 节点之间的逻辑引用或推导关系。
  * **逻辑**: 采用**同片段分析**策略，旨在捕捉**位置较近**的逻辑单元之间的联系（即在同一上下文片段或相邻语境中出现的概念关联）。
  * **结构示例**:
    ```json
    [
        {
            "pos1": "复数域",           // 出发节点
            "pos2": "代数基本定理",      // 到达节点
            "relationship": "基础关系", // 关系类型
            "strength": 10             // 关系强度
        },
        ...
    ]
    ```

-----

**注意**: 如果在运行过程中遇到 `Permission denied` 错误，请确保没有其他程序（如 PDF 阅读器或编辑器）正在占用相关文件。
