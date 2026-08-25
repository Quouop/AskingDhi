from typing import Dict, Any, List
from tavily import AsyncTavilyClient
import asyncio
import os
import tomllib
from clearhtml import 
# 加载配置（与 web_search.py 同源）
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


async def _extract_pages(urls: List[str], api_key: str = None) -> List[Dict[str, Any]]:
    """调用 Tavily extract 抓取多个 URL 的正文"""
    if not urls:
        return []

    # Key 优先级：传入值 > 配置文件 > 环境变量
    if not api_key:
        api_key = config.get("tavilyApiKey") or os.environ.get("TAVILY_API_KEY")

    try:
        client = AsyncTavilyClient(api_key=api_key) if api_key else AsyncTavilyClient()
    except Exception as e:
        raise RuntimeError(f"Tavily 客户端初始化失败（未配置 api_key？）: {e}")

    response = await client.extract(urls=urls)
    return response.get("results", [])


def run(params: Dict[str, Any]) -> str:
    """
    网页爬取：抓取指定 URL 列表的正文内容（基于 Tavily extract）。

    参数：
        params: {
            "urls": str | list[str]   (必需，要爬取的网址)
            "max_chars": int          (可选，每条结果截断长度，默认 2000，范围 200-10000)
        }

    返回：
        str: 状态信息和爬取的正文内容。
    """
    try:
        # 1. 解析 urls
        urls_raw = params.get("urls") or params.get("url")
        if urls_raw is None:
            return "状态:Error, 原因:缺少 urls 参数（必需，str 或 list）"
        try:
            urls = _normalize_str_list(urls_raw, "urls")
        except TypeError as e:
            return f"状态:Error, 原因:{e}"
        if not urls:
            return "状态:Error, 原因:urls 为空或全是空白"

        # 2. 解析 max_chars
        try:
            max_chars = int(params.get("max_chars", 2000))
        except (TypeError, ValueError):
            return "状态:Error, 原因:max_chars 必须是整数"
        max_chars = max(200, min(max_chars, 10000))

        # 3. 执行爬取（异步转同步）
        results = asyncio.run(_extract_pages(urls=urls))

        # 4. 格式化输出
        if not results:
            return "状态:200, 未抓取到任何内容（URL 可能无法访问或被反爬）"

        output_lines = [f"状态:200, 共抓取 {len(results)} 个页面", ""]
        for i, r in enumerate(results, 1):
            url = r.get("url", "")
            content = r.get("raw_content", "") or ""
            content_text = content.strip()
            content_text = 
            if len(content_text) > max_chars:
                content_text = content_text[:max_chars] + f"...[截断，共{len(content)}字符]"
            output_lines.append(f"{i}. {url}")
            output_lines.append(f"   内容: {content_text if content_text else '(空)'}")
            output_lines.append("")
        return "\n".join(output_lines)

    except Exception as e:
        return f"状态:Error, 原因:未知错误: {e}"
