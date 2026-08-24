import json
from pathlib import Path
from datetime import datetime

# 长期记忆独立存储，与对话历史分离
MEMORY_FILE = Path(__file__).parent.parent / "memory" / "long_term_memory.json"
# 行为模式计数器（观察用户行为，3次成习惯）
BEHAVIOR_COUNTER_FILE = Path(__file__).parent.parent / "memory" / "behavior_counter.json"

def _load_memory():
    if not MEMORY_FILE.exists() or MEMORY_FILE.stat().st_size == 0:
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []

def _save_memory(data):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================= 行为模式计数器 =================
def _load_counter():
    if not BEHAVIOR_COUNTER_FILE.exists() or BEHAVIOR_COUNTER_FILE.stat().st_size == 0:
        return []
    try:
        with open(BEHAVIOR_COUNTER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []

def _save_counter(data):
    BEHAVIOR_COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BEHAVIOR_COUNTER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _match_pattern(pattern, existing_patterns):
    """模糊匹配：pattern 拆词，与已有 pattern 有 >=2 个共同关键词就算匹配"""
    import re
    # 提取中文词（2字以上）和英文单词
    words = set(re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}', pattern.lower()))
    if not words:
        return None
    for item in existing_patterns:
        existing_words = set(re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}', item.get("pattern", "").lower()))
        if existing_words:
            overlap = words & existing_words
            if len(overlap) >= 2:  # 至少2个关键词相同
                return item
    return None

def _next_id(data):
    return max((m.get("id", 0) for m in data), default=0) + 1

def _build_summary_text(mem):
    """把一条事件记忆格式化为简洁文本，用于注入上下文或 search 返回"""
    parts = []
    if mem.get("title"):
        parts.append(f"[{mem['title']}]")
    if mem.get("cause"):
        parts.append(f"起因: {mem['cause']}")
    if mem.get("process"):
        parts.append(f"经过: {mem['process']}")
    if mem.get("climax"):
        parts.append(f"高潮: {mem['climax']}")
    if mem.get("result"):
        parts.append(f"结果: {mem['result']}")
    if not parts and mem.get("content"):
        # 没有叙事结构，退化为纯文本
        parts.append(mem["content"])
    return " | ".join(parts) if parts else "(空记忆)"

def _match_time_condition(cond, now):
    """判断时间条件是否命中当前时间
    支持格式：
    - "HH:MM-HH:MM" 时间范围（支持跨天，如 22:00-06:00）
    - "weekday" 工作日 / "weekend" 周末
    - "monday".."sunday" 星期几
    - "HH:MM" 具体时刻（前后5分钟内命中）
    多条件用 "," 分隔，任一命中即成立
    """
    cond = str(cond).strip().lower()
    if not cond:
        return False
    weekday_map = {"monday":0, "tuesday":1, "wednesday":2, "thursday":3,
                   "friday":4, "saturday":5, "sunday":6}
    now_min = now.hour * 60 + now.minute  # 当前时刻（分钟）
    for sub in cond.split(","):
        sub = sub.strip()
        if not sub:
            continue
        # 星期
        if sub in weekday_map:
            if now.weekday() == weekday_map[sub]:
                return True
            continue
        if sub == "weekday":
            if now.weekday() < 5:
                return True
            continue
        if sub == "weekend":
            if now.weekday() >= 5:
                return True
            continue
        # 时间范围 HH:MM-HH:MM
        if "-" in sub and ":" in sub:
            try:
                start_s, end_s = sub.split("-", 1)
                sh, sm = map(int, start_s.strip().split(":"))
                eh, em = map(int, end_s.strip().split(":"))
                start_min, end_min = sh*60+sm, eh*60+em
                if start_min <= end_min:
                    if start_min <= now_min <= end_min:
                        return True
                else:  # 跨天，如 22:00-06:00
                    if now_min >= start_min or now_min <= end_min:
                        return True
            except ValueError:
                continue
            continue
        # 具体时刻 HH:MM
        if ":" in sub:
            try:
                h, m = map(int, sub.split(":"))
                if abs((h*60+m) - now_min) <= 5:
                    return True
            except ValueError:
                continue
    return False

def run(params):
    try:
        behavior = params.get("behavior")
        if behavior not in ("search", "update", "save", "compress", "observe", "check_time"):
            return "状态:Error, 原因:behavior 参数错误，仅支持 search, update, save, compress, observe 或 check_time"

        memory_data = _load_memory()

        # ================= 分支 1：查找记忆 =================
        if behavior == "search":
            query = params.get("query")
            if not query:
                return "状态:Error, 原因:search 行为缺少 query 参数"

            limit = int(params.get("limit", 5))
            query_lower = query.lower()
            scored_memory = []

            for mem in memory_data:
                # 检索范围：title + content + tags + 叙事四要素
                searchable_parts = [
                    str(mem.get("title", "")),
                    str(mem.get("content", "")),
                    str(mem.get("cause", "")),
                    str(mem.get("process", "")),
                    str(mem.get("climax", "")),
                    str(mem.get("result", "")),
                ]
                tags = mem.get("tags", [])
                if isinstance(tags, list):
                    searchable_parts.extend(str(t) for t in tags)
                content = " ".join(searchable_parts)
                content_lower = content.lower()

                score = 0
                if query_lower in content_lower:
                    score += 10
                for word in query_lower.split():
                    if len(word) > 1 and word in content_lower:
                        score += 1

                if score > 0:
                    scored_memory.append((score, mem))

            # 综合排序：分数优先，分数相同时权重优先
            scored_memory.sort(
                key=lambda x: (x[0], x[1].get("weight", 0.5)),
                reverse=True
            )
            top_matches = scored_memory[:limit]

            if not top_matches:
                return f"状态:200, 记忆检索完毕。未找到与 '{query}' 相关的记忆片段。"

            result_text = f"状态:200, 成功检索到 {len(top_matches)} 条相关记忆:\n\n"
            for i, (score, mem) in enumerate(top_matches, 1):
                role = "先生" if mem.get("role") == "user" else "AskingDhi"
                summary = _build_summary_text(mem)
                if len(summary) > 300:
                    summary = summary[:300] + "...[已截断]"
                result_text += f"[ID: {mem.get('id', '?')}] ({role}, 权重: {mem.get('weight', 0.5)}, 状态: {mem.get('status', 'unknown')}, 匹配分: {score}):\n{summary}\n\n"
            return result_text.strip()

        # ================= 分支 2：修改现有记忆 =================
        elif behavior == "update":
            target_id = params.get("id")
            if target_id is None:
                return "状态:Error, 原因:update 行为必须提供 id 参数"

            target_id = int(target_id)
            found = False
            for mem in memory_data:
                if mem.get("id") == target_id:
                    # 可更新字段：权重、状态、内容、叙事四要素、标签
                    if params.get("weight") is not None:
                        mem["weight"] = float(params.get("weight"))
                    if params.get("status") is not None:
                        mem["status"] = params.get("status")
                    if params.get("content") is not None:
                        mem["content"] = params.get("content")
                    if params.get("title") is not None:
                        mem["title"] = params.get("title")
                    if params.get("cause") is not None:
                        mem["cause"] = params.get("cause")
                    if params.get("process") is not None:
                        mem["process"] = params.get("process")
                    if params.get("climax") is not None:
                        mem["climax"] = params.get("climax")
                    if params.get("result") is not None:
                        mem["result"] = params.get("result")
                    if params.get("tags") is not None:
                        new_tags = params.get("tags")
                        if isinstance(new_tags, str):
                            new_tags = [t.strip() for t in new_tags.split(",") if t.strip()]
                        mem["tags"] = new_tags
                    if params.get("time_condition") is not None:
                        mem["time_condition"] = params.get("time_condition")
                    if params.get("time_action") is not None:
                        mem["time_action"] = params.get("time_action")
                    mem["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    found = True
                    break

            if found:
                _save_memory(memory_data)
                return f"状态:200, 记忆 ID [{target_id}] 已成功更新。"
            else:
                return f"状态:Error, 原因:未找到 ID 为 {target_id} 的记忆"

        # ================= 分支 3：新增记忆 =================
        elif behavior == "save":
            content = params.get("content")
            # 如果没传 content，尝试从 title+叙事四要素拼一个
            if not content:
                narrative = _build_summary_text({
                    "title": params.get("title"),
                    "cause": params.get("cause"),
                    "process": params.get("process"),
                    "climax": params.get("climax"),
                    "result": params.get("result"),
                })
                if narrative and narrative != "(空记忆)":
                    content = narrative
            if not content:
                return "状态:Error, 原因:save 行为缺少 content 或叙事字段(title/cause/process/climax/result)"

            role = params.get("role", "assistant")
            if role not in ("user", "assistant"):
                return "状态:Error, 原因:新增记忆时 role 必须为 user 或 assistant"

            weight = float(params.get("weight", 0.5))
            tags = params.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            new_tags_set = set(str(t).lower() for t in tags) if tags else set()

            # ================= 自动去重 =================
            duplicate_id = None
            for mem in memory_data:
                if mem.get("content", "") == content:
                    duplicate_id = mem.get("id")
                    break
                existing_tags = mem.get("tags", [])
                if isinstance(existing_tags, list):
                    existing_tags_set = set(str(t).lower() for t in existing_tags)
                    if new_tags_set and existing_tags_set:
                        overlap = new_tags_set & existing_tags_set
                        if overlap:
                            existing_weight = float(mem.get("weight", 0.5))
                            new_weight = weight
                            same_tier = (
                                (existing_weight >= 0.9 and new_weight >= 0.9) or
                                (0.5 <= existing_weight < 0.9 and 0.5 <= new_weight < 0.9) or
                                (existing_weight < 0.5 and new_weight < 0.5)
                            )
                            if same_tier:
                                duplicate_id = mem.get("id")
                                break

            if duplicate_id is not None:
                for mem in memory_data:
                    if mem.get("id") == duplicate_id:
                        if params.get("title"):
                            mem["title"] = params.get("title")
                        if params.get("cause"):
                            mem["cause"] = params.get("cause")
                        if params.get("process"):
                            mem["process"] = params.get("process")
                        if params.get("climax"):
                            mem["climax"] = params.get("climax")
                        if params.get("result"):
                            mem["result"] = params.get("result")
                        existing_tags = mem.get("tags", [])
                        if isinstance(existing_tags, list):
                            merged = list(set(existing_tags + tags))
                            mem["tags"] = merged
                        if weight > float(mem.get("weight", 0.5)):
                            mem["weight"] = weight
                        mem["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        break
                _save_memory(memory_data)
                return f"状态:200, 检测到重复记忆，已更新 ID [{duplicate_id}] 而非新增。"

            # ================= 无重复：正常新增 =================
            new_mem = {
                "id": _next_id(memory_data),
                "role": role,
                "content": content,
                "weight": weight,
                "status": params.get("status", "done"),
                "tags": tags,
                "title": params.get("title", ""),
                "cause": params.get("cause", ""),
                "process": params.get("process", ""),
                "climax": params.get("climax", ""),
                "result": params.get("result", ""),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                # 时间条件记忆字段（为空表示普通记忆）
                "time_condition": params.get("time_condition", ""),
                "time_action": params.get("time_action", "")  # remind=到点提醒 / reject=到点拒绝
            }
            memory_data.append(new_mem)
            _save_memory(memory_data)
            return f"状态:200, 新记忆已成功写入，分配 ID: [{new_mem['id']}]。"

        # ================= 分支 4：归档压缩 =================
        # LLM 完成一个大任务后，把事件总结写入长期记忆，并通知系统清理上下文
        elif behavior == "compress":
            summary = params.get("content") or params.get("summary")
            if not summary:
                return "状态:Error, 原因:compress 行为需要 content 或 summary 参数（事件总结文本）"

            weight = float(params.get("weight", 0.6))
            tags = params.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]

            # 写入长期记忆，status=archived 表示已归档
            new_mem = {
                "id": _next_id(memory_data),
                "role": "assistant",
                "content": summary,
                "weight": weight,
                "status": "archived",
                "tags": tags,
                "title": params.get("title", ""),
                "cause": params.get("cause", ""),
                "process": params.get("process", ""),
                "climax": params.get("climax", ""),
                "result": params.get("result", ""),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            memory_data.append(new_mem)
            _save_memory(memory_data)
            # 返回特殊标记，Call_Llm 会检测这个标记来触发上下文清理
            return f"状态:200, 事件已归档，记忆 ID: [{new_mem['id']}], 触发上下文压缩。"

        # ================= 分支 5：行为模式观察 =================
        # LLM 观察到用户表现出某种行为（偏好/拒绝），记录并计数
        # 3 次同一偏好 → 自动转习惯写入长期记忆
        # 3 次拒绝某建议 → 自动降权相关记忆
        elif behavior == "observe":
            pattern = params.get("pattern") or params.get("content")
            category = params.get("category", "preference")  # preference=偏好 / rejection=拒绝
            if not pattern:
                return "状态:Error, 原因:observe 行为需要 pattern 或 content 参数（行为描述）"

            counter_data = _load_counter()
            matched = _match_pattern(pattern, counter_data)

            if matched:
                # 已有相似 pattern：count+1
                matched["count"] = matched.get("count", 0) + 1
                matched["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _save_counter(counter_data)

                count = matched["count"]
                # 3 次偏好 → 自动转习惯
                if count >= 3 and category == "preference" and matched.get("status") != "confirmed":
                    matched["status"] = "confirmed"
                    _save_counter(counter_data)
                    # 写入长期记忆
                    habit_mem = {
                        "id": _next_id(memory_data),
                        "role": "assistant",
                        "content": f"[习惯] {matched['pattern']}（连续观察{count}次确认）",
                        "weight": 0.9,
                        "status": "habit",
                        "tags": ["习惯", "行为模式"],
                        "title": f"用户习惯：{matched['pattern']}",
                        "cause": "", "process": "", "climax": "", "result": "",
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    memory_data.append(habit_mem)
                    _save_memory(memory_data)
                    matched["related_memory_id"] = habit_mem["id"]
                    _save_counter(counter_data)
                    return f"状态:200, 行为模式已确认（{count}次），已自动转为习惯写入长期记忆，ID: [{habit_mem['id']}]。"

                # 3 次拒绝 → 降权相关记忆
                elif count >= 3 and category == "rejection":
                    matched["status"] = "rejected"
                    _save_counter(counter_data)
                    # 查找相关记忆降权
                    import re
                    words = set(re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}', pattern.lower()))
                    lowered = 0
                    for mem in memory_data:
                        mem_content = mem.get("content", "").lower()
                        if any(w in mem_content for w in words):
                            old_w = float(mem.get("weight", 0.5))
                            if old_w > 0.2:
                                mem["weight"] = max(0.1, old_w - 0.3)
                                lowered += 1
                    if lowered > 0:
                        _save_memory(memory_data)
                    return f"状态:200, 拒绝模式已确认（{count}次），已降权 {lowered} 条相关记忆。"

                return f"状态:200, 行为已记录（第 {count} 次，需 3 次确认）。"
            else:
                # 新行为：创建记录
                new_counter = {
                    "id": _next_id(counter_data),
                    "pattern": pattern,
                    "category": category,
                    "count": 1,
                    "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "observing",
                    "related_memory_id": None
                }
                counter_data.append(new_counter)
                _save_counter(counter_data)
                return f"状态:200, 新行为模式已记录（第 1 次，需 3 次确认）。"

        # ================= 分支 6：时间条件检查 =================
        # 检查当前时间命中哪些时间条件记忆，返回应触发的提醒/拒绝清单
        # 启动时由 Call_Llm 调用，对话中 LLM 也可主动调用
        elif behavior == "check_time":
            now = datetime.now()
            hits = []
            for mem in memory_data:
                tc = mem.get("time_condition")
                if not tc:
                    continue
                if _match_time_condition(tc, now):
                    hits.append(mem)

            if not hits:
                return "状态:200, 当前时间无命中时间条件记忆。"

            remind_parts = []
            reject_parts = []
            for mem in hits:
                summary = _build_summary_text(mem)
                action = mem.get("time_action", "remind")
                if action == "reject":
                    reject_parts.append(f"- [拒绝] {summary}")
                else:
                    remind_parts.append(f"- [提醒] {summary}")

            result_parts = [f"状态:200, 当前时间命中 {len(hits)} 条时间条件记忆（{now.strftime('%Y-%m-%d %H:%M')}）:"]
            if remind_parts:
                result_parts.append("\n【到点提醒】")
                result_parts.extend(remind_parts)
            if reject_parts:
                result_parts.append("\n【到点拒绝】（相关请求应委婉拒绝）")
                result_parts.extend(reject_parts)
            return "\n".join(result_parts)

    except Exception as e:
        return f"状态:Error, 原因:{e}"
