"""子智能体(subAgent)实现。

设计要点:
- 同步阻塞执行:在 TaskAgent 线程内直接跑,不开 daemon 线程(TaskAgent 会等待 subAgent 返回)
- 40 轮 React 上限:超时直接返回当前进度(不询问用户)
- 身份硬编码:source 强制 "subAgent",直接赋值覆盖(防越权)
- 权限校验:只允许 Accessible 含 "subAgent" 的工具(无 memory/todo/clock/taskagent/subagent 自身)
- 路径校验:继承父智能体(TaskAgent)的 allowed_path
- subagent_complete 拦截:由本模块拦截,提取 summary+progress+next_step+artifacts,返回给 TaskAgent
- task_complete 不可用:不在 subAgent 的 Accessible 里(那是 TaskAgent 的事)
- 用完即毁:无记忆、无调度权、不写任何持久化日志
"""
import os
import json
import importlib.util
from typing import Dict, Any, List, Optional


# ========== 路径配置 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))      # HandleTool/SAgentTool
PROJECT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))    # 项目根目录
TOOL_LIST_FILE = os.path.join(PROJECT_DIR, "tool_list.json")
PROMPT_FILE = os.path.join(SCRIPT_DIR, "Prompt.subagent.md")

# 文件类工具及其路径参数名(用于路径权限校验,与 TAgentTool 对齐)
_FILE_PATH_PARAMS = {
    "read": ["path"],
    "write": ["path"],
    "str_replace": ["path"],
    "grep": ["path"],
    "glob": ["search_path"],
}

# subAgent 身份(硬编码,绝不暴露给 LLM)
_SUBAGENT_SOURCE = "subAgent"


def LoadSysForSubAgent(FilePath: str, AgentName: str = _SUBAGENT_SOURCE) -> List[Dict[str, Any]]:
    """从工具配置文件中筛选 Accessible 包含 subAgent 且 is_builtin 为 True 的工具。"""
    Result = []
    try:
        with open(FilePath, 'r', encoding='utf-8') as f:
            ToolsData = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[subAgent] 读取配置文件失败: {e}")
        return Result

    if not isinstance(ToolsData, list):
        if isinstance(ToolsData, dict):
            ToolsData = [ToolsData]
        else:
            return Result

    for Tool in ToolsData:
        if not isinstance(Tool, dict):
            continue
        IsBuiltin = Tool.get("is_builtin")
        if isinstance(IsBuiltin, bool):
            BuiltinFlag = IsBuiltin
        elif isinstance(IsBuiltin, str):
            BuiltinFlag = IsBuiltin.lower() == "true"
        else:
            BuiltinFlag = False
        AccessibleList = Tool.get("Accessible")
        if not isinstance(AccessibleList, list):
            AccessibleList = []
        if BuiltinFlag and AgentName in AccessibleList:
            Result.append(Tool)
    return Result


def _build_tool_list_str():
    """加载 subAgent 可用的内置工具,格式化为 JSON 字符串"""
    tools = LoadSysForSubAgent(TOOL_LIST_FILE, _SUBAGENT_SOURCE)
    return json.dumps(tools, ensure_ascii=False, indent=4)


def _load_subagent_prompt():
    """加载 Prompt.subagent.md 并动态注入工具列表与项目路径"""
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return ""
    tool_list_str = _build_tool_list_str()
    content = content.replace("{PROJECT_PATH}", PROJECT_DIR)
    content = content.replace("{TOOL_LIST}", tool_list_str)
    return content


# ========== 路径权限校验(与 TAgentTool 对齐) ==========
def _check_path_access(tool_name: str, tool_params: dict, allowed_path: str) -> Optional[str]:
    """校验文件类工具的路径参数是否在 allowed_path 范围内。返回 None=放行;字符串=越权错误。"""
    if not allowed_path:
        return None
    param_names = _FILE_PATH_PARAMS.get(tool_name)
    if not param_names:
        return None

    allowed_abs = os.path.abspath(allowed_path).lower().rstrip(os.sep)
    for pn in param_names:
        val = tool_params.get(pn)
        if not val:
            continue
        val_abs = os.path.abspath(str(val)).lower().rstrip(os.sep)
        if val_abs != allowed_abs and not val_abs.startswith(allowed_abs + os.sep):
            return (
                f"状态:Error, 路径越权: 工具 '{tool_name}' 的 {pn}='{val}' "
                f"不在允许范围 '{allowed_path}' 内。你只能操作该目录内的文件。"
            )
    return None


# ========== LLM 调用(与 TAgentTool 对齐) ==========
def _call_llm_once(messages):
    """单次调用 LLM(非流式,关闭思考),返回纯文本回复"""
    try:
        from dashscope import MultiModalConversation
        responses = MultiModalConversation.call(
            model="qwen3.7-plus",
            messages=messages,
            result_format='message',
            stream=False,
            enable_thinking=False,
        )
        if responses.status_code != 200:
            return f"状态:Error, LLM 调用失败: {responses.code} - {responses.message}"
        answer = responses.output.choices[0].message.content
        if isinstance(answer, list):
            answer = ''.join(item.get('text', '') for item in answer if isinstance(item, dict))
        return answer or ""
    except Exception as e:
        return f"状态:Error, LLM 调用异常: {e}"


# ========== 子任务执行核心(同步阻塞,40 轮 React) ==========
def _execute_subtask(task: str, context: str, allowed_path: str, sys_prompt: str,
                     max_rounds: int = 40) -> Dict[str, Any]:
    """
    同步执行子任务(在 TaskAgent 线程内阻塞运行)。
    返回结构化结果 dict:
      - status: "completed" / "failed" / "timeout"
      - summary: 一句话完成描述
      - progress: 完成进度(如 "3/5")
      - next_step: 未完成时的下一步建议
      - artifacts: 产物文件路径列表
      - final_response: 最后回复(兜底)
    subagent_complete 由本函数拦截处理,不交给 ParsingTool。
    task_complete 不在 subAgent 的 Accessible 里,即使 LLM 调用也会被权限校验拒掉。
    """
    # 动态加载 ParsingTool(避免循环导入)
    spec = importlib.util.spec_from_file_location(
        "ParsingTool_sub",
        os.path.join(os.path.dirname(SCRIPT_DIR), "ParsingTool.py")
    )
    ParsingMod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ParsingMod)
    Parser = ParsingMod.ParseTool()
    ToolConfigMap = Parser.ToolConfigMap  # name -> config

    # 组装初始消息
    user_input = f"[子任务] {task}"
    if context:
        user_input += f"\n[父智能体上下文摘要] {context}"
    if allowed_path:
        user_input += f"\n[路径权限] 你只能在以下目录范围内操作文件: {allowed_path}。越权会被拦截。"
    user_input += "\n[完成要求] 完成后必须调用 subagent_complete 提交完成描述、进度和下一步建议。"
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_input},
    ]

    final_response = ""
    result_bundle = {
        "status": "failed",
        "summary": "",
        "progress": "",
        "next_step": "",
        "artifacts": [],
        "final_response": "",
    }

    for round_idx in range(1, max_rounds + 1):
        answer = _call_llm_once(messages)
        if answer.startswith("状态:Error"):
            result_bundle["summary"] = answer
            result_bundle["final_response"] = answer
            return result_bundle

        final_response = answer
        messages.append({"role": "assistant", "content": answer})

        # 解析所有工具调用
        valid_tools = [t for t in Parser.ExtractAllJsonFromText(answer)
                       if isinstance(t, dict) and t.get("name")]
        if not valid_tools:
            # 无工具调用,subAgent 选择用自然语言结束(未调用 subagent_complete)
            break

        # 分离 subagent_complete(由本函数拦截)和其他工具
        complete_tool = None
        other_tools = []
        for t in valid_tools:
            if t.get("name") == "subagent_complete":
                complete_tool = t
            else:
                other_tools.append(t)

        # ========== source 硬编码:强制 tool_search 的 source="subAgent"(防越权) ==========
        # 直接赋值覆盖,无论 LLM 传什么(修复点:不用 setdefault)
        for t in other_tools:
            if t.get("name") == "tool_search":
                if not isinstance(t.get("parameters"), dict):
                    t["parameters"] = {}
                t["parameters"]["source"] = _SUBAGENT_SOURCE

        # ========== 执行其他工具(权限校验 + 路径校验 + 执行) ==========
        result_list = []
        for t in other_tools:
            tool_name = t.get("name")
            tool_params = t.get("parameters", {}) or {}

            # 权限校验:检查是否在 subAgent 的 Accessible 里
            tool_config = ToolConfigMap.get(tool_name)
            if not tool_config:
                result_list.append({"name": tool_name,
                                    "output": f"状态:Error, 工具 '{tool_name}' 未注册"})
                continue
            if _SUBAGENT_SOURCE not in tool_config.get("Accessible", []):
                result_list.append({"name": tool_name,
                                    "output": f"状态:Error, 工具 '{tool_name}' 不在 subAgent 的使用列表里(越权拒绝)"})
                continue

            # 路径权限校验:文件类工具的路径必须在 allowed_path 范围内
            path_err = _check_path_access(tool_name, tool_params, allowed_path)
            if path_err:
                result_list.append({"name": tool_name, "output": path_err})
                continue

            # 执行工具
            try:
                output = Parser.ToolRouting(tool_name, tool_params)
            except Exception as e:
                output = f"状态:Error, 执行异常: {e}"
            result_list.append({"name": tool_name, "output": output})

        # 注入工具结果
        for tr in result_list:
            messages.append({
                "role": "user",
                "content": f"[工具 {tr['name']} 执行结果]\n{tr['output']}"
            })

        # ========== 拦截 subagent_complete:由本函数处理,不交给 ParsingTool ==========
        if complete_tool:
            params = complete_tool.get("parameters", {}) or {}
            summary = params.get("summary", "") or final_response[:200]
            progress = params.get("progress", "")
            next_step = params.get("next_step", "")
            artifacts = params.get("artifacts", []) or []
            result_bundle.update({
                "status": "completed",
                "summary": summary,
                "progress": progress,
                "next_step": next_step,
                "artifacts": artifacts,
                "final_response": final_response,
            })
            print(f"[subAgent] 子任务完成: {summary} (进度: {progress or '未指定'})")
            return result_bundle
    else:
        # 达到 40 轮上限仍未调用 subagent_complete → 返回超时进度(不询问)
        result_bundle.update({
            "status": "timeout",
            "summary": f"已达 40 轮 React 上限,未调用 subagent_complete。最后输出: {final_response[:300]}",
            "progress": "",
            "next_step": "建议父智能体基于以上输出决定是否派下一个 subAgent 接力",
            "artifacts": [],
            "final_response": final_response,
        })
        print(f"[subAgent] 子任务超时(40轮),返回当前进度")
        return result_bundle

    # 循环正常结束但未调用 subagent_complete → 标记失败,返回最后输出
    result_bundle.update({
        "status": "failed",
        "summary": f"子任务未调用 subagent_complete 正式结束。最后输出: {final_response[:300]}",
        "final_response": final_response,
    })
    return result_bundle


# ========== 入口函数 ==========
def run(params: Dict[str, Any]) -> str:
    """
    subAgent 入口函数(由 TaskAgent 通过 ToolRouting 调用,同步阻塞执行)。
    behavior:
        - run: 同步执行子任务,阻塞等待返回,返回结构化结果给 TaskAgent
    """
    try:
        # ParsingTool 传进来的 params 本身就是 LLM JSON 里的 parameters 字典
        # 不要再多一层 .get("parameters")，否则所有参数都会是 None
        if not isinstance(params, dict):
            params = {}
        behavior = params.get("behavior")

        if behavior == "run":
            task = params.get("task")
            context = params.get("context", "")
            path = params.get("path", "")  # 继承父智能体的 allowed_path(选填)
            if not task:
                return "状态:Error, 缺少 task 参数"

            # 加载系统提示词(动态注入工具列表)
            sys_prompt = _load_subagent_prompt()
            if not sys_prompt:
                return "状态:Error, 无法加载 subAgent 提示词"

            # 同步阻塞执行(不开线程,在 TaskAgent 线程内跑)
            result = _execute_subtask(task, context, path, sys_prompt, max_rounds=40)

            # 组装返回给 TaskAgent 的结构化字符串
            status = result.get("status", "failed")
            summary = result.get("summary", "")
            progress = result.get("progress", "")
            next_step = result.get("next_step", "")
            artifacts = result.get("artifacts", []) or []

            parts = [f"状态:{status}, subAgent 执行结果:"]
            parts.append(f"摘要: {summary}")
            if progress:
                parts.append(f"进度: {progress}")
            if next_step:
                parts.append(f"下一步建议: {next_step}")
            if artifacts:
                parts.append(f"产物: {', '.join(artifacts)}")
            return "\n".join(parts)

        else:
            return f"状态:Error, 未知 behavior: {behavior}(支持: run)"

    except Exception as e:
        return f"状态:Error, {e}"
