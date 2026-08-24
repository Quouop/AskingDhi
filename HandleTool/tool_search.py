"""
工具搜索：让 LLM 按需查询可用工具的用法。
工具描述统一存放在 tool_list.json，本模块只负责读取和匹配。
"""
import json
from pathlib import Path

TOOL_LIST_FILE = Path(__file__).parent.parent / "tool_list.json"


def _load_tool_docs():
    """读取 tool_list.json，返回 {name: doc_dict} 映射。
    doc_dict 包含 description/use_case/keywords/params_example/note。
    """
    try:
        if not TOOL_LIST_FILE.exists():
            return {}
        with open(TOOL_LIST_FILE, "r", encoding="utf-8") as f:
            tools = json.load(f)
        if not isinstance(tools, list):
            return {}
        docs = {}
        for t in tools:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            if not name:
                continue
            docs[name] = {
                "description": t.get("description", "（无描述）"),
                "use_case": t.get("use_case", ""),
                "keywords": t.get("keywords", []),
                "params_example": t.get("params_example", ""),
                "note": t.get("note", ""),
                "is_builtin": t.get("is_builtin", False),
                "Accessible": t.get("Accessible", []),
            }
        return docs
    except Exception:
        return {}


def _load_registered_tools():
    """读取 tool_list.json，获取所有已注册工具名"""
    docs = _load_tool_docs()
    return list(docs.keys())


def _split_query(query):
    """拆词：中文做 2-gram 滑动窗口（避免长中文短语无法匹配短关键词），英文按单词"""
    import re
    query = query.lower()
    words = set()
    # 英文单词（3字符以上）
    for m in re.findall(r'[a-z]{3,}', query):
        words.add(m)
    # 中文 2-gram：把连续中文切成所有相邻2字组合
    for seg in re.findall(r'[\u4e00-\u9fa5]+', query):
        for i in range(len(seg) - 1):
            words.add(seg[i:i+2])
        if len(seg) == 1:  # 单字也收
            words.add(seg)
    return words


def _match_tools(query, docs, max_results=3):
    """关键词匹配：query 2-gram 拆词，与工具的 keywords/description 匹配"""
    query_words = _split_query(query)
    if not query_words:
        query_words = {query.lower()}

    scored = []
    for tool_name, doc in docs.items():
        keywords = set(k.lower() for k in doc.get("keywords", []))
        kw_score = len(query_words & keywords)

        desc_text = (doc.get("description", "") + doc.get("use_case", "")).lower()
        desc_score = sum(1 for w in query_words if w in desc_text)

        total_score = kw_score * 2 + desc_score
        scored.append((total_score, tool_name, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:max_results]


def run(params):
    try:
        query = params.get("query") or params.get("keyword") or params.get("q")
        source = params.get("source") or params.get("from") or params.get("caller")
        docs = _load_tool_docs()

        # 按"来源"过滤：只保留 Accessible 包含 source 的工具
        if source:
            docs = {name: doc for name, doc in docs.items()
                    if source in doc.get("Accessible", [])}
        else:
            # 未指定来源：默认只暴露内置工具（避免泄露不可用工具）
            docs = {name: doc for name, doc in docs.items()
                    if doc.get("is_builtin", False)}

        if not query:
            # 没传 query，返回所有（已过滤的）工具列表
            tool_list = [f"- {name}: {doc.get('description', '（无描述）')}" for name, doc in docs.items()]
            src_tag = f"[来源:{source}] " if source else "[来源:未指定,仅内置] "
            return f"状态:200, {src_tag}共 {len(tool_list)} 个工具可用:\n" + "\n".join(tool_list)

        max_results = int(params.get("limit", 3))
        matched = _match_tools(query, docs, max_results)

        if not matched or matched[0][0] == 0:
            all_tools = [f"- {name}: {doc.get('description', '（无描述）')}" for name, doc in docs.items()]
            return f"状态:200, 未找到匹配工具，当前可用工具:\n" + "\n".join(all_tools)

        result_parts = [f"状态:200, 找到 {len(matched)} 个相关工具:"]
        for score, name, doc in matched:
            if score == 0:
                continue
            builtin_tag = " [内置]" if doc.get("is_builtin") else " [非内置]"
            result_parts.append(f"\n{'='*40}")
            result_parts.append(f"工具名: {name}{builtin_tag}")
            result_parts.append(f"描述: {doc.get('description', '')}")
            result_parts.append(f"适用场景: {doc.get('use_case', '')}")
            result_parts.append(f"参数示例: {doc.get('params_example', '')}")
            if doc.get("note"):
                result_parts.append(f"注意: {doc.get('note')}")

        return "\n".join(result_parts)
    except Exception as e:
        return f"状态:Error, 原因:{e}"
