# backend/server.py

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import sys
from pathlib import Path
from werkzeug.utils import secure_filename
import traceback
import json
import io
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime
import time
import re

# 修复 Windows GBK 编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 添加路径
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root / 'MyPDFPipeline'))

from main import process_pdf_to_json
from pipeline.config import load_env_file
from integrations.neo4j_handler import Neo4jHandler

# 加载 .env（确保环境变量可用）
load_env_file()

app = Flask(__name__)
# ✅ 优化 CORS 配置
CORS(app, resources={
    r"/api/*": {
        "origins": "*",  # 生产环境应指定具体域名
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"],
        "expose_headers": ["Content-Disposition"],
        "max_age": 3600
    }
})

# ✅ 添加请求超时配置
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # 禁用静态文件缓存

UPLOAD_FOLDER = Path(__file__).parent / 'uploads'
UPLOAD_FOLDER.mkdir(exist_ok=True)

# ✅ Neo4j 凭据从环境变量读取（连接失败时不阻塞服务启动）
neo4j_handler = None
try:
    neo4j_handler = Neo4jHandler(
        uri=os.environ.get("NEO4J_URI", ""),
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ.get("NEO4J_PASSWORD", ""),
    )
except Exception as e:
    print(f"[WARNING] Neo4j 连接失败，图谱功能不可用: {e}")

# 存储最后处理的数据（用于下载）
last_processed_data = {
    'markdown': '',
    'json_nodes': [],
    'json_edges': [],
    'pdf_name': ''
}


# 全局任务状态存储
processing_tasks = {}
# ✅ 添加：终止标志字典
stop_flags = {}

@app.route('/api/process-pdf-async', methods=['POST'])
def process_pdf_async():
    """异步处理 PDF"""
    try:
        if 'pdfFile' not in request.files:
            return jsonify({'success': False, 'error': '没有上传文件'}), 400
        
        pdf_file = request.files['pdfFile']
        api_url = request.form.get('apiUrl')
        model_name = request.form.get('modelName')
        api_key = request.form.get('apiKey')
        enable_analysis = request.form.get('enableAnalysis', 'false').lower() == 'true'
        
        if not all([api_url, model_name, api_key]):
            return jsonify({'success': False, 'error': 'API配置不完整'}), 400
        
        # 保存文件
        filename = secure_filename(pdf_file.filename)
        pdf_path = UPLOAD_FOLDER / filename
        pdf_file.save(str(pdf_path))
        
        # 生成任务 ID
        task_id = str(uuid.uuid4())

        # ✅ 初始化终止标志
        stop_flags[task_id] = False

        # 初始化任务状态
        processing_tasks[task_id] = {
            'status': 'processing',
            'progress': 0,
            'message': '正在处理 PDF...',
            'result': None,
            'error': None,
            'start_time': datetime.now().isoformat()
        }
        
        # 启动后台线程处理
        def process_in_background():
            try:
                # ✅ 在各个步骤中检查终止标志
                if stop_flags.get(task_id, False):
                    raise Exception("用户取消了处理")
                
                # 更新状态：OCR 中
                processing_tasks[task_id]['progress'] = 10
                processing_tasks[task_id]['message'] = '步骤 1/3: 正在进行 OCR 转换...'
                
                if stop_flags.get(task_id, False):
                    raise Exception("用户取消了处理")
                
                result = process_pdf_to_json(
                    pdf_path=str(pdf_path),
                    api_url=api_url,
                    model_name=model_name,
                    api_key=api_key,
                    enable_analysis=enable_analysis,
                )
                processing_tasks[task_id]['message'] = '步骤 2/3: 正在分析条目...'

                if stop_flags.get(task_id, False):
                    raise Exception("用户取消了处理")
                
                # ✅ 添加：读取生成的 Markdown 文件
                md_path = pdf_path.parent / f"{pdf_path.stem}_output" / f"{pdf_path.stem}.md"
                markdown_content = ''
                if md_path.exists():
                    with open(md_path, 'r', encoding='utf-8') as f:
                        markdown_content = f.read()

                # 更新状态：存入 Neo4j
                processing_tasks[task_id]['progress'] = 80
                processing_tasks[task_id]['message'] = '步骤 3/3: 正在存入 Neo4j...'
                
                nodes = result.get('nodes', [])
                edges = result.get('edges', [])
                
                # 存入数据库
                neo4j_result = neo4j_handler.create_graph(nodes, edges, filename)
                
                # 保存到全局变量（用于下载）
                global last_processed_data
                last_processed_data = {
                    'markdown': markdown_content,  # ✅ 添加这一行
                    'json_nodes': nodes,
                    'json_edges': edges,
                    'pdf_name': filename
                }
                
                # 完成
                processing_tasks[task_id]['status'] = 'completed'
                processing_tasks[task_id]['progress'] = 100
                processing_tasks[task_id]['message'] = '处理完成'
                processing_tasks[task_id]['result'] = {
                    'nodes': nodes,
                    'edges': edges,
                    'neo4j_saved': True
                }
                
            except Exception as e:
                # ✅ 区分取消和错误
                if "用户取消" in str(e):
                    processing_tasks[task_id]['status'] = 'cancelled'
                    processing_tasks[task_id]['error'] = '已取消'
                else:
                    processing_tasks[task_id]['status'] = 'failed'
                    processing_tasks[task_id]['error'] = str(e)
                print(f"后台处理失败: {e}")
                traceback.print_exc()
            
            finally:
                # 清理临时文件
                stop_flags.pop(task_id, None)
                if pdf_path.exists():
                    try:
                        pdf_path.unlink()
                    except:
                        pass
        
        # 启动线程
        thread = threading.Thread(target=process_in_background)
        thread.daemon = True
        thread.start()
        
        # 立即返回任务 ID
        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': '任务已创建，正在后台处理'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# 修改取消路由：
@app.route('/api/cancel-task/<task_id>', methods=['POST'])
def cancel_task(task_id):
    """取消任务"""
    if task_id not in processing_tasks:
        return jsonify({'error': '任务不存在'}), 404
    
    task = processing_tasks[task_id]
    if task['status'] != 'processing':
        return jsonify({'error': '任务不在处理中'}), 400
    
    # ✅ 设置停止标志
    stop_flags[task_id] = True
    return jsonify({'success': True, 'message': '任务已开始终止'})


@app.route('/api/task-status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """查询任务状态"""
    if task_id not in processing_tasks:
        return jsonify({'error': '任务不存在'}), 404
    
    return jsonify({
        'success': True,
        'task': processing_tasks[task_id]
    })


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'ok',
        'message': 'Backend is running',
        'neo4j': 'connected'
    })

# 在 health_check 路由后添加
@app.route('/api/ping', methods=['GET'])
def ping():
    """简单的心跳检查"""
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

@app.route('/api/process-pdf', methods=['POST'])
def process_pdf():
    """处理 PDF 并存入 Neo4j"""
    global last_processed_data
    pdf_path = None
    
    try:
        print("\n" + "="*60)
        print("📥 收到 PDF 处理请求")
        
        if 'pdfFile' not in request.files:
            return jsonify({'success': False, 'error': '没有上传文件'}), 400
        
        pdf_file = request.files['pdfFile']
        api_url = request.form.get('apiUrl')
        model_name = request.form.get('modelName')
        api_key = request.form.get('apiKey')
        enable_analysis = request.form.get('enableAnalysis', 'false').lower() == 'true'
        
        if not all([api_url, model_name, api_key]):
            return jsonify({'success': False, 'error': 'API配置不完整'}), 400
        
        filename = secure_filename(pdf_file.filename)
        pdf_path = UPLOAD_FOLDER / filename
        pdf_file.save(str(pdf_path))
        
        print(f"📄 文件: {filename}")
        print(f"🤖 API: {api_url}")
        print(f"📝 模型: {model_name}")
        
        # 调用处理函数
        print("\n🚀 开始处理...")
        result = process_pdf_to_json(
            pdf_path=str(pdf_path),
            api_url=api_url,
            model_name=model_name,
            api_key=api_key,
            enable_analysis=enable_analysis,
        )
        
        nodes = result.get('nodes', [])
        edges = result.get('edges', [])
        
        print(f"\n✅ 处理完成")
        print(f"📊 节点: {len(nodes)}, 关系: {len(edges)}")
        
        # 读取生成的 Markdown（如果存在）
        md_path = pdf_path.parent / f"{pdf_path.stem}_output" / f"{pdf_path.stem}.md"
        markdown_content = ''
        if md_path.exists():
            with open(md_path, 'r', encoding='utf-8') as f:
                markdown_content = f.read()
        
        # 保存到全局变量（用于下载）
        last_processed_data = {
            'markdown': markdown_content,
            'json_nodes': nodes,
            'json_edges': edges,
            'pdf_name': filename
        }
        
        # 存入 Neo4j
        print("\n💾 正在存入 Neo4j...")
        neo4j_result = neo4j_handler.create_graph(nodes, edges, filename)
        print(f"✅ Neo4j 存储完成")
        print(f"   节点: {neo4j_result['nodes_created']}")
        print(f"   关系: {neo4j_result['edges_created']}")
        print("="*60 + "\n")
        
        return jsonify({
            'success': True,
            'graph': {'nodes': nodes, 'edges': edges},
            'neo4j_saved': True
        })
        
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
    
    finally:
        if pdf_path and pdf_path.exists():
            try:
                pdf_path.unlink()
            except:
                pass


@app.route('/api/get-graph-from-neo4j', methods=['GET'])
def get_graph_from_neo4j():
    """从 Neo4j 加载图谱"""
    try:
        print("📥 查询 Neo4j...")
        result = neo4j_handler.get_all_graph()
        print(f"✅ 查询成功: 节点={len(result['nodes'])}, 关系={len(result['edges'])}")
        
        return jsonify({
            'success': True,
            'graph': result
        })
    except Exception as e:
        print(f"❌ Neo4j 查询失败: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# ✅ 下载 Markdown
@app.route('/api/download-markdown', methods=['GET'])
def download_markdown():
    """下载最后处理的 Markdown 文件"""
    try:
        if not last_processed_data['markdown']:
            return jsonify({'error': '没有可下载的 Markdown'}), 404
        
        filename = f"{Path(last_processed_data['pdf_name']).stem}.md"
        return send_file(
            io.BytesIO(last_processed_data['markdown'].encode('utf-8')),
            mimetype='text/markdown',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ✅ 下载 JSON
@app.route('/api/download-json', methods=['GET'])
def download_json():
    """下载 JSON 格式的图谱数据"""
    try:
        if not last_processed_data['json_nodes']:
            return jsonify({'error': '没有可下载的数据'}), 404
        
        data = {
            'nodes': last_processed_data['json_nodes'],
            'edges': last_processed_data['json_edges']
        }
        
        filename = f"{Path(last_processed_data['pdf_name']).stem}_graph.json"
        return send_file(
            io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')),
            mimetype='application/json',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ✅ 生成并下载 LaTeX（使用你的模板）
@app.route('/api/download-latex', methods=['GET'])
def download_latex():
    """生成并下载 LaTeX 文件"""
    try:
        if not last_processed_data['json_nodes']:
            return jsonify({'error': '没有可下载的数据'}), 404
        
        # 使用你的 json_to_latex 逻辑
        latex_content = generate_latex(last_processed_data['json_nodes'])
        
        filename = f"{Path(last_processed_data['pdf_name']).stem}.tex"
        return send_file(
            io.BytesIO(latex_content.encode('utf-8')),
            mimetype='application/x-tex',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def generate_latex(node):

    # ====== 2. 定义环境映射 ======
    node_type_map = {
        "定义": "dfn",
        "定理": "thm",
        "命题": "ppt",
        "引理": "lma",
        "推论": "crl",
        "公理": "axm",
        "性质": "ppt",
        "例子": "xmp",
        "反例": "cxmp",
        "证明": "prf",
        "习题": "xmp",
        "解答": "prf",
        "注释": "xmp"
    }

    # ====== 3. 遍历条目并生成 LaTeX 块 ======
    output_blocks = []
    for entry in node:
        node_type = entry.get("node_type", "").strip()
        node_type_short = node_type_map.get(node_type)

        # 如果没匹配上，跳过并提示
        if node_type_short is None:
            print(f"⚠️ 跳过条目：未识别的环境类型 “{node_type}”")
            continue

        title = entry.get("title", "").strip()
        title_en = entry.get("title_en", "").strip()
        content = entry.get("content", " ").strip()
        label = title_en.replace("_", " ").replace("'", "").replace(",", "").replace(":", "").replace("(", "").replace(")", "").replace("$", "").replace("^", "").replace("{", "").replace("}", "").replace("\\", "")
        original_label = entry.get("label", "").strip()
        if original_label != "":
            content = original_label + " " + content
        block = f"""
\\begin{{{node_type_short}}}
    [{label}]
    {{{title}}}
    [{title_en}]
    [gpt-4.1]
    {content}
\\end{{{node_type_short}}}

"""
        output_blocks.append(block)

    # ====== 4. 合并并输出 ======
    indented_blocks = ["    " + b.replace("\n", "\n    ") for b in output_blocks]
    result_text = " \n ".join(indented_blocks)

    header = r"""\documentclass[UTF8]{ctexart}

\usepackage{FulcrumCN}
\usepackage{geometry}
\usepackage{amssymb}
\usepackage{amsmath}
\geometry{
    paper =a4paper,
    top =3cm,
    bottom =3cm,
    left=2cm,
    right =2cm
}
\linespread{1.2}
\begin{document}
    \begin{center}
        {\LARGE\textbf{Fulcrum 自动生成示例}}

        SJTU AI4Math Team
    \end{center}

    \section{知识条目}
    \subsection{自动生成条目}
"""
    footer = r"""
    \section{附录}
    本文档由脚本自动生成，供 Fulcrum 数学知识条目测试使用。

\end{document}
"""
    result_text = header + "\n" + result_text + "\n" + footer
    return result_text

# 添加新路由
@app.route('/api/download-pdf', methods=['GET'])
def download_pdf():
    """生成并下载 PDF（宽松编译模式）"""
    try:
        if not last_processed_data['json_nodes']:
            return jsonify({'error': '没有可下载的数据'}), 404
        
        # 生成 LaTeX
        latex_content = generate_latex(last_processed_data['json_nodes'])
        
        # 创建临时目录编译
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # 复制 .sty 文件
            fulcrum_path = Path(__file__).parent / 'FulcrumCN.sty'
            if fulcrum_path.exists():
                import shutil
                shutil.copy(str(fulcrum_path), tmpdir)
            
            # 写入 .tex 文件
            tex_path = tmp_path / 'document.tex'
            with open(tex_path, 'w', encoding='utf-8') as f:
                f.write(latex_content)
            
            # 编译命令
            cmd = ['pdflatex', '-interaction=nonstopmode', 'document.tex']
            
            try:
                # 第一次编译 (忽略错误)
                subprocess.run(
                    cmd, cwd=tmpdir, capture_output=True, 
                    text=True, encoding='utf-8', errors='ignore', timeout=60
                )
                
                # 🟢 宽松检查：只要 PDF 存在，就尝试第二次编译（为了目录对齐），不管第一次是否报错
                pdf_path = tmp_path / 'document.pdf'
                if pdf_path.exists():
                    subprocess.run(
                        cmd, cwd=tmpdir, capture_output=True, 
                        text=True, encoding='utf-8', errors='ignore', timeout=60
                    )
                
                # 🟢 最终判断：只看 PDF 文件是否生成
                if pdf_path.exists():
                    with open(pdf_path, 'rb') as f:
                        pdf_content = f.read()
                    
                    filename = f"{Path(last_processed_data['pdf_name']).stem}.pdf"
                    return send_file(
                        io.BytesIO(pdf_content),
                        mimetype='application/pdf',
                        as_attachment=True,
                        download_name=filename
                    )
                else:
                    # 只有真的没生成 PDF 时才报错
                    return jsonify({'error': 'PDF 生成失败（可能存在严重语法错误）'}), 500

            except subprocess.TimeoutExpired:
                return jsonify({'error': f'LaTeX 编译超时'}), 500
            except FileNotFoundError:
                return jsonify({'error': '未找到pdflatex'}), 500
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    
    
if __name__ == '__main__':
    print("\n" + "="*60)
    print("PDF 处理后端启动")
    print("地址: http://0.0.0.0:5000")
    print(f"Neo4j: {os.environ.get('NEO4J_URI', '(未配置)')}")
    print("="*60 + "\n")

    # 设置 UTF-8 编码避免 GBK 报错
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    try:
        app.run(host='0.0.0.0', port=5000, debug=True)
    finally:
        if neo4j_handler:
            neo4j_handler.close()
