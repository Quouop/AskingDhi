"""wake 工具：让 AI（或外部系统）能够调度"到点给 AI 主动发句话"。

用法示例（LLM 输出的工具调用 JSON）：
  {"name": "wake", "parameters": {"behavior": "schedule",
                                  "trigger_at": "2026-08-26 22:30:00",
                                  "content": "请提醒我去洗澡",
                                  "max_triggers": 3,
                                  "interval_minutes": 120,
                                  "miss_policy": "skip",
                                  "catchup_threshold_seconds": 300}}

max_triggers 语义：
  -1  无限次（永远滚动，直到被 cancel / delete）
   0  已完成（创建时会拒绝；触发完=0 次时立刻从队列移除）
 >=1  剩余 N 次（schedule 传 3 => 一共触发 3 次）

错过策略（关机/休眠导致超过 trigger_at N 久之后才回到系统时怎么办）：
  miss_policy=skip（默认）：超过 catchup_threshold_seconds 窗口的事件全部跳过不触发，
    剩余次数会一并扣掉（"每天 22:30 洗澡，昨晚关机错过了=昨晚那次就过去了，今晚 22:30 再提醒"）。
    ⚠️ 人类提醒类任务默认这样做——**不会早上开机补跑昨晚的事**。
  miss_policy=backfill：超过窗口的事件在本轮一次性全部补齐（立即连发 N 条假用户消息）。
    仅用于"必须不丢次数"的自动化任务（如每小时记日志），人类提醒别用。

behavior:
  - schedule: 创建一条唤醒（trigger_at 必需, content 必需）
              可选: max_triggers / interval_minutes / miss_policy / catchup_threshold_seconds
  - cancel:   取消未触发的唤醒（id 必需；对重复模式直接停掉整个系列）
  - delete:   物理删除唤醒（id 必需；无论是否已 cancel/done 都能删）
  - list:     列出所有仍在激活中的唤醒（含剩余次数）
  - gc:       清理超过 N 天的已触发记录（可选 keep_days，默认 7）
"""
import os
import sys
from datetime import datetime
from typing import Dict, Any

try:
    from wake_queue import (
        schedule_wake, cancel_wake, delete_wake, list_wakes, gc_wakes,
    )
except ImportError:
    # 被 ToolRouting spec_from_file_location 加载时找不到相对包，走绝对路径兜底
    _proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _proj_dir not in sys.path:
        sys.path.insert(0, _proj_dir)
    from wake_queue import (
        schedule_wake, cancel_wake, delete_wake, list_wakes, gc_wakes,
    )


def _parse_dt(s: str) -> datetime:
    """支持两种格式：%Y-%m-%d %H:%M:%S 和 %Y-%m-%d %H:%M"""
    s = (s or "").strip()
    if not s:
        raise ValueError("时间为空")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间: {s} (支持 'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD HH:MM:SS')")


def run(params) -> str:
    try:
        if not isinstance(params, dict):
            params = {}
        behavior = params.get("behavior")
        if behavior not in ("schedule", "cancel", "delete", "list", "gc"):
            return ("状态:Error, 原因:behavior 参数错误，"
                    "仅支持 schedule/cancel/delete/list/gc")

        if behavior == "list":
            items = list_wakes(include_done=False)
            if not items:
                return "状态:200, 暂无待触发的唤醒。"
            parts = [f"状态:200, 共 {len(items)} 条仍在激活中的唤醒:"]
            for m in items:
                remain = m.get("remaining_triggers", 1)
                if remain == -1:
                    remain_txt = "无限次"
                else:
                    remain_txt = f"剩余{remain}次"
                interv = int(m.get("interval_minutes", 1440))
                policy = m.get("miss_policy", "skip")
                thresh_s = int(m.get("catchup_threshold_seconds", 300))
                missed = int(m.get("missed_count", 0))
                if remain == 1:
                    repeat_txt = "单次"
                else:
                    h = interv // 60
                    mnt = interv % 60
                    if h and mnt:
                        interval_txt = f"每{h}小时{mnt}分钟"
                    elif h:
                        interval_txt = f"每{h}小时"
                    else:
                        interval_txt = f"每{mnt}分钟"
                    repeat_txt = f"{remain_txt}, {interval_txt}"
                policy_txt = (
                    f"错过策略={policy}, 窗口={thresh_s}s"
                    f"{' | 累计跳过=' + str(missed) if missed else ''}"
                )
                parts.append(
                    f"- [下次触发: {m.get('trigger_at','?')}] id={m.get('id','?')} | "
                    f"来源={m.get('source','?')} | {repeat_txt} | {policy_txt} | "
                    f"内容={m.get('content','')[:50]}"
                )
            return "\n".join(parts)

        if behavior == "gc":
            keep_days = int(params.get("keep_days", 7))
            n = gc_wakes(keep_days=keep_days)
            return f"状态:200, 已清理 {n} 条超过 {keep_days} 天的旧唤醒记录。"

        if behavior == "cancel":
            wake_id = params.get("id")
            if not wake_id:
                return "状态:Error, 原因:cancel 行为需要 id 参数"
            ok = cancel_wake(wake_id)
            if ok:
                return f"状态:200, 唤醒 [{wake_id}] 已取消（整个系列停止，下一轮会被移除）。"
            return f"状态:Error, 原因:未找到唤醒 [{wake_id}] 或已 done/已取消。"

        if behavior == "delete":
            wake_id = params.get("id")
            if not wake_id:
                return "状态:Error, 原因:delete 行为需要 id 参数"
            ok = delete_wake(wake_id)
            if ok:
                return f"状态:200, 唤醒 [{wake_id}] 已物理删除。"
            return f"状态:Error, 原因:未找到唤醒 [{wake_id}]。"

        # schedule
        try:
            trigger_dt = _parse_dt(params.get("trigger_at", ""))
        except ValueError as e:
            return f"状态:Error, 原因:{e}"
        content = params.get("content") or params.get("message") or ""
        if not content.strip():
            return "状态:Error, 原因:schedule 行为需要 content 或 message 参数（到点要发给 AI 的内容）"
        source = params.get("source", "tool")

        max_triggers = params.get("max_triggers")
        interval_minutes = params.get("interval_minutes")
        miss_policy = params.get("miss_policy")
        catchup_threshold_seconds = params.get("catchup_threshold_seconds")

        # 允许显式传 max_triggers=0 的语义：直接拒绝，等价于啥也不做
        try:
            if max_triggers is not None:
                mt_coerced = int(max_triggers)
            else:
                mt_coerced = None  # 走默认=1
        except (TypeError, ValueError):
            return f"状态:Error, 原因:max_triggers 必须是整数 (-1/0/N)，收到: {max_triggers!r}"
        if mt_coerced == 0:
            return "状态:Error, 原因:max_triggers=0（0 次触发）拒绝创建，直接去除。"

        # 若触发时间已经过去了（超过 5 分钟）且是单次/第一次，拒绝避免误触发
        # （任务系统"完成通知"这种立即型场景，走"现在+几秒"即可）
        delta = (trigger_dt - datetime.now()).total_seconds()
        if delta < -300 and (mt_coerced is None or mt_coerced == 1):
            return (f"状态:Error, 原因:trigger_at {trigger_dt.strftime('%Y-%m-%d %H:%M:%S')}"
                    f" 已过去超过 5 分钟，拒绝创建。如需立即触发请直接对话，"
                    f"或传入 interval_minutes + max_triggers 并把 trigger_at 设为未来时间。")

        try:
            wid = schedule_wake(
                trigger_dt, content, source=source,
                max_triggers=max_triggers,
                interval_minutes=interval_minutes,
                miss_policy=miss_policy,
                catchup_threshold_seconds=catchup_threshold_seconds,
            )
        except ValueError as e:
            return f"状态:Error, 原因:{e}"

        # 语义展示：剩余次数
        if mt_coerced is None:
            remain_show = "单次"
        elif mt_coerced == -1:
            remain_show = "无限次"
        else:
            remain_show = f"{mt_coerced}次"

        # 语义展示：间隔（仅重复时用）
        interval_show = ""
        if mt_coerced is not None and mt_coerced != 1:
            if interval_minutes is None:
                iv = 1440  # 默认一天
            else:
                iv = int(interval_minutes)
            h = iv // 60
            mnt = iv % 60
            if h and mnt:
                interval_show = f"，间隔每{h}小时{mnt}分钟"
            elif h:
                interval_show = f"，间隔每{h}小时"
            else:
                interval_show = f"，间隔每{mnt}分钟"

        # 语义展示：错过策略
        policy_show = (
            f"，错过策略={str(miss_policy).strip().lower() if miss_policy else 'skip(默认)'}，"
            f"晚到窗口={int(catchup_threshold_seconds) if catchup_threshold_seconds is not None else 300}s"
        )

        return (
            f"状态:200, 唤醒已创建，ID={wid}，"
            f"首次触发={trigger_dt.strftime('%Y-%m-%d %H:%M:%S')}，"
            f"次数={remain_show}{interval_show}{policy_show}，"
            f"内容={content[:40] + ('...' if len(content) > 40 else '')}。"
            f"到点后系统会主动以用户身份向 AskingDhi 发送该内容并等待回复。"
            f"关机/休眠错过时按 miss_policy 处理(skip=跳过不补)；"
            f"每次触发完毕，max_triggers=0/该次结束的条目会立即从队列中物理去除。"
        )

    except Exception as e:
        return f"状态:Error, 原因:未预期错误: {e}"
