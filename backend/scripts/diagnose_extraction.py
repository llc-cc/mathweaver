#!/usr/bin/env python3
"""
诊断 extract_statements 的 LLM 调用质量：区分「网络问题」与「模型输出格式问题」，并对比不同模型。

背景：extract_statements 对每个文本块独立、并发地调用 LLM；某块调用失败（网络异常）或
返回无法解析的内容（格式异常）时会被静默丢弃，导致节点数骤降且每次运行结果不同。
本脚本用 .env 里的同一个 key（不修改 key），只切换 --model，复现并量化该失败率。

用法：
  # 列出该 endpoint 上可用的模型
  python scripts/diagnose_extraction.py --list-models

  # 用当前默认模型探测 8 次（顺序）
  python scripts/diagnose_extraction.py --n 8

  # 对比多个模型，并用 8 路并发复现限流（贴近线上 32 线程）
  python scripts/diagnose_extraction.py --model qwen3-max,qwen-max,deepseek-v3,gpt-4o-mini --n 8 --concurrency 8
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

# 让脚本能 import 到 backend 包 + 内置 JoinAgent
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

import importlib.util  # noqa: E402

from dotenv import load_dotenv  # noqa: E402


def _load(name, relpath):
    """直接按文件路径加载模块，绕过 JoinAgent/__init__（其会 import 未安装的 dashscope）。"""
    spec = importlib.util.spec_from_file_location(name, os.path.join(BACKEND_DIR, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_llm = _load("ja_llm", "JoinAgent/LLM_API/llm.py")
_parser = _load("ja_parser", "JoinAgent/LLM_Parser/llm_parser.py")
SimpleLLM = _llm.SimpleLLM
_normalize_chat_completions_url = _llm._normalize_chat_completions_url
LLMParser = _parser.LLMParser

# 一个真实的数学文本块（取自 Evans Ch.5 Sobolev 章节），用于贴近真实提取任务
SAMPLE_BLOCK = (
    r"DEFINITION. Assume $u, v \in L^1_{loc}(U)$, and $\alpha$ is a multiindex. "
    r"We say that $v$ is the $\alpha^{th}$-weak partial derivative of $u$, written "
    r"$D^\alpha u = v$, provided $\int_U u D^\alpha \phi\,dx = (-1)^{|\alpha|}\int_U v\phi\,dx$ "
    r"for all test functions $\phi \in C_c^\infty(U)$. "
    r"THEOREM (Uniqueness of weak derivatives). A weak $\alpha^{th}$-partial derivative of $u$, "
    r"if it exists, is uniquely defined up to a set of measure zero. "
    r"DEFINITION (Hölder spaces). If $u: U \to \mathbb{R}$ is bounded and continuous, "
    r"we write $\|u\|_{C(\bar U)} = \sup_{x\in U}|u(x)|$."
)

PROMPT = (
    "你是数学命题抽取器。请从下面的文本中抽取所有独立的数学命题（定义/定理/引理等），"
    "并严格只输出一个 JSON 字典，键为从 0 开始的序号字符串，值为含 "
    '"node_type" 与 "content" 两个字段的对象。不要输出 JSON 以外的任何内容。\n\n文本：\n'
    + SAMPLE_BLOCK
)


def categorize(fn):
    """运行一次探测，返回 (类别, 详情, 耗时秒, 抽到的命题数)。"""
    parser = LLMParser()
    t0 = time.time()
    try:
        answer = fn(PROMPT)
    except requests.exceptions.Timeout as e:
        return ("①网络-超时", str(e)[:120], time.time() - t0, 0)
    except requests.exceptions.ConnectionError as e:
        return ("①网络-连接", str(e)[:120], time.time() - t0, 0)
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", "?")
        body = (e.response.text[:120] if e.response is not None else "")
        if status == 429 or "RateQuota" in body or "Throttling" in body:
            return ("①网络-限流429", f"{status} {body}", time.time() - t0, 0)
        return (f"①网络-HTTP{status}", body, time.time() - t0, 0)
    except Exception as e:  # noqa: BLE001
        return ("①网络-其他", f"{type(e).__name__}: {e}"[:120], time.time() - t0, 0)
    elapsed = time.time() - t0
    # 拿到响应了——检查是否能解析为字典（= 模型输出格式是否合格）
    try:
        parsed = parser.parse_dict(answer)
    except Exception as e:  # noqa: BLE001
        return ("②格式-无法解析", f"{type(e).__name__}: {str(e)[:80]} | 原文尾部={answer[-80:]!r}", elapsed, 0)
    if not isinstance(parsed, dict):
        return ("②格式-非字典", f"得到 {type(parsed).__name__}", elapsed, 0)
    return ("✅成功", "", elapsed, len(parsed))


def list_models(api_url, api_key):
    base = _normalize_chat_completions_url(api_url).rsplit("/chat/completions", 1)[0]
    url = base + "/models"
    print(f"GET {url}")
    r = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", r.json())
    ids = sorted(m.get("id", str(m)) if isinstance(m, dict) else str(m) for m in data)
    print(f"可用模型 {len(ids)} 个：")
    for i in ids:
        print(" ", i)


def probe_model(model, api_url, api_key, n, concurrency):
    llm = SimpleLLM(model=model, api_url=api_url, api_key=api_key)
    print(f"\n===== 模型 {model}（{n} 次，并发 {concurrency}）=====")
    results = []
    if concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            results = list(ex.map(lambda _: categorize(llm.ask), range(n)))
    else:
        for _ in range(n):
            results.append(categorize(llm.ask))

    cats = Counter(r[0] for r in results)
    ok = cats.get("✅成功", 0)
    latencies = [r[2] for r in results if r[0] == "✅成功"]
    nodes = [r[3] for r in results if r[0] == "✅成功"]
    print(f"成功率: {ok}/{n} = {ok / n * 100:.0f}%")
    if latencies:
        latencies.sort()
        print(f"成功调用延迟: 中位 {latencies[len(latencies)//2]:.1f}s  最大 {max(latencies):.1f}s")
        print(f"每次抽到命题数: {nodes}（应该稳定 >=3；忽多忽少=不稳定）")
    print("分类统计:")
    for cat, c in cats.most_common():
        print(f"  {cat}: {c}")
    # 打印每类一个样例详情，便于定位
    seen = set()
    for cat, detail, el, _ in results:
        if cat != "✅成功" and cat not in seen and detail:
            print(f"    例[{cat}] ({el:.1f}s): {detail}")
            seen.add(cat)
    return ok, n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="逗号分隔的模型列表；默认用 .env 的 MODEL_NAME")
    ap.add_argument("--n", type=int, default=8, help="每个模型探测次数")
    ap.add_argument("--concurrency", type=int, default=1, help="并发数（设大可复现限流，线上为 32）")
    ap.add_argument("--list-models", action="store_true", help="列出 endpoint 可用模型后退出")
    args = ap.parse_args()

    load_dotenv(os.path.join(BACKEND_DIR, "..", ".env"))
    load_dotenv(os.path.join(BACKEND_DIR, ".env"))
    api_url = os.getenv("API_URL") or os.getenv("PDFPIPELINE_API_URL") or os.getenv("LLM_API_URL")
    api_key = os.getenv("API_KEY") or os.getenv("PDFPIPELINE_API_KEY") or os.getenv("OPENAI_API_KEY")
    default_model = os.getenv("MODEL_NAME") or os.getenv("PDFPIPELINE_MODEL_NAME")
    if not api_url or not api_key:
        sys.exit("缺少 API_URL / API_KEY（请检查 .env）")
    print(f"Endpoint: {api_url}")
    print(f"Key: {api_key[:6]}****{api_key[-4:]}")

    if args.list_models:
        list_models(api_url, api_key)
        return

    models = [m.strip() for m in (args.model or default_model or "").split(",") if m.strip()]
    if not models:
        sys.exit("未指定模型，且 .env 无 MODEL_NAME")

    summary = []
    for m in models:
        try:
            ok, n = probe_model(m, api_url, api_key, args.n, args.concurrency)
            summary.append((m, ok, n))
        except Exception as e:  # noqa: BLE001
            print(f"  模型 {m} 探测异常: {e}")
            summary.append((m, 0, args.n))

    print("\n===== 汇总 =====")
    for m, ok, n in summary:
        print(f"  {m:28s} 成功率 {ok}/{n} = {ok / n * 100:.0f}%")
    print("\n判读：①网络类失败为主 → 换 endpoint/降并发；②格式类失败为主 → 换输出更规整的模型。")


if __name__ == "__main__":
    main()
