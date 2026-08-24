from typing import Dict, Any, List, Optional
from tavily import AsyncTavilyClient
import asyncio
import os
import tomllib

# 加载配置（假设 config.toml 中存在 tavilyApiKey）
with open("config.toml", "rb") as toml_file:
    config = tomllib.load(toml_file)
def _normalize_str_list(val, field_name):
    """将 str 或 list 统一转为 list[str]，并去除空字符串"""
    if val is None:
        return []
    if isinstance(val, str):
        s = val.strip()
        return [s] if s else []
    if isinstance(val, list):
        out = []
        for i, item in enumerate(val):
            if not isinstance(item, str):
                raise TypeError(f"{field_name} 列表第 {i} 个元素不是字符串: {type(item).__name__}")
            s = item.strip()
            if s:
                out.append(s)
        return out
    raise TypeError(f"{field_name} 必须是字符串或字符串列表，收到: {type(val).__name__}")

async def multi_query_search(
    queries: List[str],
    max_results: int = 5,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    if not queries:
        return []

    # 尝试获取 Key（环境变量 > 传入值）
    if not api_key:
        api_key = config["tavilyApiKey"]

    # 创建客户端（无 Key 时尝试无参构造，失败则提示）
    try:
        client = AsyncTavilyClient(api_key=api_key) if api_key else AsyncTavilyClient()
    except Exception as e:
        raise RuntimeError(f"Tavily 客户端初始化失败（未配置 api_key？）: {e}")

    # 无 Key 时强制使用 basic 深度
    search_depth = "advanced" if api_key else "basic"
    if not api_key and max_results > 5:
        # 无 Key 时可能限制更多，这里主动限制防止报错
        max_results = min(max_results, 5)

    tasks = [
        client.search(
            query=q,
            max_results=max_results,
            search_depth=search_depth,
            include_domains=include_domains,
            exclude_domains=exclude_domains
        )
        for q in queries
    ]

    responses = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_results = []
    for query, resp in zip(queries, responses):
        if isinstance(resp, Exception):
            print(f"查询失败: {query} — {resp}")
            continue
        # 为每条结果添加来源查询标记
        for r in resp.get("results", []):
            r["source_query"] = query
            all_results.append(r)
    
    # 按 URL 去重，保留相关性分数最高的那条
    url_map = {}
    for r in all_results:
        url = r.get("url")
        if not url:
            continue
        if url not in url_map or r.get("score", 0) > url_map[url].get("score", 0):
            url_map[url] = r
    
    # 按分数降序返回
    return sorted(url_map.values(), key=lambda x: x.get("score", 0), reverse=True)

def run(params: Dict[str, Any]) -> str:
    """
    联网搜索入口函数（同步包装器）

    参数：
        params: {
            "keywords": str | list[str]   (必需)
            "include_sites": list[str]    (可选)
            "exclude_sites": list[str]    (可选)
            "max_results": int            (可选, 5-20, 默认10)
        }

    返回：
        str: 状态信息和搜索结果摘要
    """
    try:
        # 1. 解析 keywords
        keywords_raw = params.get("keywords") or params.get("keyword") or params.get("query")
        if keywords_raw is None:
            return "状态:Error, 原因:缺少 keywords 参数（必需，str 或 list）"
        try:
            keywords = _normalize_str_list(keywords_raw, "keywords")
        except TypeError as e:
            return f"状态:Error, 原因:{e}"
        if not keywords:
            return "状态:Error, 原因:keywords 为空或全是空白"

        # 2. 解析 include_sites / exclude_sites
        try:
            include_sites = _normalize_str_list(params.get("include_sites"), "include_sites")
            exclude_sites = _normalize_str_list(params.get("exclude_sites"), "exclude_sites")
        except TypeError as e:
            return f"状态:Error, 原因:{e}"

        # 冲突检测
        if include_sites and exclude_sites:
            conflict = set(s.lower() for s in include_sites) & set(s.lower() for s in exclude_sites)
            if conflict:
                return f"状态:Error, 原因:以下站点同时出现在 include_sites 和 exclude_sites: {sorted(conflict)}"

        # 3. 解析 max_results
        try:
            max_results = int(params.get("max_results", 10))
        except (TypeError, ValueError):
            return "状态:Error, 原因:max_results 必须是整数"
        max_results = max(5, min(max_results, 20))

        # 4. 执行并发搜索（异步转同步）
        results = asyncio.run(
            multi_query_search(
                queries=keywords,
                max_results=max_results,
                include_domains=include_sites or None,
                exclude_domains=exclude_sites or None,
                api_key=config.get('tavilyApiKey')  # 从配置文件获取
            )
        )

        # 5. 格式化输出
        output_lines = [f"状态:200, 共获取 {len(results)} 条结果", ""]
        for i, r in enumerate(results[:max_results], 1):
            title = r.get("title", "无标题")
            url = r.get("url", "")
            score = r.get("score", 0)
            source = r.get("source_query", "")
            output_lines.append(f"{i}. {title} (相关度:{score:.2f})")
            output_lines.append(f"   链接: {url}")
            output_lines.append(f"   来源查询: {source}")
            output_lines.append("")
        return "\n".join(output_lines)

    except Exception as e:
        return f"状态:Error, 原因:未知错误: {e}"