from pathlib import Path
import re
from typing import Dict, Any
try:
    from .bpath_check import check_path, filter_files
except ImportError:
    from HandleTool.bpath_check import check_path, filter_files

def _to_bool(val, default=False):
    """把 LLM 可能传的字符串/布尔值统一转成 bool"""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ('true', '1', 'yes', '是')
    return default

def run(params: Dict[str, Any]) -> str:
    """
    在文件或目录中搜索关键词（支持正则），返回匹配行及上下文。

    参数:
        params: 字典，包含:
            - path (str): 文件或目录路径（必需）
            - keyword (str): 搜索关键词/正则表达式（必需）
            - encoding_method (str): 文件编码，默认 'utf-8'
            - case_sensitive (bool): 是否区分大小写，默认 False
            - use_regex (bool): 是否启用正则匹配，默认 False
            - context (int): 上下文行数（前后各N行），默认 2
            - recursive (bool): path 为目录时是否递归，默认 True
            - file_pattern (str): 目录搜索时按文件名正则过滤，默认 '.*\\.(py|md|txt|json|js|ts|html|css|yaml|yml|toml|ini|cfg)$'
            - max_matches (int): 最大返回匹配数，默认 30

    返回:
        str: 状态和搜索结果。
    """
    try:
        # 1. 参数校验
        path = params.get("path")
        keyword = params.get("keyword")
        if not path:
            return "状态:Error, 原因:缺少 path 参数"
        if not keyword:
            return "状态:Error, 原因:缺少 keyword 参数"

        p = Path(path)
        if not p.exists():
            return f"状态:Error, 原因:路径不存在: {p.resolve()}"

        # 单文件：直接检查黑名单
        if p.is_file():
            block_msg = check_path(p, "grep")
            if block_msg:
                return block_msg

        encoding = params.get("encoding_method", "utf-8")
        case_sensitive = _to_bool(params.get("case_sensitive"), False)
        use_regex = _to_bool(params.get("use_regex"), False)
        recursive = _to_bool(params.get("recursive"), True)
        try:
            context = int(params.get("context", 2))
        except (TypeError, ValueError):
            context = 2
        try:
            max_matches = int(params.get("max_matches", 30))
        except (TypeError, ValueError):
            max_matches = 30
        file_pattern = params.get("file_pattern", r'.*\.(py|md|txt|json|js|ts|html|css|yaml|yml|toml|ini|cfg)$')

        # 2. 编译正则
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            if use_regex:
                pattern = re.compile(keyword, flags)
            else:
                pattern = re.compile(re.escape(keyword), flags)
            file_re = re.compile(file_pattern, re.IGNORECASE)
        except re.error as e:
            return f"状态:Error, 原因:正则表达式错误: {e}"

        # 3. 收集待搜索的文件列表 + 过滤黑名单
        if p.is_file():
            files = [p]
            blocked_notices = []
        elif p.is_dir():
            if recursive:
                raw_files = [f for f in p.rglob('*') if f.is_file() and file_re.search(f.name)]
            else:
                raw_files = [f for f in p.glob('*') if f.is_file() and file_re.search(f.name)]
            files, blocked = filter_files(raw_files, "grep")
            blocked_notices = [f"   [已跳过·黑名单] {b}" for b in blocked]
        else:
            return f"状态:Error, 原因:路径类型未知: {p.resolve()}"

        if not files:
            return f"状态:200, 无可搜索文件（候选均命中 .bpath 黑名单）"

        # 4. 逐文件搜索
        blocks = []
        total_matches = 0
        truncated = False

        for fp in files:
            if total_matches >= max_matches:
                truncated = True
                break
            try:
                content = fp.read_text(encoding=encoding)
            except (UnicodeDecodeError, PermissionError):
                # 二进制文件或无权限，跳过
                continue

            lines = content.splitlines()
            total_lines = len(lines)

            # 找当前文件的所有匹配行
            match_indices = [i for i, line in enumerate(lines) if pattern.search(line)]

            if not match_indices:
                continue

            blocks.append(f"=== {fp.resolve()} ===")

            seen_ranges = set()
            for match_idx in match_indices:
                if total_matches >= max_matches:
                    truncated = True
                    break

                # 1-based 行号
                match_line_no = match_idx + 1
                start = max(1, match_line_no - context)
                end = min(total_lines, match_line_no + context)

                if (start, end) in seen_ranges:
                    # 同一片段已输出，只计数不重复输出
                    total_matches += 1
                    continue
                seen_ranges.add((start, end))

                # 提取上下文
                context_lines = lines[start-1:end]
                block_content = []
                for i, line in enumerate(context_lines, start=start):
                    prefix = ">>> " if i == match_line_no else "    "
                    block_content.append(f"{prefix}{i:4d}: {line}")
                blocks.append("\n".join(block_content))
                total_matches += 1

        # 5. 拼接结果
        footer = "\n".join(blocked_notices) if blocked_notices else ""
        if not blocks:
            msg = f"状态:200, 搜索结果:\n未找到关键词 '{keyword}'"
            if footer:
                msg += f"\n\n【目录搜索中已跳过 {len(blocked_notices)} 个黑名单文件】\n{footer}"
            return msg

        result_text = "\n".join(blocks)
        header = f"状态:200, 匹配数:{total_matches}"
        if truncated:
            header += f"(已达上限{max_matches}，可能还有更多)"
        output = f"{header}\n{result_text}"
        if footer:
            output += f"\n\n【目录搜索中已跳过 {len(blocked_notices)} 个黑名单文件】\n{footer}"
        return output

    except Exception as e:
        return f"状态:Error, 原因:未知错误: {e}"
