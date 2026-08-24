from pathlib import Path
import re
from typing import List, Union
try:
    from .bpath_check import check_path, filter_files
except ImportError:
    from HandleTool.bpath_check import check_path, filter_files

def run(params: dict) -> str:
    """
    在指定目录下搜索文件路径（相对路径），匹配给定的关键词（支持正则表达式）。

    参数 params (dict):
        - search_path (str): 搜索根目录（必需）
        - keywords (str | List[str]): 一个或多个关键词/正则表达式（必需）
        - recursive (bool): 是否递归子目录，默认 True
        - case_sensitive (bool): 正则是否区分大小写，默认 False

    返回:
        str: 状态信息及匹配文件的绝对路径列表，每行一个路径。
    """
    try:
        # ---------- 1. 参数校验 ----------
        search_path = params.get("search_path")
        keywords = params.get("keywords")
        if not search_path:
            return "状态:Error, 原因:缺少 search_path 参数"
        if not keywords:
            return "状态:Error, 原因:缺少 keywords 参数"

        recursive = params.get("recursive", True)
        case_sensitive = params.get("case_sensitive", False)

        root = Path(search_path)
        if not root.exists():
            return f"状态:Error, 原因:搜索路径不存在: {root.resolve()}"
        if not root.is_dir():
            return f"状态:Error, 原因:搜索路径不是目录: {root.resolve()}"

        # ---------- 2. 处理关键词列表 ----------
        if isinstance(keywords, str):
            # 单个关键词，直接放入列表
            keyword_list = [keywords]
        elif isinstance(keywords, list):
            keyword_list = keywords
        else:
            return "状态:Error, 原因:keywords 参数类型错误，应为字符串或字符串列表"

        # 编译正则表达式（忽略大小写根据参数）
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            patterns = [re.compile(kw, flags) for kw in keyword_list]
        except re.error as e:
            return f"状态:Error, 原因:正则表达式语法错误: {e}"

        # ---------- 3. 遍历目录 + 过滤黑名单 ----------
        if recursive:
            iterator = root.rglob('*')   # 递归所有文件和目录
        else:
            iterator = root.glob('*')    # 仅当前目录

        raw_candidates = [item for item in iterator if item.is_file()]
        candidates, blocked = filter_files(raw_candidates, "glob")

        matches = []
        for item in candidates:
            # 计算相对于搜索根目录的相对路径（使用 / 作为分隔符，统一风格）
            rel_path = item.relative_to(root)
            rel_str = rel_path.as_posix()

            # 检查任一正则是否匹配（search 为部分匹配）
            for pat in patterns:
                if pat.search(rel_str):
                    matches.append(str(item.resolve()))
                    break   # 匹配成功则跳出，避免重复添加

        # ---------- 4. 返回结果 ----------
        blocked_suffix = ""
        if blocked:
            notices = "\n".join(f"   [已跳过·黑名单] {b}" for b in blocked)
            blocked_suffix = f"\n\n【已跳过 {len(blocked)} 个黑名单文件】\n{notices}"

        if not matches:
            return f"状态:200, 匹配文件:\n无匹配文件{blocked_suffix}"
        return f"状态:200, 匹配文件:\n" + "\n".join(matches) + blocked_suffix

    except PermissionError as e:
        return f"状态:Error, 原因:权限错误: {e}"
    except Exception as e:
        return f"状态:Error, 原因:未知错误: {e}"