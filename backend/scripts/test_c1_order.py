#!/usr/bin/env python3
"""
C-1方案顺序测试 - 无需API调用
验证顺序是否被保留
"""

import json
import sys
from collections import OrderedDict

def test_multitask_perform_order():
    """测试 multitask_perform 的返回顺序"""
    print("【测试1】multitask_perform 返回顺序")
    print("-" * 50)
    
    # 模拟原始输入（有序）
    index_dict = {0: {"pos1": "text_0"}, 1: {"pos1": "text_1"}, 2: {"pos1": "text_2"}}
    
    # 模拟并发后的结果（无序）
    results = {2: "result_2", 0: "result_0", 1: "result_1"}
    
    # C-1方案：按输入key顺序返回
    ordered_results = {k: results[k] for k in index_dict.keys() if k in results}
    
    print(f"输入顺序: {list(index_dict.keys())}")
    print(f"并发后: {list(results.keys())}")
    print(f"排序后: {list(ordered_results.keys())}")
    print(f"✅ 通过" if list(ordered_results.keys()) == list(index_dict.keys()) else "❌ 失败")
    print()

def test_extract_nonempty_blocks():
    """测试 extract_nonempty_blocks 的顺序保留"""
    print("【测试2】extract_nonempty_blocks 顺序保留")
    print("-" * 50)
    
    # 模拟输入（大切片按序）
    input_dict = {
        2: {3: "block_2_3", 1: "block_2_1"},  # 乱序的内层key
        0: {0: "block_0_0", 2: "block_0_2"},
        1: {1: "block_1_1"}
    }
    
    # 执行逻辑
    new_dict = {}
    new_id = 0
    
    for key in sorted(input_dict.keys()):  # ✅ 外层按顺序
        nested = input_dict[key]
        if not nested:
            continue
        for _, block in sorted(nested.items(), key=lambda x: int(str(x[0]))):
            new_dict[new_id] = {"pos1": block, "_orig_key": key}
            new_id += 1
    
    # 检查结果顺序
    print(f"输入大切片顺序: {sorted(input_dict.keys())}")
    print(f"输出block数: {len(new_dict)}")
    
    # 验证大切片顺序是否保留
    orig_keys = [v.get("_orig_key") for v in new_dict.values()]
    print(f"输出中大切片顺序: {orig_keys}")
    
    is_correct = orig_keys == [0, 0, 1, 2, 2]  # 应该是有序的
    print(f"{'✅ 通过' if is_correct else '❌ 失败'}")
    print()

def test_reorder_blocks():
    """测试 reorder_blocks 的顺序保留"""
    print("【测试3】reorder_blocks 顺序保留")
    print("-" * 50)
    
    # 模拟输入（key乱序）
    input_dict = {
        2: {0: {"title": "node2"}},
        0: {0: {"title": "node0"}},
        1: {0: {"title": "node1"}}
    }
    
    # 执行逻辑
    new_dict = {}
    new_id = 0
    
    for key in sorted(input_dict.keys()):  # ✅ 按顺序遍历
        nested = input_dict[key]
        block = nested[0]
        block['_reorder_id'] = key
        new_dict[new_id] = block
        new_id += 1
    
    # 检查结果
    reorder_ids = [v.get('_reorder_id') for v in new_dict.values()]
    print(f"输入key顺序: {sorted(input_dict.keys())}")
    print(f"输出_reorder_id: {reorder_ids}")
    
    is_correct = reorder_ids == [0, 1, 2]
    print(f"{'✅ 通过' if is_correct else '❌ 失败'}")
    print()

def test_full_pipeline():
    """集成测试：模拟完整流程"""
    print("【测试4】完整流程（模拟）")
    print("-" * 50)
    
    # step1: 模拟多线程返回（乱序）
    text_dict = {0: "text0", 1: "text1", 2: "text2"}
    
    # multitask_perform 返回（乱序）
    results = {2: "corrected_2", 0: "corrected_0", 1: "corrected_1"}
    ordered_results = {k: results[k] for k in text_dict.keys() if k in results}
    
    print("Step1 - multitask_perform")
    print(f"  输入: {list(text_dict.keys())}")
    print(f"  返回: {list(ordered_results.keys())}")
    print(f"  ✅ 顺序保留" if list(ordered_results.keys()) == list(text_dict.keys()) else "  ❌ 顺序错误")
    
    # step2: 模拟 extract_nonempty_blocks
    statement_dict = {
        2: {1: {"title": "th2"}},
        0: {0: {"title": "th0"}},
        1: {0: {"title": "th1"}}
    }
    
    nonempty = {}
    nid = 0
    for key in sorted(statement_dict.keys()):
        nested = statement_dict[key]
        for _, block in sorted(nested.items(), key=lambda x: int(str(x[0]))):
            nonempty[nid] = {"pos1": block, "_orig_key": key}
            nid += 1
    
    print("\nStep2 - extract_nonempty_blocks")
    print(f"  输出数: {len(nonempty)}")
    print(f"  原始大块顺序: {[v['_orig_key'] for v in nonempty.values()]}")
    print(f"  ✅ 大块顺序保留" if all(nonempty[i]['_orig_key'] <= nonempty[i+1]['_orig_key'] 
                                    for i in range(len(nonempty)-1)) else "  ❌ 顺序错误")
    
    # step3: 模拟 reorder_blocks
    final = {}
    fid = 0
    for key in sorted(nonempty.keys()):
        block = nonempty[key]["pos1"]
        block['_reorder_id'] = key
        final[fid] = block
        fid += 1
    
    print("\nStep3 - reorder_blocks")
    print(f"  输出数: {len(final)}")
    reorder_ids = [v['_reorder_id'] for v in final.values()]
    print(f"  _reorder_id: {reorder_ids}")
    print(f"  ✅ ID递增" if all(reorder_ids[i] <= reorder_ids[i+1] for i in range(len(reorder_ids)-1)) else "  ❌ ID混乱")
    
    print()

if __name__ == "__main__":
    print("\n" + "="*60)
    print("C-1方案顺序保留测试（无需API调用）")
    print("="*60 + "\n")
    
    try:
        test_multitask_perform_order()
        test_extract_nonempty_blocks()
        test_reorder_blocks()
        test_full_pipeline()
        
        print("="*60)
        print("✅ 所有测试完成")
        print("="*60)
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
