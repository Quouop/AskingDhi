from pathlib import Path
import json
from datetime import datetime

TODO_FILE = Path(__file__).parent.parent / "todo.json"

VALID_STATUSES = ('pending', 'doing', 'done', 'failed')

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _load():
    if not TODO_FILE.exists() or TODO_FILE.stat().st_size == 0:
        return []
    try:
        with open(TODO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []

def _save(todos):
    with open(TODO_FILE, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)

def _next_id(todos):
    return max((t.get("id", 0) for t in todos), default=0) + 1

def _status_icon(status):
    return {"pending": "⭕", "doing": "🔄", "done": "✅", "failed": "❌"}.get(status, "❓")

def _format_todo(t):
    icon = _status_icon(t.get("status", "pending"))
    sid = t.get("id", "?")
    task = t.get("task", "")
    status = t.get("status", "pending")
    created = t.get("created_at", "")
    updated = t.get("updated_at", "")
    line = f"[{sid}] {icon} {status.upper()} | {task}"
    if created:
        line += f"\n       创建: {created}"
    if updated and updated != created:
        line += f"\n       更新: {updated}"
    return line

def run(params):
    try:
        behavior = params.get("behavior")
        if behavior not in ('write', 'finish', 'read', 'update'):
            return "状态:Error,原因:behavior参数错误，仅支持 write/finish/read/update"

        todos = _load()

        # ---------- write：新增任务 ----------
        if behavior == "write":
            task = params.get("task")
            if not task:
                return "状态:Error,原因:缺少 task 参数"
            if isinstance(task, str):
                tasks = [task]
            elif isinstance(task, list):
                tasks = [str(t) for t in task if t]
            else:
                return "状态:Error,原因:task参数类型错误，需字符串或列表"

            now = _now()
            new_ids = []
            for t in tasks:
                new_id = _next_id(todos)
                todos.append({
                    "id": new_id,
                    "task": t.strip(),
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                })
                new_ids.append(new_id)
            _save(todos)
            return f"状态:200,已新增{len(new_ids)}个任务,ID: {new_ids}"

        # ---------- finish：标记完成 ----------
        if behavior == "finish":
            tid = params.get("id")
            task_keyword = params.get("task")
            if tid is None and not task_keyword:
                return "状态:Error,原因:需传入 id 或 task 参数"

            now = _now()
            matched = None
            if tid is not None:
                try:
                    tid = int(tid)
                except (TypeError, ValueError):
                    return "状态:Error,原因:id必须是数字"
                for i, t in enumerate(todos):
                    if t.get("id") == tid:
                        matched = i
                        break
            if matched is None and task_keyword:
                # 精确匹配优先
                for i, t in enumerate(todos):
                    if t.get("task") == task_keyword:
                        matched = i
                        break
                if matched is None:
                    # 模糊包含匹配
                    for i, t in enumerate(todos):
                        if task_keyword in t.get("task", ""):
                            matched = i
                            break

            if matched is None:
                return f"状态:Error,原因:未找到匹配的任务 (id={tid}, task={task_keyword})"

            todos[matched]["status"] = "done"
            todos[matched]["updated_at"] = now
            _save(todos)
            return f"状态:200,已完成任务: [#{todos[matched]['id']}] {todos[matched]['task']}"

        # ---------- update：修改状态/内容 ----------
        if behavior == "update":
            tid = params.get("id")
            if tid is None:
                return "状态:Error,原因:缺少 id 参数"
            try:
                tid = int(tid)
            except (TypeError, ValueError):
                return "状态:Error,原因:id必须是数字"

            target = None
            for i, t in enumerate(todos):
                if t.get("id") == tid:
                    target = i
                    break
            if target is None:
                return f"状态:Error,原因:未找到ID为{tid}的任务"

            new_status = params.get("status")
            new_task = params.get("task")
            if new_status and new_status not in VALID_STATUSES:
                return f"状态:Error,原因:status仅支持 {VALID_STATUSES}"

            changed = False
            if new_status:
                todos[target]["status"] = new_status
                changed = True
            if new_task is not None:
                todos[target]["task"] = new_task.strip()
                changed = True
            if not changed:
                return "状态:Error,原因:未提供任何修改内容(status/task)"

            todos[target]["updated_at"] = _now()
            _save(todos)
            return f"状态:200,已更新任务 #{tid}"

        # ---------- read：读取列表 ----------
        if behavior == "read":
            if not todos:
                return "状态:200,当前无待办任务"

            status_filter = params.get("status")  # 可选：按状态过滤
            filtered = todos
            if status_filter:
                if isinstance(status_filter, str):
                    status_filter = [s.strip() for s in status_filter.split(",") if s.strip()]
                filtered = [t for t in todos if t.get("status") in status_filter]

            if not filtered:
                return f"状态:200,未找到状态为 {status_filter} 的任务"

            lines = [_format_todo(t) for t in filtered]
            count = len(filtered)
            total_done = sum(1 for t in todos if t.get("status") == "done")
            total_pending = sum(1 for t in todos if t.get("status") in ("pending", "doing"))
            header = f"状态:200,显示{count}条,进度:{total_done}完成/{total_pending}进行中"
            return header + "\n" + "\n\n".join(lines)

    except Exception as e:
        return f"状态:Error,原因:{e}"
