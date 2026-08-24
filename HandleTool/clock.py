import json
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

# 提醒数据存储文件
REMINDER_FILE = Path(__file__).parent.parent / "clock_reminders.json"

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _load():
    if not REMINDER_FILE.exists() or REMINDER_FILE.stat().st_size == 0:
        return []
    try:
        with open(REMINDER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []

def _save(reminders):
    REMINDER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REMINDER_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, ensure_ascii=False, indent=2)

def _next_id(reminders):
    return max((r.get("id", 0) for r in reminders), default=0) + 1

def _parse_time(time_str):
    """解析时间字符串，支持 'HH:MM' (今天) 或 'YYYY-MM-DD HH:MM'"""
    now = datetime.now()
    time_str = time_str.strip()
    
    # 尝试解析为完整日期时间
    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M")
    except ValueError:
        pass
    
    # 尝试解析为仅时间 (HH:MM)，默认为今天
    try:
        parsed_time = datetime.strptime(time_str, "%H:%M")
        target = now.replace(hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0)
        # 如果解析出的时间已经过了今天，则自动顺延到明天
        if target <= now:
            target += timedelta(days=1)
        return target
    except ValueError:
        return None

def _show_toast(title, message):
    """使用 PowerShell 显示 Windows 原生 Toast 通知"""
    # 转义 XML 特殊字符
    title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    script = f'''
 [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $template = @"
    <toast>
        <visual>
            <binding template="ToastText02">
                <text id="1">{title}</text>
                <text id="2">{message}</text>
            </binding>
        </visual>
    </toast>
"@
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml($template)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Jarvis").Show($toast)
    '''
    try:
        subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", script],
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False

def _format_reminder(r):
    rid = r.get("id", "?")
    time = r.get("time", "")
    message = r.get("message", "")
    status = r.get("status", "pending")
    icon = "⏰" if status == "pending" else "✅"
    return f"[{rid}] {icon} {time} | {message}"

def run(params):
    try:
        behavior = params.get("behavior")
        if behavior not in ('add', 'check', 'list', 'remove'):
            return "状态:Error,原因:behavior参数错误，仅支持 add/check/list/remove"

        reminders = _load()

        # ---------- add：添加提醒 ----------
        if behavior == "add":
            time_str = params.get("time")
            message = params.get("message", "时间到了！")
            
            if not time_str:
                return "状态:Error,原因:缺少 time 参数"
            
            target_time = _parse_time(time_str)
            if not target_time:
                return f"状态:Error,原因:无法解析时间格式 '{time_str}'，请使用 'HH:MM' 或 'YYYY-MM-DD HH:MM'"
            
            new_reminder = {
                "id": _next_id(reminders),
                "time": target_time.strftime("%Y-%m-%d %H:%M"),
                "message": message,
                "status": "pending",
                "created_at": _now()
            }
            reminders.append(new_reminder)
            _save(reminders)
            return f"状态:200,提醒已设置: [{new_reminder['id']}] {new_reminder['time']} | {message}"

        # ---------- check：检查并触发到期提醒 ----------
        elif behavior == "check":
            now = datetime.now()
            triggered = []
            
            for r in reminders:
                if r.get("status") == "pending":
                    try:
                        r_time = datetime.strptime(r["time"], "%Y-%m-%d %H:%M")
                        if now >= r_time:
                            # 触发通知
                            _show_toast("贾维斯提醒", r["message"])
                            r["status"] = "done"
                            r["triggered_at"] = _now()
                            triggered.append(r)
                    except ValueError:
                        continue
            
            if triggered:
                _save(reminders)
                results = [f"[{r['id']}] {r['time']} | {r['message']}" for r in triggered]
                return f"状态:200,已触发 {len(triggered)} 条提醒:\n" + "\n".join(results)
            else:
                return "状态:200,当前无到期提醒"

        # ---------- list：列出所有提醒 ----------
        elif behavior == "list":
            pending = [r for r in reminders if r.get("status") == "pending"]
            if not pending:
                return "状态:200,当前无待办提醒"
            
            lines = [_format_reminder(r) for r in pending]
            return "状态:200,待办提醒列表:\n" + "\n".join(lines)

        # ---------- remove：删除提醒 ----------
        elif behavior == "remove":
            rid = params.get("id")
            if not rid:
                return "状态:Error,原因:缺少 id 参数"
            
            initial_count = len(reminders)
            reminders = [r for r in reminders if r.get("id") != rid]
            
            if len(reminders) < initial_count:
                _save(reminders)
                return f"状态:200,提醒 [{rid}] 已删除"
            else:
                return f"状态:Error,原因:未找到 ID 为 {rid} 的提醒"

    except Exception as e:
        return f"状态:Error,原因:执行异常 - {str(e)}"
