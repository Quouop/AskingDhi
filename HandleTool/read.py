from pathlib import Path
try:
    from .bpath_check import check_path
except ImportError:
    from HandleTool.bpath_check import check_path

def run(params):
    try:
        path = params.get("path") or params.get("filename")
        if not path:
            return "状态:Error, 原因:缺少 path 参数"

        p = Path(path)
        block_msg = check_path(p, "read")
        if block_msg:
            return block_msg

        if not p.exists():
            return f"状态:Error, 原因:文件不存在: {p.resolve()}"
        if p.is_dir():
            return f"状态:Error, 原因:路径是目录: {p.resolve()}"

        encoding = params.get("encoding_method", "utf-8")
        lines = params.get("lines")
        content = p.read_text(encoding=encoding)

        # lines 为 None 或 <=0 时读全部，否则按行截断
        max_lines = int(lines) if lines else 0
        if max_lines > 0:
            all_lines = content.splitlines()
            if len(all_lines) > max_lines:
                content = "\n".join(all_lines[:max_lines]) + f"\n...[截断，共{len(all_lines)}行]"

        # 兜底截断：未指定行数时，超长内容截到 2000 字符
        if max_lines == 0 and len(content) > 2000:
            content = content[:2000] + f"\n...[截断，共{len(content)}字符]"

        return f"状态:200, 文件:{p.resolve()}, 内容:\n{content}"
    except Exception as e:
        return f"状态:Error, 原因:{e}"