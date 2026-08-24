import json
from typing import Union, List, Optional, Any
import importlib.util
import sys
import os
# 当前脚本的绝对路径
ScriptDir = os.path.dirname(os.path.abspath(__file__))
ParentDir = os.path.dirname(ScriptDir)          # 上一级目录
FilePath = os.path.join(ParentDir, 'tool_list.json')
print(FilePath)
class ParseTool:
    def __init__(self, tools_config_path=FilePath):
        # 加载工具配置（包含每个工具的 Accessible 信息）
        with open(tools_config_path, 'r', encoding='utf-8') as f:
            self.ToolsConfig = json.load(f)   # 假设是列表或字典，建议转为以 name 为键的字典
        # 为了方便查找，构建 name -> config 映射
        self.ToolConfigMap = {tool["name"]: tool for tool in self.ToolsConfig}
    def ExtractJsonFromText(self,text: str) -> Optional[Union[dict, list]]:
        """
        从混杂文本中提取第一个完整的 JSON 对象或数组。

        Args:
            text: 包含 JSON 的原始字符串

        Returns:
            解析后的 Python 对象（dict 或 list），若未找到则返回 None
        """
        start_pos = self._FindJsonStart(text)
        if start_pos == -1:
            return None

        end_pos = self._FindMatchingBracket(text, start_pos)
        if end_pos == -1:
            return None

        json_str = text[start_pos:end_pos + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    def ExtractAllJsonFromText(self,text: str) -> List[Any]:
        """
        从混杂文本中提取所有完整的 JSON 对象或数组。

        Returns:
            包含所有成功解析的 JSON 对象的列表
        """
        results = []
        idx = 0
        while idx < len(text):
            start = self._FindJsonStart(text, idx)
            if start == -1:
                break
            end = self._FindMatchingBracket(text, start)
            if end == -1:
                break
            json_str = text[start:end + 1]
            try:
                obj = json.loads(json_str)
                results.append(obj)
            except json.JSONDecodeError:
                pass
            idx = end + 1
        return results

    # ---------- 内部辅助函数（小驼峰，不暴露） ----------

    def _FindJsonStart(self,text: str, offset: int = 0) -> int:
        """查找第一个 '{' 或 '[' 的位置"""
        for i in range(offset, len(text)):
            ch = text[i]
            if ch == '{' or ch == '[':
                return i
        return -1

    def _FindMatchingBracket(self, text: str, start: int) -> int:
        opening = text[start]
        if opening not in ('{', '['):
            return -1
        closing = '}' if opening == '{' else ']'
        depth = 0
        in_string = False
        escaped = False  # 当前字符是否被转义
        for i in range(start, len(text)):
            ch = text[i]
            # 如果当前字符被转义，则它不改变任何状态
            if not escaped:
                if ch == '"' and not in_string:
                    # 遇到未转义的双引号，切换字符串状态
                    in_string = True
                elif ch == '"' and in_string:
                    # 遇到未转义的双引号，结束字符串
                    in_string = False
                elif not in_string:
                    # 只在非字符串内计数括号
                    if ch == opening:
                        depth += 1
                    elif ch == closing:
                        depth -= 1
                        if depth == 0:
                            return i
            # 更新转义状态：当前字符是反斜杠且未转义，则下一个字符被转义
            escaped = (ch == '\\' and not escaped)
        return -1
    def ToolRouting(self,ToolName,Parameters):
        with open("tool_list.json","r",encoding="utf-8") as f:
            ToolList = f.read()
        ToolList = json.loads(ToolList)
        for tool in ToolList:
            if tool.get("name") == ToolName:
                file_path = tool.get("ToolPath")
                if not file_path:
                    print(f"工具 {ToolName} 没有配置 ToolPath")
                    return f"状态:Error, 原因:工具 {ToolName} 没有配置 ToolPath"

                module_name = ToolName
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                if spec is None:
                    print(f"无法从 {file_path} 加载模块")
                    return f"状态:Error, 原因:无法从 {file_path} 加载模块"

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                if hasattr(module, "run"):
                    return module.run(Parameters)
                else:
                    print(f"模块 {ToolName} 加载成功，但没有找到 run 函数")
                    return f"状态:Error, 原因:模块 {ToolName} 没有 run 函数"

        print(f"未找到名为 '{ToolName}' 的工具")
        return f"状态:Error, 原因:未找到名为 '{ToolName}' 的工具"

    def ParseAllLLMOutput(self, text: str, source: str):
        """解析并执行 LLM 输出中的所有工具，并根据 source 校验权限"""
        llm_jsons = self.ExtractAllJsonFromText(text)
        if not llm_jsons:
            print("没提取到任何 JSON")
            return

        # 展平 JSON
        flat_jsons = []
        for item in llm_jsons:
            if isinstance(item, list):
                flat_jsons.extend(x for x in item if isinstance(x, dict))
            elif isinstance(item, dict):
                flat_jsons.append(item)

        result = []
        for idx, llm_json in enumerate(flat_jsons):
            tool_name = llm_json.get("name")
            params = llm_json.get("parameters", {})
            if not tool_name:
                print(f"第 {idx+1} 个 JSON 缺少 'name' 字段，跳过: {llm_json}")
                continue

            # ========== 权限校验 ==========
            tool_config = self.ToolConfigMap.get(tool_name)
            if not tool_config:
                error_msg = f"工具 '{tool_name}' 未在配置中定义，无法使用"
                print(error_msg)
                result.append({"name": tool_name, "output": error_msg})
                continue

            accessible_list = tool_config.get("Accessible", [])
            if source not in accessible_list:
                error_msg = f"工具 '{tool_name}' 不在您的使用列表里（当前来源：{source}）"
                print(error_msg)
                result.append({"name": tool_name, "output": error_msg})
                continue
            # ================================

            print(f"--- 执行第 {idx+1} 个工具: {tool_name} ---")
            tool_output = self.ToolRouting(tool_name, params)
            result.append({"name": tool_name, "output": tool_output})

        return result
    def GetToolNameByIndex(self, text: str, idx: int) -> str:
        """
        根据序号获取工具名，输入1代表第一个，2代表第二个
        超出实际数量返回空字符串""
        """
        llm_jsons = self.ExtractAllJsonFromText(text)
        flat_jsons = []
        for item in llm_jsons:
            if isinstance(item, list):
                flat_jsons.extend(x for x in item if isinstance(x, dict))
            elif isinstance(item, dict):
                flat_jsons.append(item)

        target_pos = idx - 1
        if target_pos < 0 or target_pos >= len(flat_jsons):
            return ""
        tn = flat_jsons[target_pos].get("name", "")
        return tn if isinstance(tn, str) else ""


    def CountToolNumber(self, text: str) -> int:
        """统计解析出来的工具实际数量"""
        llm_jsons = self.ExtractAllJsonFromText(text)
        flat_jsons = []
        for item in llm_jsons:
            if isinstance(item, list):
                flat_jsons.extend(x for x in item if isinstance(x, dict))
            elif isinstance(item, dict):
                flat_jsons.append(item)
        return len(flat_jsons)