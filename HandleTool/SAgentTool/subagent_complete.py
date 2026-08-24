"""子智能体完成报告工具(防御性兜底实现)。

实际处理逻辑由 SAgentTool/main.py 的 _execute_subtask 拦截:
- 提取 summary / progress / next_step / artifacts
- 组装成结构化返回结果交给 TaskAgent
- 不走 ParsingTool 的动态加载执行

本文件的 run 函数仅作为防御性兜底:若被 ParsingTool 直接调用(正常流程不会),
返回提示说明此工具应由系统拦截处理。
"""


def run(params):
    p = params or {}
    summary = p.get("summary", "")
    progress = p.get("progress", "")
    next_step = p.get("next_step", "")
    artifacts = p.get("artifacts", [])
    return (
        "状态:200, subagent_complete 已由系统拦截处理。"
        f"摘要:{summary} | 进度:{progress} | 下一步:{next_step} | 产物:{artifacts}"
    )
