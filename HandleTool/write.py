from pathlib import Path
try:
    from .bpath_check import check_path
except ImportError:
    # 被 ToolRouting 动态加载（spec_from_file_location）时无 package 上下文，回退绝对导入
    from HandleTool.bpath_check import check_path

def run(params):
    try:
        # 1. 必填参数校验
        path = params.get("path") or params.get("filename")
        if not path:
            return "状态:Error, 原因:缺少参数{path}"

        p = Path(path)
        block_msg = check_path(p, "write")
        if block_msg:
            return block_msg

        content = params.get("content", "")
        encoding = params.get("encoding_method", "utf-8")

        # 2. 检查路径是否已存在
        if p.exists():
            if p.is_dir():
                return "状态:Error, 原因:路径是一个目录，无法写入文件"
            # 是文件则直接覆盖（无需额外操作）
        else:
            # 3. 路径不存在：确保父目录存在（递归创建）
            p.parent.mkdir(parents=True, exist_ok=True)
            # 无需 touch()，后续写入会自动创建文件

        # 4. 写入内容（覆盖模式）
        # 使用 pathlib 的 write_text 更简洁（内部处理编码）
        BytesWritten = p.write_text(content, encoding=encoding)
        
        # 注意：write_text 返回写入的字符数（不是字节数），
        # 如果你确实需要字节数，可以先用 encode 计算长度。
        # 这里保持和原逻辑一致，返回“写入字符数”
        return f"状态:200, 写入字符数:{BytesWritten},写入位置{p.resolve()}"

    except Exception as e:
        return f"状态:Error, 原因:{e}"