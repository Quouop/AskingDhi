"""任务完成报告工具(占位实现)。

实际处理逻辑由 main.py 的 _execute_task 拦截:
- 提取 summary / artifacts
- 更新 taskagent_log.json 中对应任务的 status=completed / end_time / result
- 不走 ParsingTool 的动态加载执行

本文件的 run 函数仅作为防御性兜底:若被 ParsingTool 直接调用(正常流程不会),
返回提示说明此工具应由系统拦截处理。
"""


def run(params):
    summary = (params or {}).get("summary", "")
    artifacts = (params or {}).get("artifacts", [])
    return (
        "状态:200, task_complete 已由系统拦截处理。"
        f"摘要:{summary} | 产物:{artifacts}"
    )
