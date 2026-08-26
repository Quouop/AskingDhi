"""唤醒队列：跨线程/跨进程共享的"到点给 AI 发句话"调度器。

设计要点（对屎山友好）：
- 不使用额外 pip 依赖，只用 stdlib。
- 存储用 JSON 文件（lock 用同目录同名 .lock，跨线程线程锁+跨进文件锁兜底）。
- 主循环里每 30s 调用 pop_due_wakes()，有就按 content 调 StreamDialogue()。

关键语义修正（防"关机后早上补跑昨晚任务"）：
- 每条唤醒有 miss_policy（默认 skip）和 catchup_threshold_seconds（默认 300s）。
- 实际 fire 时 now - trigger_at 超过 catchup_threshold，就算"错过窗口"：
  - skip：错过的事件**不 fire**，剩余次数一并扣掉，前进到下一个 > now 的时间点。
    这是默认行为，符合人类"22:30 洗澡没洗=昨晚那次就过去了，明晚 22:30 再提醒"。
  - backfill：本轮一次性把错过的 N 次都 fire（立即连发 N 条"假用户消息"）。
    只用于"必须不丢次数"的自动化任务，人类提醒类别用。
"""
import json
import os
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

_QUEUE_FILE = Path(__file__).parent / "memory" / "wake_queue.json"
_LOCK_FILE = Path(__file__).parent / "memory" / "wake_queue.lock"
_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)

# 进程内线程锁（同一 Python 进程内多线程竞争）
_thread_lock = threading.Lock()

# ---------- 对外 API 常量 ----------
DEFAULT_INTERVAL_MINUTES = 24 * 60         # 默认每天一次（仅 max_triggers != 1 时才用到）
DEFAULT_CATCHUP_THRESHOLD_SECONDS = 5 * 60  # 默认 5 分钟：超过这个时间算"错过窗口"
VALID_MISS_POLICIES = ("skip", "backfill")


# ---------- 跨进程文件锁（Win 兼容，msvcrt 兜底） ----------
class _FileLock:
    """最小可用的文件锁：排他创建 lock 文件；僵死 > 60s 自动回收。"""

    def __init__(self, lock_path: Path):
        self.lock_path = Path(lock_path)
        self._fh = None

    def __enter__(self):
        _thread_lock.acquire()
        while True:
            try:
                self._fh = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    0o644,
                )
                os.write(self._fh, f"{os.getpid()}\n".encode("utf-8"))
                os.fsync(self._fh)
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > 60:
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                time.sleep(0.2)

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fh is not None:
                os.close(self._fh)
                self._fh = None
        except Exception:
            pass
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass
        _thread_lock.release()


def _file_lock() -> _FileLock:
    return _FileLock(_LOCK_FILE)


# ---------- 基础读写 ----------
def _load() -> List[Dict[str, Any]]:
    if not _QUEUE_FILE.exists() or _QUEUE_FILE.stat().st_size == 0:
        return []
    try:
        with open(_QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(data: List[Dict[str, Any]]) -> None:
    _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _QUEUE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _QUEUE_FILE)


# ---------- 类型/参数校验 ----------
def _coerce_max_triggers(value) -> int:
    if value is None:
        return 1
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"max_triggers 必须是整数 (-1/0/N)，收到: {value!r}")
    if v < -1:
        raise ValueError(f"max_triggers 不能小于 -1，收到: {v}")
    return v


def _coerce_interval_minutes(value) -> int:
    if value is None:
        return DEFAULT_INTERVAL_MINUTES
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"interval_minutes 必须是正整数，收到: {value!r}")
    if v <= 0:
        raise ValueError(f"interval_minutes 必须 > 0，收到: {v}")
    if v > 365 * 24 * 60:
        raise ValueError(f"interval_minutes 最大 365 天(525600 分钟)，收到: {v}")
    return v


def _coerce_miss_policy(value) -> str:
    if value is None:
        return "skip"
    v = str(value).strip().lower()
    if v not in VALID_MISS_POLICIES:
        raise ValueError(f"miss_policy 仅支持 {VALID_MISS_POLICIES}，收到: {value!r}")
    return v


def _coerce_catchup_threshold_seconds(value) -> int:
    if value is None:
        return DEFAULT_CATCHUP_THRESHOLD_SECONDS
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"catchup_threshold_seconds 必须是非负整数，收到: {value!r}")
    if v < 0:
        raise ValueError(f"catchup_threshold_seconds 不能 < 0，收到: {v}")
    if v > 30 * 24 * 3600:
        raise ValueError(f"catchup_threshold_seconds 过大(>30天)，收到: {v}")
    return v


def _parse_ts(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


# ---------- 对外 API ----------
def schedule_wake(
    trigger_at: datetime,
    content: str,
    source: str = "tool",
    max_triggers=None,              # 1单次/-1无限/0拒绝创建/>=2剩余N次
    interval_minutes=None,          # 重复间隔分钟数（仅 !=1 用）
    miss_policy: str = None,        # "skip"(默认) 或 "backfill"
    catchup_threshold_seconds=None, # 晚到仍触发窗口秒数(默认300)
) -> str:
    """提交一条唤醒任务。详见模块级 docstring 对"错过窗口"的说明。"""
    if not content or not isinstance(content, str) or not content.strip():
        raise ValueError("schedule_wake: content 不能为空")
    if not isinstance(trigger_at, datetime):
        raise TypeError("schedule_wake: trigger_at 必须是 datetime")

    max_n = _coerce_max_triggers(max_triggers)
    if max_n == 0:
        raise ValueError("max_triggers=0（0 次触发）拒绝创建")

    interval = _coerce_interval_minutes(interval_minutes)
    policy = _coerce_miss_policy(miss_policy)
    threshold_s = _coerce_catchup_threshold_seconds(catchup_threshold_seconds)

    item = {
        "id": f"wake_{int(time.time()*1000)}_{os.getpid()}",
        "trigger_at": trigger_at.strftime("%Y-%m-%d %H:%M:%S"),
        "content": content.strip(),
        "source": source,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "max_triggers": max_n,
        "interval_minutes": interval,
        "miss_policy": policy,
        "catchup_threshold_seconds": threshold_s,
        # 运行时字段
        "fired_count": 0,
        "missed_count": 0,
        "last_fired_at": "",
        "fired": False,
        "done": False,
    }
    with _file_lock():
        data = _load()
        data.append(item)
        _save(data)
    return item["id"]


def cancel_wake(wake_id: str) -> bool:
    """取消唤醒。不管是单次还是重复模式，只要没 canceled/done 就可以取消。"""
    if not wake_id:
        return False
    with _file_lock():
        data = _load()
        for m in data:
            if m.get("id") == wake_id and not m.get("canceled") and not m.get("done"):
                m["canceled"] = True
                m["canceled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _save(data)
                return True
        return False


def delete_wake(wake_id: str) -> bool:
    """物理删除一条唤醒（已 canceled/done 也能删，用于清理）。"""
    if not wake_id:
        return False
    with _file_lock():
        data = _load()
        for i, m in enumerate(data):
            if m.get("id") == wake_id:
                del data[i]
                _save(data)
                return True
        return False


def _advance_trigger_until(prev_trigger: datetime,
                           interval_minutes: int,
                           after: datetime) -> datetime:
    """把 prev_trigger 往前加 interval，直到结果严格 > after。返回那个时间点。"""
    delta = timedelta(minutes=interval_minutes)
    nxt = prev_trigger + delta
    while nxt <= after:
        nxt = nxt + delta
    return nxt


def _pop_one_due(m: Dict[str, Any], now: datetime, now_str: str) -> List[Dict[str, Any]]:
    """针对单条到期记录，结合错过策略产出 0~N 个"应触发"的事件。

    返回列表：应被 wake_poller 触发的事件（0 个表示全部被 skip 了）。
    副作用：直接修改 m（更新 max_triggers / trigger_at / missed_count / fired_count / done / fired / last_fired_at ...）。

    正确的分层：
      - 对 now-threshold 之前的那些点：绝对错过，按 policy 处理(skip / backfill)。
      - 对 (now-threshold, now] 之间的那一个点：仍在窗口内，走正常 ontime fire。
      - 对 now 之后的点：不动，留在 trigger_at
    """
    out: List[Dict[str, Any]] = []

    max_n = m.get("max_triggers", 1)
    interval = int(m.get("interval_minutes", DEFAULT_INTERVAL_MINUTES))
    policy = _coerce_miss_policy(m.get("miss_policy"))
    threshold_s = int(m.get("catchup_threshold_seconds", DEFAULT_CATCHUP_THRESHOLD_SECONDS))
    cutoff_ts = now - timedelta(seconds=threshold_s) if threshold_s > 0 else now

    anchor = _parse_ts(m.get("trigger_at", "")) or now  # 本记录原定"下次触发点"
    step = timedelta(minutes=interval)

    # 第一阶段：把 anchor 从当前 trigger_at 一步步推到 cutoff 之后最近的一个点，
    # 走过的每一步 = 1 次"绝对错过"的事件，按 policy 决定扣不扣/补不补。
    absolute_missed = 0
    backfill_items: List[datetime] = []
    cursor = anchor
    while cursor <= cutoff_ts:
        # cursor 这一次肯定错过窗口了
        absolute_missed += 1
        if policy == "backfill":
            backfill_items.append(cursor)
        cursor = cursor + step

    # 绝对错过阶段的"扣次数 / 补 due 项"统一处理
    if absolute_missed > 0:
        # 限制：backfill 单次最多 100 项，防止 interval=1 关机半年
        MAX_BACKFILL = 100
        if policy == "backfill":
            # 超过上限：超出部分按 skip 处理（记 missed，不 fire）
            if len(backfill_items) > MAX_BACKFILL:
                overflow = len(backfill_items) - MAX_BACKFILL
                backfill_items = backfill_items[:MAX_BACKFILL]
            else:
                overflow = 0
        else:
            overflow = 0

        how_many_consume = absolute_missed  # 无论 skip 还是 backfill，绝对错过的次数都消耗"配额"
        # 如果是有限次数且 absolute_missed > 剩余次数，就只能消耗到 0
        if max_n >= 1:
            consume = min(how_many_consume, max_n)
            max_n -= consume
            m["max_triggers"] = max_n
            skipped_count = consume - len(backfill_items) + overflow
            m["missed_count"] = int(m.get("missed_count", 0)) + max(skipped_count, 0)
        else:
            # -1 无限
            consume = how_many_consume
            skipped_count = consume - len(backfill_items) + overflow
            m["missed_count"] = int(m.get("missed_count", 0)) + max(skipped_count, 0)

        # backfill 的 due 项追加（携带 missed 元信息）
        for idx, vt in enumerate(backfill_items):
            snapshot = dict(m)
            snapshot["catchup_kind"] = "backfill"
            snapshot["catchup_of_total"] = len(backfill_items)
            snapshot["catchup_index"] = idx
            snapshot["trigger_at"] = vt.strftime("%Y-%m-%d %H:%M:%S")
            snapshot["last_fired_at"] = now_str
            snapshot["fired_count"] = int(m.get("fired_count", 0)) + idx + 1
            out.append(snapshot)

        if backfill_items:
            m["fired_count"] = int(m.get("fired_count", 0)) + len(backfill_items)
            m["last_fired_at"] = now_str

        # 如果消耗完剩余次数，直接 done
        if max_n >= 0 and max_n <= 0:
            m["fired"] = True
            m["fired_at"] = now_str
            m["done"] = True
            # anchor 已经推进到 cursor（下一个 > cutoff 的点），但 done=True 会被主循环删除
            m["trigger_at"] = cursor.strftime("%Y-%m-%d %H:%M:%S")
            return out

        # 继续推进：现在 cursor 是第一个 > cutoff 的点。把它写成 trigger_at（可能仍在窗口内，也可能还大于 now）
        anchor = cursor
        m["trigger_at"] = anchor.strftime("%Y-%m-%d %H:%M:%S")

    # 第二阶段：anchor 在 cutoff 之后。分两种情况：
    #   a) anchor <= now：还在 catchup 窗口内 → 正常 fire 一次
    #   b) anchor >  now：本轮不该 fire（但已扣错过的次数）→ 返回累计（可能空）
    if anchor <= now:
        return _apply_one_fire(
            m, now_str, anchor, interval, one_shot_fire=True, out_appendix=out
        )
    return out


def _apply_one_fire(m: Dict[str, Any], now_str: str,
                    prev_ts: datetime, interval: int,
                    one_shot_fire: bool,
                    out_appendix: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """窗口内正常 fire 一次的路径。返回 out_appendix（方便链式调用）。"""
    max_n = m.get("max_triggers", 1)
    m["last_fired_at"] = now_str
    m["fired_count"] = int(m.get("fired_count", 0)) + 1

    if max_n == 1:
        m["fired"] = True
        m["fired_at"] = now_str
        m["done"] = True
    elif max_n == -1:
        m["trigger_at"] = _advance_trigger_until(prev_ts, interval,
            datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S")
        ).strftime("%Y-%m-%d %H:%M:%S")
    else:
        remaining = max_n - 1
        m["max_triggers"] = remaining
        if remaining <= 0:
            m["fired"] = True
            m["fired_at"] = now_str
            m["done"] = True
        else:
            m["trigger_at"] = _advance_trigger_until(
                prev_ts, interval,
                datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S"),
            ).strftime("%Y-%m-%d %H:%M:%S")

    snapshot = dict(m)
    # 单次模式在 append snapshot 之后马上会被主循环物理删除，所以 trigger_at 还保持原值也行
    if max_n == 1:
        snapshot["trigger_at"] = prev_ts.strftime("%Y-%m-%d %H:%M:%S")
    out_appendix.append(snapshot)
    return out_appendix


def pop_due_wakes(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """取走所有到期的唤醒并应用 miss_policy。

    返回列表按原始 trigger_at 升序，每项包含 fired_count / missed_count /
    catchup_kind / catchup_index / catchup_of_total 等诊断字段便于上层理解。
    """
    now = now or datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    due: List[Dict[str, Any]] = []
    with _file_lock():
        data = _load()
        changed = False
        for m in data:
            if m.get("canceled") or m.get("done"):
                continue
            if m.get("max_triggers", 1) == 1 and m.get("fired"):
                continue
            if not m.get("trigger_at"):
                continue
            if m["trigger_at"] <= now_str:
                changed = True
                fired_list = _pop_one_due(m, now, now_str)
                if fired_list:
                    due.extend(fired_list)
        # 清理：done / canceled 的记录立即物理删除（用户要求 0 次就直接去除）
        before = len(data)
        data = [m for m in data if not m.get("done") and not m.get("canceled")]
        if len(data) != before:
            changed = True
        if changed:
            _save(data)
    if due:
        due.sort(key=lambda x: x.get("trigger_at", ""))
    return due


def list_wakes(include_done: bool = False) -> List[Dict[str, Any]]:
    """列出仍在激活中的唤醒（默认：未 canceled / 未 done / 未 fired）。

    返回附带 `remaining_triggers` 字段：
      - -1 = 无限
      - 0  = 已结束
      - N  = 剩余 N 次
    """
    data = _load()
    out = []
    for m in data:
        if not include_done and (m.get("done") or m.get("canceled")):
            continue
        mm = dict(m)
        mm["remaining_triggers"] = mm.get("max_triggers", 1)
        out.append(mm)
    return out


def gc_wakes(keep_days: int = 7) -> int:
    """清理 done/canceled 超过 keep_days 天的老记录（兜底，pop 已即时删除）。"""
    cutoff = time.time() - keep_days * 86400
    removed = 0
    with _file_lock():
        data = _load()
        kept = []
        for m in data:
            if m.get("done") or m.get("canceled"):
                ts = (m.get("fired_at") or m.get("canceled_at")
                      or m.get("last_fired_at") or m.get("created_at", ""))
                try:
                    t = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                except (ValueError, TypeError):
                    t = 0
                if t and t < cutoff:
                    removed += 1
                    continue
            kept.append(m)
        if removed:
            _save(kept)
    return removed
