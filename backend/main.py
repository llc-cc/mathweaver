import os
import sys
import subprocess
import argparse
import shutil # 引入 shutil 确保文件移动功能可用
import json
#os.environ["MINERU_SKIP_LANG_DETECT"] = "1" #设置环境变量以跳过 MinerU 的语言检测（12.6）


# 导入我们的模块
try:
    from tools.cleaner import clean_mineru_output
except ImportError:
    print("❌ 无法导入 cleaner.py，请确保文件存在。")
    sys.exit(1)

try:
    from extractor import process_md
except ImportError:
    print("❌ 无法导入 extractor.py，请确保它在同一目录下且依赖(JoinAgent)已正确安装。")
    sys.exit(1)

from pipeline.config import load_env_file, resolve_llm_config


def process_pdf_to_json(pdf_path: str, api_url: str = None,
                       model_name: str = None, api_key: str = None,
                       enable_analysis: bool = False,
                       enable_math_disambiguation: bool = True,
                       output_root_dir: str = None,
                       output_natural_nodes: bool = False,
                       edge_output_mode: str = "structured",
                       relation_prompt_profile: str = "graph",
                       source_format: str = "auto") -> dict:
    """
    处理 PDF 文件，返回知识图谱数据（供后端 API 调用）
    
    Args:
        pdf_path: PDF 文件路径
        api_url: 大模型 API 地址（可选）
        model_name: 模型名称（可选）
        api_key: API 密钥（可选）
    
    Returns:
        dict: {'nodes': [...], 'edges': [...]}
    
    Raises:
        Exception: 处理失败时抛出异常
    """
    try:
        load_env_file()
        resolved = resolve_llm_config(api_url, model_name, api_key)
        api_url = resolved.api_url
        model_name = resolved.model_name
        api_key = resolved.api_key

        print("="*60)
        print("📡 后端 API 调用模式")
        print(f"📄 PDF: {os.path.basename(pdf_path)}")
        if api_url:
            print(f"🤖 API: {api_url}")
            print(f"📝 Model: {model_name}")
        print("="*60)
        
        # 调用流程，传递 API 配置
        result = run_pipeline(
            pdf_path, 
            api_url=api_url, 
            model_name=model_name,
            api_key=api_key,
            enable_analysis=enable_analysis,
            output_root_dir=output_root_dir,
            output_natural_nodes=output_natural_nodes,
            edge_output_mode=edge_output_mode,
            relation_prompt_profile=relation_prompt_profile,
            source_format=source_format,
            return_data=True  # 表示需要返回数据
        )
        
        if result is None:
            raise Exception("处理流程失败，未返回数据")
        
        return result
        
    except Exception as e:
        print(f"❌ process_pdf_to_json 失败: {str(e)}")
        raise

    
def run_pipeline(
    pdf_path,
    api_url=None,
    model_name=None,
    api_key=None,
    enable_analysis=False,
    enable_math_disambiguation=True,
    output_root_dir=None,
    output_natural_nodes=False,
    edge_output_mode="structured",
    relation_prompt_profile="graph",
    source_format="auto",
    return_data=False,
):
    """
    运行 PDF 处理流程
    
    Args:
        pdf_path: PDF 文件路径
        api_url: 大模型 API 地址
        model_name: 模型名称
        api_key: API 密钥
        return_data: 是否返回数据（True=返回dict，False=仅打印）
    
    Returns:
        dict 或 None: 如果 return_data=True，返回 {'nodes': [], 'edges': []}
    """
    # ---------------------------------------------------------
    # 0. 路径与环境准备
    # ---------------------------------------------------------
    input_path = os.path.abspath(pdf_path)
    working_dir = os.path.dirname(input_path)
    if output_root_dir:
        output_root_dir = os.path.abspath(output_root_dir)
        os.makedirs(output_root_dir, exist_ok=True)
    else:
        output_root_dir = working_dir
    file_stem = os.path.splitext(os.path.basename(input_path))[0]
    file_ext = os.path.splitext(os.path.basename(input_path))[1].lower() # 获取文件扩展名

    load_env_file()
    resolved = resolve_llm_config(api_url, model_name, api_key)
    api_url = resolved.api_url
    model_name = resolved.model_name
    api_key = resolved.api_key
    
    # 1. 定义新旧路径
    output_folder_name = f"{file_stem}_output"
    final_output_dir = os.path.join(output_root_dir, output_folder_name)
    final_md_path = os.path.join(final_output_dir, f"{file_stem}.md")
    legacy_md_path = os.path.join(working_dir, f"{file_stem}.md") # 旧版本的输出位置
    
    if not os.path.exists(pdf_path):
        print(f"❌ 文件不存在: {pdf_path}")
        return

    print("="*60)
    print(f"🚀 开始处理任务: {os.path.basename(pdf_path)}")
    print(f"📄 文件类型: {'PDF' if file_ext == '.pdf' else ('TeX' if file_ext == '.tex' else ('Markdown' if file_ext == '.md' else 'Unknown'))}")
    print(f"📂 目标输出目录: {final_output_dir}")
    if api_url:
        print(f"🤖 使用大模型: {model_name or 'default'}")
    print("="*60)

    skipped_ocr = False
    md_file_path = None
    
    # ---------------------------------------------------------
    # 步骤 1: 条件式处理 (PDF 或 Markdown)
    # ---------------------------------------------------------
    if file_ext in {'.md', '.tex'}:
        # ---------------------------------------------------------
        # Markdown/TeX 输入，直接跳过 OCR 和清洗
        # ---------------------------------------------------------
        print("\n>>> [Step 1&2/3] 检测到 Markdown/TeX 输入，跳过 OCR 转换和清洗。")
        md_file_path = input_path
        os.makedirs(final_output_dir, exist_ok=True) # 确保输出目录存在
        
    elif file_ext == '.pdf':

        print("\n>>> [Step 1/3] 检查 MinerU OCR 状态...")

        if os.path.exists(legacy_md_path):
            # 若文件已经在旧位置，需要移动
            print(f"⚠️  检测到 Markdown 文件在旧目录: {os.path.basename(legacy_md_path)}")
            print(f"⏭️  跳过 OCR 转换步骤，准备归档。")
            skipped_ocr = True
            md_file_path = legacy_md_path # 暂时指向旧路径

        else:
            # 若文件不存在，运行 OCR
            os.makedirs(final_output_dir, exist_ok=True) # 确保目录在 MinerU 运行前就存在
            print(f"▶️  正在启动 MinerU 进行转换...")
            
            cmd = [
                "uv", "run", "mineru",
                "-p", pdf_path,
                "-o", working_dir 
            ]
            
            try:
                subprocess.run(cmd, check=True, shell=True)
                print("✅ MinerU 转换成功。")
            except subprocess.CalledProcessError:
                print("❌ MinerU 转换过程中断或失败。请检查 MinerU 是否安装正确。")
                return

        # ---------------------------------------------------------
        # 步骤 2: 清洗和整理目录 (兼容性处理)
        # ---------------------------------------------------------
        print("\n>>> [Step 2/3] 检查文件结构清洗状态...")

        if skipped_ocr:
            # 如果跳过了 OCR
            if md_file_path == legacy_md_path:
                # Case 2: 发现文件在旧位置，执行移动操作
                os.makedirs(final_output_dir, exist_ok=True) # 确保目录存在
                shutil.move(legacy_md_path, final_md_path)
                md_file_path = final_md_path # 更新为新的最终路径
                print(f"✅ 历史文件已移动并归档到 {os.path.basename(final_output_dir)} 目录。")
            else:
                # Case 1: 文件已在最终位置
                print(f"⏭️  由于跳过了 OCR，且文件已在目标位置，无需进行目录清洗。")
                
        else:
            # 如果运行了 OCR (优先级 3)
            print(f"▶️  正在清洗 MinerU 生成的临时目录并移动文件...")
            # 此时 md_file_path 将由 cleaner 返回最终路径 (final_md_path)
            md_file_path = clean_mineru_output(pdf_path, working_dir, final_output_dir)
            
            if not md_file_path:
                print("❌ 清洗步骤失败，无法找到生成的 Markdown 文件，终止流程。")
                return
            print("✅ 清洗完成，文件已提取。")

    else:
        # 其他文件类型，报错
        print(f"❌ 不支持的文件类型: {file_ext}，终止流程。")
        return {"success": False, "error": f"不支持的文件类型: {file_ext}", "directory": final_output_dir}

    # ---------------------------------------------------------
    # 步骤 3: 运行 Extractor 提取知识
    # ---------------------------------------------------------
    print("\n>>> [Step 3/3] 正在运行 Extractor 知识提取...")
    
    # 再次确认文件存在（双重保险）
    if not md_file_path or not os.path.exists(md_file_path):
        print(f"❌ 错误: 无法读取 Markdown 文件: {md_file_path}")
        return None

    # 定义 JSON 输出路径 (指向 final_output_dir)
    output_node_json = os.path.join(final_output_dir, f"{file_stem}_node.json")
    output_edge_json = os.path.join(final_output_dir, f"{file_stem}_edge.json")
    output_edge_natural_json = os.path.join(final_output_dir, f"{file_stem}_edge_natural.json")
    output_natural_node_json = os.path.join(final_output_dir, f"{file_stem}_node_natural.json")
    
    try:
        print(f"▶️  开始分析 Markdown 内容 (Extractor)...")
        
        node_list, edge_list = process_md(
            md_file_path, 
            output_node_json if not return_data else None,  # 如果返回数据就不保存文件
            output_edge_json if not return_data else None,
            output_natural_node_json if (output_natural_nodes and not return_data) else None,
            api_url=api_url,       # 👈 传递用户的 API 配置
            model_name=model_name,
            api_key=api_key,
            enable_analysis=enable_analysis,
            enable_math_disambiguation=enable_math_disambiguation,
            edge_output_mode=edge_output_mode,
            relation_prompt_profile=relation_prompt_profile,
            source_format=source_format,
        )

        print("\n" + "="*60)
        print("🎉 全部流程圆满完成！")
        print("-" * 60)
        print(f"📂 输出目录:  {os.path.basename(final_output_dir)}")
        print(f"Markdown:  {os.path.basename(md_file_path)}")
        if not return_data:
            print(f"Node JSON: {os.path.basename(output_node_json)}")
            if output_natural_nodes:
                print(f"Node JSON (natural): {os.path.basename(output_natural_node_json)}")
            if edge_output_mode == "both":
                print(f"Edge JSON: {os.path.basename(output_edge_json)}")
                print(f"Edge JSON (natural): {os.path.basename(output_edge_natural_json)}")
            elif edge_output_mode == "natural":
                print(f"Edge JSON (natural mode): {os.path.basename(output_edge_json)}")
            else:
                print(f"Edge JSON (structured mode): {os.path.basename(output_edge_json)}")
        print("="*60)
        
        # ✅ 如果需要返回数据（被后端调用时）
        if return_data:
            return {
                'nodes': node_list,
                'edges': edge_list
            }
        
        return True  # 命令行模式返回成功标志
    
    except Exception as e:
        print(f"❌ Extractor 运行出错: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    # 设置命令行参数
    parser = argparse.ArgumentParser(description="PDF 知识提取流水线")
    parser.add_argument("pdf_path", help="PDF 文件的路径")
    parser.add_argument("--api-url", help="大模型 API 地址", default=None)
    parser.add_argument("--model-name", help="模型名称", default=None)
    parser.add_argument("--api-key", help="API 密钥", default=None)
    parser.add_argument("--enable-analysis", action="store_true", help="是否附加 analysis_layer 后处理")
    parser.add_argument(
        "--disable-math-disambiguation",
        action="store_true",
        help="关闭数学符号歧义消解阶段",
    )
    parser.add_argument("--output-root-dir", default=None, help="输出根目录，默认与输入文件同目录")
    parser.add_argument(
        "--output-natural-nodes",
        action="store_true",
        help="额外输出未致密化的自然语言节点 JSON（*_node_natural.json）",
    )
    parser.add_argument(
        "--edge-output-mode",
        choices=["structured", "natural", "both"],
        default="structured",
        help="边输出模式：structured=仅结构化版, natural=仅自然语言版, both=同时输出两版",
    )
    parser.add_argument(
        "--relation-prompt-profile",
        choices=["graph", "formalization"],
        default="graph",
        help="关系提示词版本：graph=知识图谱默认版, formalization=自动形式化辅助版",
    )
    
    parser.add_argument("--source-format", choices=["auto", "markdown", "tex"], default="auto")
    args = parser.parse_args()
    
    # 命令行模式：不返回数据，直接保存文件
    run_pipeline(
        args.pdf_path,
        api_url=args.api_url,
        model_name=args.model_name,
        api_key=args.api_key,
        enable_analysis=args.enable_analysis,
        enable_math_disambiguation=not args.disable_math_disambiguation,
        output_root_dir=args.output_root_dir,
        output_natural_nodes=args.output_natural_nodes,
        edge_output_mode=args.edge_output_mode,
        relation_prompt_profile=args.relation_prompt_profile,
        source_format=args.source_format,
        return_data=False
    )
