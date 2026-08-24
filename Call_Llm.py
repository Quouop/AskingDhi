from HandleTool.ParsingTool import *
from LoadSystemPrompy import replace_placeholders
import tomllib
from dashscope import MultiModalConversation
import dashscope
import os
import json
import time
import threading
import json
import shutil
import unicodedata
import re
# ========== 文件存储配置 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = os.path.join(SCRIPT_DIR, "memory")
HISTORY_FILE = os.path.join(MEMORY_DIR, "conversation_history.json")
with open("tool_list.json","r",encoding="utf-8") as f:
    full_tool_list = json.load(f)
builtin_only = [t for t in full_tool_list if t.get("is_builtin", False)]
TOOL_LIST = json.dumps(builtin_only, ensure_ascii=False, indent=4)

# 确保记忆目录存在
os.makedirs(MEMORY_DIR, exist_ok=True)

# ========== API 配置 ==========
with open("config.toml", "rb") as toml_file:
    config = tomllib.load(toml_file)
api_key = config["dashscopeApiKey"]
if api_key == "get":
    dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
else:
    dashscope.api_key = api_key
if not dashscope.api_key:
    raise ValueError("请设置环境变量 DASHSCOPE_API_KEY")

# ========== 全局变量 ==========
USER_NAME = "先生"
USER_ID = "托尼·斯塔克(Tony Stark)"
messages = []
full_response = ""

#=========== 提示词加载 ===========
SysPrompt = replace_placeholders("Prompt.askingdhi.md",USER_NAME,USER_ID,SCRIPT_DIR,TOOL_LIST) #你的原有逻辑加载md提示词
# ========== 长期记忆配置 ==========
LONG_TERM_MEMORY_FILE = os.path.join(MEMORY_DIR, "long_term_memory.json")

def _dedup_long_term_memory():
    """启动去重：移除 content 完全相同的记忆，保留最早的一条"""
    if not os.path.exists(LONG_TERM_MEMORY_FILE):
        return 0
    try:
        with open(LONG_TERM_MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list) or len(data) == 0:
            return 0

        seen = set()
        deduped = []
        removed = 0
        for mem in data:
            # 去重键：content + role 组合，完全一样才算重复
            key = (mem.get("content", ""), mem.get("role", ""))
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            deduped.append(mem)

        if removed > 0:
            with open(LONG_TERM_MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(deduped, f, ensure_ascii=False, indent=2)
            print(f"✅长期记忆去重：移除 {removed} 条完全相同的记录")
        return removed
    except Exception as e:
        print(f"⚠️ 长期记忆去重失败: {e}")
        return 0

def _load_core_memory(max_items=5):
    """加载核心记忆：权重>=0.5 或 状态非 done 的记忆
    - 用户习惯 (weight≈1.0) ✅ 注入
    - 未完成/重要事 (weight 0.5-1.0) ✅ 注入
    - 闲聊/已完成小事 (weight<0.5) ❌ 不注入
    """
    if not os.path.exists(LONG_TERM_MEMORY_FILE):
        return []
    try:
        with open(LONG_TERM_MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        # 筛选：权重>=0.5 或 状态非 done（未完成）
        core = [m for m in data
                if m.get("weight", 0.5) >= 0.5 or m.get("status") not in ("done", None)]
        # 按权重降序，取 top N
        core.sort(key=lambda x: x.get("weight", 0.5), reverse=True)
        return core[:max_items]
    except Exception as e:
        print(f"⚠️ 加载核心记忆失败: {e}")
        return []

def _format_core_memory_for_injection(core_memories):
    """把核心记忆格式化为 system 消息文本"""
    if not core_memories:
        return ""
    lines = ["[核心记忆 - AskingDhi长期记忆]"]
    for mem in core_memories:
        parts = []
        if mem.get("title"):
            parts.append(mem["title"])
        if mem.get("cause"):
            parts.append(f"起因:{mem['cause']}")
        if mem.get("process"):
            parts.append(f"经过:{mem['process']}")
        if mem.get("climax"):
            parts.append(f"高潮:{mem['climax']}")
        if mem.get("result"):
            parts.append(f"结果:{mem['result']}")
        if not parts and mem.get("content"):
            parts.append(mem["content"])
        status = mem.get("status", "unknown")
        weight = mem.get("weight", 0.5)
        lines.append(f"- (权重:{weight}, 状态:{status}) {' | '.join(parts)}")
    lines.append("[核心记忆结束]")
    return "\n".join(lines)

# ========== 历史管理函数 ==========
def load_history():
    global messages, SysPrompt


    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE,"r",encoding="utf-8") as f:
                loaded = json.load(f)
            # 【核心隔离】：只提取 role 和 content 给大模型
            # 权重、ID 等元数据留在磁盘里，绝不污染上下文
            messages = []
            for m in loaded:
                if m.get("role") in ("user", "assistant"):
                    messages.append({
                        "role": m["role"],
                        "content": m["content"]
                    })
        except Exception as e:
            print(f"读取历史异常:{e}，开启空白对话")
            messages = []
    else:
        messages = []

    # ✅强制：无论如何，内存messages[0]必须是system，绝不丢失！
    system_msg = {"role":"system","content":SysPrompt}
    # 如果第一条不是system，插到最前面
    if len(messages)==0 or messages[0]["role"] != "system":
        messages.insert(0, system_msg)

    # ✅启动注入核心记忆（权重>=0.5 或未完成），作为第二条 system 消息
    _dedup_long_term_memory()  # 先去重，再加载
    core_memories = _load_core_memory()
    if core_memories:
        core_text = _format_core_memory_for_injection(core_memories)
        if core_text:
            messages.insert(1, {"role": "system", "content": core_text})
            print(f"✅已注入 {len(core_memories)} 条核心记忆")

    # ✅启动检查时间条件记忆（到点提醒/到点拒绝），命中则注入为最前 system
    try:
        from HandleTool.memory import run as _memory_run
        time_result = _memory_run({"behavior": "check_time"})
        if time_result and "无命中" not in time_result:
            messages.insert(1, {"role": "system", "content": f"[时间条件提醒 - 当前时刻触发]\n{time_result}\n[请据此调整本次对话的行为：remind 项主动告知用户，reject 项相关请求委婉拒绝]"})
            print(f"⏰ 时间条件命中: {time_result.split(chr(10))[0]}")
    except Exception as e:
        print(f"⚠️ 时间条件检查失败: {e}")

    print(f"✅已加载会话，消息总数:{len(messages)}，第一条role={messages[0]['role']}")

def save_history():
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存历史失败: {e}")

def append_to_disk(role, content, weight=0.5):
    """
    向磁盘追加带有元数据的记忆，不依赖内存中的 messages
    """
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                disk_data = json.load(f)
        else:
            disk_data = []
            
        new_id = max((m.get("id", 0) for m in disk_data), default=0) + 1
        new_record = {
            "id": new_id,
            "role": role,
            "content": content,
            "weight": weight,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        disk_data.append(new_record)
        
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(disk_data, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        print(f"❌ 记忆落盘失败: {e}")


def reset_history():
    global messages
    messages = [{"role": "system", "content": SysPrompt}]
    save_history()
    print("对话历史已重置")

def _extract_keywords(text, extra_words=None):
    """
    从文本中提取关键词：中文(≥2字) + 英文(≥3字母)，去停用词，去重，小写。
    extra_words: 额外的关键词列表（比如 tags 单独传进来的）
    """
    import re
    # 基础停用词（极高频无意义词）
    stopwords = {"的", "了", "是", "在", "和", "与", "或", "也", "都", "就", "要", "会",
                 "可以", "我们", "你们", "他们", "这个", "那个", "什么", "怎么", "为什么",
                 "因为", "所以", "但是", "如果", "虽然", "然后", "已经", "还有", "以及",
                 "进行", "使用", "需要", "通过", "关于", "对于", "根据", "目前", "现在"}
    words = set()
    if text:
        # 中文：2字以上连续中文
        cn = re.findall(r'[\u4e00-\u9fa5]{2,}', text)
        for w in cn:
            wl = w.lower()
            if wl not in stopwords:
                words.add(wl)
        # 英文：3字母以上单词
        en = re.findall(r'[a-zA-Z]{3,}', text)
        for w in en:
            wl = w.lower()
            if wl not in stopwords:
                words.add(wl)
    if extra_words:
        for w in extra_words:
            if not w or not isinstance(w, str):
                continue
            wl = w.lower().strip()
            if len(wl) >= 2 and wl not in stopwords:
                words.add(wl)
    return words


def _pair_messages(non_system):
    """
    把非 system 消息按问答对分组，保证连贯性（A问B答绑定，不拆开）。
    返回 list of dict: [{"index": pair_idx, "user": msg|None, "assistant": msg|None, "combined": str}]
    """
    pairs = []
    i = 0
    while i < len(non_system):
        m = non_system[i]
        role = m.get("role")
        pair = {"index": len(pairs), "user": None, "assistant": None, "combined": ""}
        if role == "user":
            pair["user"] = m
            pair["combined"] = m.get("content", "") or ""
            # 看下一条是不是 assistant 回复
            if i + 1 < len(non_system) and non_system[i + 1].get("role") == "assistant":
                pair["assistant"] = non_system[i + 1]
                pair["combined"] += "\n" + (non_system[i + 1].get("content", "") or "")
                i += 2
            else:
                i += 1
        elif role == "assistant":
            # assistant 开头（可能是上一条被截断），单独成组
            pair["assistant"] = m
            pair["combined"] = m.get("content", "") or ""
            i += 1
        else:
            i += 1
            continue
        pairs.append(pair)
    return pairs


def _score_relevance(combined_text, keywords):
    """计算一段对话文本与关键词的匹配得分。命中≥1个关键词即认为相关。"""
    if not keywords or not combined_text:
        return 0
    text_lower = combined_text.lower()
    score = 0
    for kw in keywords:
        if kw and kw in text_lower:
            score += 1
    return score


def _compress_context(compress_params, keep_recent_pairs=2):
    """
    归档压缩（相关性清理版）：
      - 按总结关键词匹配对话，只删"连续2对以上相关"的区间
      - 以问答对为单位（保证A问B答连贯性，不拆开）
      - 孤立1对相关 → 不删（保证连续性）
      - 最近 keep_recent_pairs 对 + 最后1对 → 强制保留
    原始记录已在磁盘（append_to_disk 存的），不会丢失。
    """
    global messages
    if not compress_params:
        return False

    # -------- 1. 提取关键词 --------
    summary_text = compress_params.get("content") or compress_params.get("summary") or ""
    if not summary_text:
        return False

    parts_for_kw = [
        summary_text,
        compress_params.get("title", "") or "",
        compress_params.get("cause", "") or "",
        compress_params.get("process", "") or "",
        compress_params.get("climax", "") or "",
        compress_params.get("result", "") or "",
    ]
    tags = compress_params.get("tags", []) or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    keywords = _extract_keywords(" ".join(str(p) for p in parts_for_kw), extra_words=tags)

    if len(messages) <= keep_recent_pairs * 2 + 2:  # 太少不压缩（系统消息+至少2对对话）
        return False

    # -------- 2. 分离 system / 非 system --------
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= keep_recent_pairs * 2:
        return False

    # -------- 3. 按问答对分组（保证连贯性：A问B答绑定） --------
    pairs = _pair_messages(non_system)
    if len(pairs) <= keep_recent_pairs:
        return False

    # -------- 4. 逐对打分，标记是否相关 --------
    is_relevant = []
    for p in pairs:
        score = _score_relevance(p["combined"], keywords)
        is_relevant.append(score >= 1)  # ≥1个关键词命中 → 相关

    # -------- 5. 连续性过滤：只有连续≥2对相关才标记删除 --------
    #    规则：孤立1对相关（前后都不相关）→ 保留，不删
    to_delete = [False] * len(pairs)
    n = len(pairs)
    i_run = 0
    while i_run < n:
        if is_relevant[i_run]:
            # 找连续相关段的结束位置
            j_run = i_run
            while j_run + 1 < n and is_relevant[j_run + 1]:
                j_run += 1
            run_len = j_run - i_run + 1
            if run_len >= 2:
                # 连续≥2对 → 整段标记删除
                for k in range(i_run, j_run + 1):
                    to_delete[k] = True
            i_run = j_run + 1
        else:
            i_run += 1

    # -------- 6. 兜底保护：最近 N 对 + 最后1对 强制保留 --------
    force_keep_start = max(0, len(pairs) - keep_recent_pairs)
    for k in range(force_keep_start, len(pairs)):
        to_delete[k] = False
    if len(pairs) > 0:
        to_delete[-1] = False  # 最后1对（最新对话）无论如何保留

    deleted_count = sum(1 for d in to_delete if d)
    if deleted_count == 0:
        return False  # 没什么可删的，直接跳过

    # -------- 7. 组装：system 消息 + 归档摘要 + 未被删除的问答对 --------
    archive_msg = {"role": "system", "content": f"[已归档事件摘要]\n{summary_text}\n[如需细节请用 memory.search 查询]"}
    new_non_system = []
    for p, del_flag in zip(pairs, to_delete):
        if not del_flag:
            if p["user"] is not None:
                new_non_system.append({"role": p["user"]["role"], "content": p["user"]["content"]})
            if p["assistant"] is not None:
                new_non_system.append({"role": p["assistant"]["role"], "content": p["assistant"]["content"]})

    messages = system_msgs + [archive_msg] + new_non_system
    save_history()
    kept = len(pairs) - deleted_count
    print(f"✅上下文已压缩：删除 {deleted_count} 对相关对话，保留 {kept} 对（含最近 {keep_recent_pairs} 对兜底）；关键词数: {len(keywords)}")
    return True

# ========== 终端宽度 & 显示宽度 辅助函数 ==========
_ANSI_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')

def _get_term_width(fallback: int = 80) -> int:
    """获取终端列数，失败时返回 fallback。PowerShell 兼容。"""
    try:
        size = shutil.get_terminal_size(fallback=(fallback, 24))
        w = size.columns
        return w if w and w > 0 else fallback
    except Exception:
        return fallback

def _display_len(text: str) -> int:
    """
    计算字符串的**显示宽度**（不是字符数）：
    - 先剥掉 ANSI 颜色代码（它们不占显示宽度）
    - 全角字符（中文、日文等）算 2 列，半角字符算 1 列
    """
    if not text:
        return 0
    # 去掉 ANSI 转义序列
    plain = _ANSI_RE.sub('', text)
    w = 0
    for ch in plain:
        # unicodedata.east_asian_width: W/F=2列, N/Na/H/A=1列
        eaw = unicodedata.east_asian_width(ch)
        if eaw in ('W', 'F'):
            w += 2
        else:
            w += 1
    return w

def _flush_line(line_buffer: str) -> None:
    """把当前 line_buffer 作为完整的一行输出并换行，用于主动软换行。"""
    sys.stdout.write('\r')
    sys.stdout.write('\033[2K')
    sys.stdout.write(line_buffer)
    sys.stdout.write('\n')
    sys.stdout.flush()

# ========== 主对话函数 ==========
def StreamDialogue(text):
    global messages, full_response
    load_history()
    # 用户输入默认赋予 0.5 权重，直接落盘并追加到内存
    append_to_disk("user", text, weight=0.5)
    messages.append({"role": "user", "content": text})
    full_response = ""

    # ========== 循环检测状态 ==========
    _last_tool_signature = None  # 上一轮的工具调用签名
    _repeat_count = 0            # 连续相同调用计数
    _MAX_REPEAT = 2             # 连续相同调用超过此值强制 break
    _MAX_ROUNDS = 8             # 单次对话最大工具调用轮数
    _round_count = 0

    while True:
        _round_count += 1
        if _round_count > _MAX_ROUNDS:
            print(f"\n⚠️ 已达最大轮数 {_MAX_ROUNDS}，强制结束以防止死循环")
            break
        # ========== 每次循环重置状态 ==========
        answer_buffer = ""
        last_resp_msg = None
        reasoning_started = False

        # ========== 发起流式请求 ==========
        try:
            if len(messages) == 0 or messages[0]["role"] != "system":
                messages.insert(0, {"role": "system", "content": SysPrompt})

            responses = MultiModalConversation.call(
                model="qwen3.7-plus",
                messages=messages,
                result_format='message',
                stream=True,
                incremental_output=True,
                enable_thinking=True
            )

            for resp in responses:
                if resp.status_code != 200:
                    print(f"\n错误: {resp.code} - {resp.message}")
                    break
                msg = resp.output.choices[0].message
                last_resp_msg = msg

                # -------- 思考：实时灰色打印 --------
                if hasattr(msg, 'reasoning_content') and msg.reasoning_content:
                    reasoning = msg.reasoning_content
                    if isinstance(reasoning, str):
                        chunk = reasoning
                    elif isinstance(reasoning, list):
                        chunk = ''.join(item.get('text', '') if isinstance(item, dict) else str(item) for item in reasoning)
                    else:
                        chunk = str(reasoning)
                    if chunk:
                        if not reasoning_started:
                            print("\033[90m[思考] \033[0m", end="", flush=True)
                            reasoning_started = True
                        print(f"\033[90m{chunk}\033[0m", end="", flush=True)

                # -------- 正文：只收集，不打印 --------
                content = msg.content
                chunk_text = ""
                if content:
                    if isinstance(content, str):
                        chunk_text = content
                    elif isinstance(content, list):
                        chunk_text = ''.join(item.get('text', '') if isinstance(item, dict) else str(item) for item in content)
                    else:
                        chunk_text = str(content)
                if chunk_text.strip():
                    answer_buffer += chunk_text

            print()  # 思考内容换行

            if not answer_buffer.strip() and last_resp_msg is not None:
                final_content = last_resp_msg.content
                if final_content:
                    if isinstance(final_content, list):
                        answer_buffer = ''.join(i.get("text", "") for i in final_content)
                    else:
                        answer_buffer = final_content

            full_response = answer_buffer

        except Exception as e:
            print(f"\n❌ 请求异常: {e}")
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            break

        print("\033[90m----------------------------------------\033[0m")
        LABEL = "AskingDhi："
        CURSOR_LOGO = "[AskingDhi]"
        DRAGON_CHARS = ['-', '^', '\\', '|', '$', '&', '^', '|', '.']
        CURSOR_WIDTH = _display_len(CURSOR_LOGO)
        SAFETY_MARGIN = 2  # 预留几列，防止卡边界

        line_buffer = ""  # 只保存真实输出文本！不含光标标记
        dragon_idx = 0
        i = 0
        CurrentToolSeq = 0

        def _check_and_soft_wrap(buffer: str, extra_suffix_width: int = 0) -> str:
            """
            检查 buffer + 后缀预留宽度 是否超过终端宽度。
            超了就主动整行 flush 换行，返回空字符串（新的一行 buffer）。
            没超就原样返回 buffer。
            """
            term_w = _get_term_width()
            max_buf_w = term_w - extra_suffix_width - SAFETY_MARGIN
            if max_buf_w <= 0:
                return buffer
            if _display_len(buffer) > max_buf_w:
                _flush_line(buffer)
                return ""
            return buffer

        def _split_for_line(text: str, max_width: int) -> list:
            """把一段文本按显示宽度拆成若干段，每段 <= max_width。"""
            if max_width <= 0:
                return [text]
            parts = []
            cur = ""
            cur_w = 0
            for ch in text:
                ch_w = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
                if cur and cur_w + ch_w > max_width:
                    parts.append(cur)
                    cur = ch
                    cur_w = ch_w
                else:
                    cur += ch
                    cur_w += ch_w
            if cur:
                parts.append(cur)
            return parts

        while i < len(full_response):
            ch = full_response[i]

            # ========== 1. 处理 JSON 块及腾龙动画 ==========
            if ch == '{':
                temp_seq = CurrentToolSeq + 1
                NowToolName = ParseTool().GetToolNameByIndex(full_response, temp_seq)
                if NowToolName:
                    CurrentToolSeq = temp_seq

                brace_count = 1
                i += 1
                json_block = '{'

                # 解析整个 JSON 块
                while i < len(full_response) and brace_count > 0:
                    c2 = full_response[i]
                    json_block += c2
                    if c2 == '{':
                        brace_count += 1
                    elif c2 == '}':
                        brace_count -= 1

                    c = DRAGON_CHARS[dragon_idx % len(DRAGON_CHARS)]
                    if NowToolName:
                        hint_text = f" AskingDhi is using the {NowToolName}"
                    else:
                        hint_text = ""

                    # ---------- 动画阶段也要防超宽：超了就截断 hint ----------
                    term_w = _get_term_width()
                    anim_full = line_buffer + '[' + c + ']' + hint_text
                    if _display_len(anim_full) > term_w - SAFETY_MARGIN:
                        # 优先截断 hint，再不行就只保留 line_buffer
                        anim_suffix_w = _display_len('[' + c + ']')
                        max_hint_w = term_w - SAFETY_MARGIN - _display_len(line_buffer) - anim_suffix_w
                        if max_hint_w > 3:
                            hint_plain = hint_text
                            cut_hint = ""
                            cut_w = 0
                            for hc in hint_plain:
                                hw = 2 if unicodedata.east_asian_width(hc) in ('W', 'F') else 1
                                if cut_w + hw > max_hint_w - 3:
                                    break
                                cut_hint += hc
                                cut_w += hw
                            hint_text = cut_hint + "..."
                        else:
                            hint_text = ""
                        anim_full = line_buffer + '[' + c + ']' + hint_text

                    sys.stdout.write('\r' + '\033[2K' + anim_full)
                    sys.stdout.flush()
                    dragon_idx += 1
                    time.sleep(0.04)
                    i += 1

                # ---------- JSON块结束：按宽度拆成多行，避免终端强制换行 ----------
                term_w = _get_term_width()
                max_w = term_w - CURSOR_WIDTH - SAFETY_MARGIN
                json_parts = _split_for_line(json_block, max(max_w, 10))

                for pi, part in enumerate(json_parts):
                    if pi > 0:
                        # 后续分段：必须是新的一行
                        if line_buffer:
                            _flush_line(line_buffer)
                            line_buffer = ""
                    # 检查当前 line_buffer + 本段 会不会超
                    if _display_len(line_buffer + part) > max_w and line_buffer:
                        _flush_line(line_buffer)
                        line_buffer = ""
                    line_buffer += part

                # 渲染：真实文本 + 临时光标logo
                line_buffer = _check_and_soft_wrap(line_buffer, CURSOR_WIDTH)
                sys.stdout.write('\r' + '\033[2K' + line_buffer + CURSOR_LOGO)
                sys.stdout.flush()
                continue

            # ========== 2. 处理换行符 ==========
            if ch == '\n':
                sys.stdout.write('\r' + '\033[2K' + line_buffer + '\n')
                sys.stdout.flush()
                line_buffer = ""
                i += 1
                continue
            
            # ========== 3.普通字符输出 ----------
            # 先判断加了这个字符+光标后会不会超；超了就先换行
            if _display_len(line_buffer + ch) + CURSOR_WIDTH + SAFETY_MARGIN > _get_term_width():
                if line_buffer:
                    _flush_line(line_buffer)
                    line_buffer = ""
            line_buffer += ch
            # 临时渲染，line_buffer不掺杂光标标记
            sys.stdout.write('\r' + '\033[2K' + line_buffer + CURSOR_LOGO)
            sys.stdout.flush()

            time.sleep(0.018)
            i += 1

        # ========== 最终收尾：直接输出纯净line_buffer，无任何光标标记 ==========
        if line_buffer:
            sys.stdout.write('\r')
            sys.stdout.write('\033[2K')
            sys.stdout.write(line_buffer)
            sys.stdout.write('\n')
            sys.stdout.flush()
        # ========== 检查是否有工具调用 ==========
        ToolCalls = ParseTool().ExtractAllJsonFromText(full_response)
        ValidTools = [t for t in ToolCalls if isinstance(t, dict) and t.get("name")]

        # 【核心拦截】：检查是否包含 memory(save) 工具
        memory_tool = None
        for t in ValidTools:
            if t.get("name") == "memory" and t.get("parameters", {}).get("behavior") == "save":
                memory_tool = t
                break

        # ========== 保存助手回复 ==========
        if full_response.strip():
            # 如果大模型主动输出了 memory 工具，提取其评估的权重；否则默认 0.5
            weight = 0.5
            if memory_tool:
                weight = float(memory_tool.get("parameters", {}).get("weight", 0.5))
            
            # 1. 带权重落盘
            append_to_disk("assistant", full_response, weight=weight)
            # 2. 纯净追加到内存
            messages.append({"role": "assistant", "content": full_response})
        else:
            print("\n⚠️ 警告: 助手返回空回复")
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            break

        if not ValidTools:
            break

        # ========== 循环检测：连续相同工具调用 ==========
        # 生成本轮工具签名（name + 关键参数），用于判断是否在重复同一操作
        _current_sig = tuple(
            (t.get("name"), json.dumps(t.get("parameters", {}), sort_keys=True))
            for t in ValidTools
        )
        if _current_sig == _last_tool_signature:
            _repeat_count += 1
            print(f"\n⚠️ 检测到重复工具调用（第 {_repeat_count} 次）")
            if _repeat_count >= _MAX_REPEAT:
                print(f"⚠️ 连续重复已达上限 {_MAX_REPEAT}，强制结束以防止死循环")
                # 给 LLM 一个提示，让它用自然语言收尾
                messages.append({
                    "role": "user",
                    "content": "[系统提示] 你刚才连续重复调用了相同的工具，这可能是一个循环。请基于已有信息用自然语言回答用户，不要再调用工具。"
                })
                break
        else:
            _repeat_count = 0
            _last_tool_signature = _current_sig

        # ========== 执行工具（静默，只打印完成提示） ==========
        result_list = ParseTool().ParseAllLLMOutput(full_response,"MainAgent")

        # 【归档压缩检测】：检查是否调用了 memory.compress，触发上下文清理
        for t in ValidTools:
            if t.get("name") == "memory" and t.get("parameters", {}).get("behavior") == "compress":
                compress_params = t.get("parameters", {}) or {}
                if compress_params.get("content") or compress_params.get("summary"):
                    _compress_context(compress_params, keep_recent_pairs=2)
                break

        # 【静默剔除】：将 memory 工具从执行结果中移除，不污染上下文
        result_list = [r for r in result_list if r.get("name") != "memory"]
        # 去重：相同name+相同参数字典视为重复调用
        seen = set()
        dedup_result = []
        for item in result_list:
            key = (item["name"], json.dumps(item["output"], sort_keys=True))
            if key not in seen:
                seen.add(key)
                dedup_result.append(item)
        result_list = dedup_result
        for tr in result_list:
            messages.append({
                "role": "user",
                "content": f"[工具 {tr['name']} 执行结果]\n{tr['output']}"
            })
        save_history()

        # 继续下一轮循环
