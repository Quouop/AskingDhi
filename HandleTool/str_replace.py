from pathlib import Path
try:
    from .bpath_check import check_path
except ImportError:
    from HandleTool.bpath_check import check_path

def run(params):
    try:
        path = params.get("path") or params.get("filename")
        if not path:
            return "状态:Error, 原因:缺少参数{path}"

        behavior = params.get("behavior")
        if not behavior:
            return "状态:Error, 原因:缺少参数{behavior}"

        p = Path(path)
        block_msg = check_path(p, "str_replace")
        if block_msg:
            return block_msg

        encoding = params.get("encoding_method", "utf-8")

        # 文件校验
        if not p.exists():
            return f"状态:Error, 原因:文件不存在: {p.resolve()}"
        if p.is_dir():
            return "状态:Error, 原因:路径是一个目录，无法执行str_replace"

        file_text = p.read_text(encoding=encoding)

        old_snippet = params.get("old_snippet", "")
        new_snippet = params.get("new_snippet", "")

        # ----------------行为分支----------------
        if behavior == "edit":
            # 替换片段 old_snippet → new_snippet
            if not old_snippet:
                return "状态:Error, 原因:behavior=edit 需要 old_snippet 参数"
            if new_snippet is None:
                return "状态:Error, 原因:behavior=edit 需要 new_snippet 参数"

            match_cnt = file_text.count(old_snippet)
            if match_cnt == 0:
                return "状态:Error, 原因:old_snippet 在文件中无匹配，请使用grep获取真实片段"
            if match_cnt > 1:
                return f"状态:Error, 原因:匹配到{match_cnt}处，old_snippet上下文不足，请增加上下文保证唯一"

            output_text = file_text.replace(old_snippet, new_snippet, 1)

        elif behavior == "insert":
            # 在 old_snippet(锚点) 的后面插入 new_snippet
            if not old_snippet:
                return "状态:Error, 原因:behavior=insert 需要 old_snippet(锚点片段)参数"
            if not new_snippet:
                return "状态:Error, 原因:behavior=insert 需要 new_snippet 参数"

            match_cnt = file_text.count(old_snippet)
            if match_cnt == 0:
                return "状态:Error, 原因:锚点old_snippet 在文件中无匹配，请使用grep获取真实片段"
            if match_cnt > 1:
                return f"状态:Error, 原因:锚点匹配到{match_cnt}处，请增加上下文保证唯一"

            output_text = file_text.replace(old_snippet, old_snippet + "\n" + new_snippet, 1)

        elif behavior == "delete":
            # 删除 old_snippet 整块内容
            if not old_snippet:
                return "状态:Error, 原因:behavior=delete 需要 old_snippet 参数"

            match_cnt = file_text.count(old_snippet)
            if match_cnt == 0:
                return "状态:Error, 原因:old_snippet 在文件中无匹配，请使用grep获取真实片段"
            if match_cnt > 1:
                return f"状态:Error, 原因:匹配到{match_cnt}处，请增加上下文保证唯一"

            output_text = file_text.replace(old_snippet, "", 1)

        else:
            return f"状态:Error, 原因:未知behavior={behavior}，支持 edit / insert / delete"

        # 回写文件
        write_chars = p.write_text(output_text, encoding=encoding)
        return f"状态:200, 修改字符数:{write_chars}, 修改位置:{p.resolve()}"

    except Exception as e:
        return f"状态:Error, 原因:{e}"