import os
import shutil

def clean_mineru_output(pdf_path, output_root_dir, final_output_dir):
    """
    逻辑：
    1. MinerU 在 output_root_dir 下生成临时文件夹 ([FileStem])。
    2. 找到 auto/xxx.md。
    3. 把它移动到新的专用输出目录 (final_output_dir)。
    4. 删除 MinerU 生成的临时文件夹 ([FileStem])。
    
    返回: 最终清洗好的 md 文件路径
    """
    # 获取文件名（无后缀），例如 Game_5
    pdf_filename = os.path.basename(pdf_path)
    file_stem = os.path.splitext(pdf_filename)[0]

    # MinerU 生成的临时目录路径，例如 D:/Project/Game_5
    generated_folder = os.path.join(output_root_dir, file_stem)
    
    # 预期的 Markdown 文件位置: generated_folder/hybrid_auto/Game_5.md
    target_md_in_auto = os.path.join(generated_folder, "hybrid_auto", f"{file_stem}.md")
    
    # ✅ 修正目标路径：现在文件要移动到指定的 final_output_dir
    final_md_path = os.path.join(final_output_dir, f"{file_stem}.md")

    print(f"   [Cleaner] 正在寻找临时文件: {target_md_in_auto}")

    if not os.path.exists(target_md_in_auto):
        print(f"❌ [Cleaner] 错误：未找到 MinerU 生成的 Markdown 文件。")
        print(f"   期待路径: {target_md_in_auto}")
        # 如果临时文件夹存在，说明 MinerU 没出错，而是文件没生成出来，保留临时文件夹供排查
        if os.path.exists(generated_folder):
             print(f"   ⚠️ 保留临时目录 {generated_folder} 供检查。")
        return None

    try:
        # 1. 移动文件 (目标路径已修正为 final_output_dir)
        shutil.move(target_md_in_auto, final_md_path)
        print(f"✅ [Cleaner] Markdown 已提取到目标目录: {final_md_path}")

        # 2. 删除临时文件夹 (删除 [FileStem] 目录)
        if os.path.exists(generated_folder):
            shutil.rmtree(generated_folder)
            print(f"🗑️ [Cleaner] 已清理临时目录: {generated_folder}")
            
        return final_md_path

    except Exception as e:
        print(f"❌ [Cleaner] 文件操作失败，请检查权限: {e}")
        return None