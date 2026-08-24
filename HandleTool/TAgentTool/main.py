import os
import json
import time
import threading
import importlib.util
from typing import Dict, Any, List, Optional


# ========== 路径配置 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))      # HandleTool/TAgentTool
PROJECT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))   # 项目根目录
TOOL_LIST_FILE = os.path.join(PROJECT_DIR, "tool_list.json")
PROMPT_FILE = os.path.join(SCRIPT_DIR, "Prompt.taskagent.md")
TASK_LOG_FILE = os.path.join(SCRIPT_DIR, "taskagent_log.json")

# 文件类工具及其路径参数名(用于路径权限校验)
_FILE_PATH_PARAMS = {
    "read": ["path"],
    "write": ["path"],
    "str_replace": ["path"],
    "grep": ["path"],
    "glob": ["search_path"],
}


def LoadSysForTaskAgent(FilePath: str, AgentName: str = "TaskAgent") -> List[Dict[str, Any]]:
    """
    从工具配置文件中读取所有工具，筛选出 Accessible 包含指定 Agent 且 is_builtin 为 True 的工具。

    Args:
        FilePath: 工具配置 JSON 文件路径（假设文件内容是一个数组，每个元素包含工具信息）。
        AgentName: 要检查的代理名称，默认为 "TaskAgent"。

    Returns:
        符合条件的工具列表（每个工具为原始字典对象）。若文件不存在或格式错误，返回空列表。
    """
    Result = []
    try:
        with open(FilePath, 'r', encoding='utf-8') as f:
            ToolsData = json.load(f)  # 期望是列表
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"读取配置文件失败: {e}")
        return Result

    if not isinstance(ToolsData, list):
        if isinstance(ToolsData, dict):
            ToolsData = [ToolsData]
        else:
            print("配置文件格式错误：期望 JSON 数组或对象")
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


# ========== 提示词动态加载（同贾维斯 {TOOL_LIST} 替换机制）==========
def _build_tool_list_str():
    """加载 TaskAgent 可用的内置工具，格式化为 JSON 字符串"""
    tools = LoadSysForTaskAgent(TOOL_LIST_FILE, "TaskAgent")
    return json.dumps(tools, ensure_ascii=False, indent=4)


def _load_taskagent_prompt():
    """加载 Prompt.taskagent.md 并动态注入工具列表与项目路径"""
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return ""
    tool_list_str = _build_tool_list_str()
    content = content.replace("{PROJECT_PATH}", PROJECT_DIR)
    content = content.replace("{TOOL_LIST}", tool_list_str)
    return content


# ========== 任务记录管理（taskagent_log.json）==========
def _load_log() -> List[Dict[str, Any]]:
    """读取任务记录文件，返回任务列表"""
    if not os.path.exists(TASK_LOG_FILE):
        return []
    try:
        with open(TASK_LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_log(log: List[Dict[str, Any]]):
    """保存任务记录到文件"""
    try:
        with open(TASK_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[TaskAgent] 任务记录保存失败: {e}")


def _gen_task_id() -> str:
    """生成唯一任务 ID: task_YYYYMMDD_HHMMSS_xxx"""
    return f"task_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time()*1000)%1000:03d}"


def _create_task(task: str, message: str, path: str) -> str:
    """创建任务记录，状态初始为 waiting（占位，等待开始）"""
    log = _load_log()
    task_id = _gen_task_id()
    record = {
        "task_id": task_id,
        "status": "waiting",
        "task": task,
        "message": message or "",
        "path": path or "",
        "start_time": "",
        "end_time": "",
        "result": "",
        "artifacts": [],
        "notified": False
    }
    log.append(record)
    _save_log(log)
    return task_id


def _update_task(task_id: str, **fields):
    """更新指定任务的字段"""
    log = _load_log()
    for r in log:
        if r.get("task_id") == task_id:
            r.update(fields)
            break
    _save_log(log)


def _start_task(task_id: str):
    """标记任务为 running，记录开始时间"""
    _update_task(task_id, status="running", start_time=time.strftime("%Y-%m-%d %H:%M:%S"))


def _finish_task(task_id: str, summary: str, artifacts: list):
    """标记任务为 completed，记录结束时间和完成描述"""
    _update_task(
        task_id,
        status="completed",
        end_time=time.strftime("%Y-%m-%d %H:%M:%S"),
        result=summary,
        artifacts=artifacts or [],
    )


def _fail_task(task_id: str, reason: str):
    """标记任务为 failed，记录结束时间和失败原因"""
    _update_task(
        task_id,
        status="failed",
        end_time=time.strftime("%Y-%m-%d %H:%M:%S"),
        result=reason,
    )


# ========== 路径权限校验 ==========
def _check_path_access(tool_name: str, tool_params: dict, allowed_path: str) -> Optional[str]:
    """
    校验文件类工具的路径参数是否在 allowed_path 范围内。
    返回 None 表示放行；返回字符串表示越权错误信息。
    """
    if not allowed_path:
        return None  # 未限定路径，放行
    param_names = _FILE_PATH_PARAMS.get(tool_name)
    if not param_names:
        return None  # 非文件类工具，放行

    allowed_abs = os.path.abspath(allowed_path).lower().rstrip(os.sep)
    for pn in param_names:
        val = tool_params.get(pn)
        if not val:
            continue
        val_abs = os.path.abspath(str(val)).lower().rstrip(os.sep)
        # 必须是 allowed_path 本身或其子路径
        if val_abs != allowed_abs and not val_abs.startswith(allowed_abs + os.sep):
            return (
                f"状态:Error, 路径越权: 工具 '{tool_name}' 的 {pn}='{val}' "
                f"不在允许范围 '{allowed_path}' 内。你只能操作该目录内的文件。"
            )
    return None


# ========== LLM 调用 ==========
def _call_llm_once(messages):
    """单次调用 LLM（非流式，关闭思考），返回纯文本回复"""
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


# ========== 任务执行核心（多轮工具调用循环）==========
def _execute_task(task_id: str, task: str, message: str, allowed_path: str, sys_prompt: str, max_rounds: int = 40):
    """
    执行一个任务：调用 LLM → 解析工具 → 校验(source硬编码+路径+权限) → 执行 → 注入结果 → 下一轮。
    task_complete 由本函数拦截处理，不交给 ParsingTool。
    """
    # 动态加载 ParsingTool（避免循环导入）
    spec = importlib.util.spec_from_file_location(
        "ParsingTool",
        os.path.join(os.path.dirname(SCRIPT_DIR), "ParsingTool.py")
    )
    ParsingMod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ParsingMod)
    Parser = ParsingMod.ParseTool()
    ToolConfigMap = Parser.ToolConfigMap  # name -> config

    # 组装初始消息
    user_input = f"[任务] {task}"
    if message:
        user_input += f"\n[任务前提/描述] {message}"
    if allowed_path:
        user_input += f"\n[路径权限] 你只能在以下目录范围内操作文件: {allowed_path}。越权会被拦截。"
    user_input += "\n[完成要求] 完成后必须调用 task_complete 提交完成描述。"
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_input},
    ]

    final_response = ""
    for round_idx in range(1, max_rounds + 1):
        answer = _call_llm_once(messages)
        if answer.startswith("状态:Error"):
            _fail_task(task_id, answer)
            return answer

        final_response = answer
        messages.append({"role": "assistant", "content": answer})

        # 解析所有工具调用
        valid_tools = [t for t in Parser.ExtractAllJsonFromText(answer)
                       if isinstance(t, dict) and t.get("name")]
        if not valid_tools:
            break  # 无工具调用 → 结束（但未调用 task_complete，后续会标记失败）

        # 分离 task_complete（由本函数拦截）和其他工具（交给 ToolRouting 执行）
        complete_tool = None
        other_tools = []
        for t in valid_tools:
            if t.get("name") == "task_complete":
                complete_tool = t
            else:
                other_tools.append(t)

        # ========== source 硬编码：强制 tool_search 的 source="TaskAgent"（防越权）==========
        # 不暴露 source 给 LLM，无论 LLM 传什么，系统强制覆盖为 TaskAgent
        for t in other_tools:
            if t.get("name") == "tool_search":
                if not isinstance(t.get("parameters"), dict):
                    t["parameters"] = {}
                t["parameters"]["source"] = "TaskAgent"

        # ========== 执行其他工具（权限校验 + 路径校验 + 执行）==========
        result_list = []
        for t in other_tools:
            tool_name = t.get("name")
            tool_params = t.get("parameters", {}) or {}

            # 权限校验：检查是否在 TaskAgent 的 Accessible 里
            tool_config = ToolConfigMap.get(tool_name)
            if not tool_config:
                result_list.append({"name": tool_name,
                                    "output": f"状态:Error, 工具 '{tool_name}' 未注册"})
                continue
            if "TaskAgent" not in tool_config.get("Accessible", []):
                result_list.append({"name": tool_name,
                                    "output": f"状态:Error, 工具 '{tool_name}' 不在 TaskAgent 的使用列表里（越权拒绝）"})
                continue

            # 路径权限校验：文件类工具的路径必须在 allowed_path 范围内
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

        # ========== 拦截 task_complete：由本函数处理，不交给 ParsingTool ==========
        if complete_tool:
            params = complete_tool.get("parameters", {}) or {}
            summary = params.get("summary", "")
            artifacts = params.get("artifacts", [])
            if not summary:
                summary = final_response[:200]  # 兜底：用最后回复截断作为摘要
            _finish_task(task_id, summary, artifacts)
            print(f"[TaskAgent] 任务 {task_id} 完成: {summary}")
            break  # 任务完成，结束循环
    else:
        # 达到最大轮数仍未完成（40 轮 React 上限，直接失败，不询问用户）
        _fail_task(task_id, "已达最大轮数限制(40轮)，任务未通过 task_complete 完成")
        final_response += "\n\n[TaskAgent] 已达 40 轮 React 上限，强制结束（未调用 task_complete，任务标记失败）。"

    # 兜底：循环正常结束但未调用 task_complete → 标记失败
    log = _load_log()
    for r in log:
        if r.get("task_id") == task_id and r.get("status") == "running":
            _fail_task(task_id, f"任务未调用 task_complete 正式结束。最后输出: {final_response[:300]}")
            break

    return final_response


def _run_task_async(task_id: str, task: str, message: str, allowed_path: str, sys_prompt: str):
    """线程入口：执行任务，更新记录。在独立线程运行，不阻塞主线程。"""
    _start_task(task_id)  # waiting → running
    try:
        _execute_task(task_id, task, message, allowed_path, sys_prompt)
    except Exception as e:
        _fail_task(task_id, f"线程执行异常: {e}")
        print(f"[TaskAgent] 任务 {task_id} 异常: {e}")


# ========== 入口函数 ==========
def run(params: Dict[str, Any]) -> str:
    """
    TaskAgent 入口函数。
    behavior:
        - add:    创建并异步执行任务（立即返回任务ID，不阻塞主线程）
        - list:   查看所有任务记录
        - query:  拉取已完成/失败任务的完成报告（主动发送给贾维斯后标记已读）
        - remove: 结束任务
    """
    try:
        parameters = params.get("parameters") or {}
        behavior = parameters.get("behavior")

        if behavior == "add":
            task = parameters.get("task")
            message = parameters.get("message")
            path = parameters.get("path")  # 新增：限定 TaskAgent 文件访问范围
            if not task:
                return "状态:Error, 缺少 task 参数"

            # 1. 加载系统提示词（动态注入工具列表，同贾维斯机制）
            sys_prompt = _load_taskagent_prompt()
            if not sys_prompt:
                return "状态:Error, 无法加载 TaskAgent 提示词"

            # 2. 创建任务记录（初始状态 waiting）
            task_id = _create_task(task, message, path)

            # 3. 启动独立线程执行任务（不阻塞主线程）
            thread = threading.Thread(
                target=_run_task_async,
                args=(task_id, task, message, path, sys_prompt),
                daemon=True,
                name=f"TaskAgent-{task_id}"
            )
            thread.start()

            return (f"状态:200, 任务已创建,TaskAgent 正在后台异步执行。"
                    f"任务ID: {task_id} | 路径权限: {path or '无限制'} | "
                    f"可用 behavior=query 拉取完成报告。")

        elif behavior == "query":
            # 拉取已完成/失败且未通知的任务报告，标记已读（主动发送给贾维斯）
            log = _load_log()
            unread = [r for r in log
                      if r.get("status") in ("completed", "failed") and not r.get("notified")]
            if not unread:
                return "状态:200, 暂无新的任务完成报告"
            # 标记已通知
            for r in log:
                if r.get("status") in ("completed", "failed") and not r.get("notified"):
                    r["notified"] = True
            _save_log(log)
            # 格式化返回完成报告
            parts = [f"状态:200, 有 {len(unread)} 个任务报告:"]
            for r in unread:
                parts.append(f"\n{'='*50}")
                parts.append(f"任务ID: {r['task_id']}")
                parts.append(f"状态: {r['status']}")
                parts.append(f"原始任务: {r['task']}")
                if r.get("message"):
                    parts.append(f"任务描述: {r['message']}")
                parts.append(f"开始: {r.get('start_time','')} | 结束: {r.get('end_time','')}")
                parts.append(f"完成描述: {r.get('result','')}")
                if r.get("artifacts"):
                    parts.append(f"产物: {', '.join(r['artifacts'])}")
            return "\n".join(parts)

        elif behavior == "list":
            log = _load_log()
            if not log:
                return "状态:200, 暂无任务记录"
            parts = [f"状态:200, 共 {len(log)} 个任务:"]
            for r in log:
                parts.append(f"- [{r.get('status','?')}] {r.get('task_id','')}: {(r.get('task','') or '')[:50]}")
            return "\n".join(parts)

        elif behavior == "remove":
            return "状态:200, 任务已结束"

        else:
            return f"状态:Error, 未知 behavior: {behavior}（支持: add/list/query/remove）"

    except Exception as e:
        return f"状态:Error, {e}"
