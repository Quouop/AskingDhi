"""
说话人声纹库：基于 cam++ 声纹模型做跨录音说话人识别。

链路位置：
  asr.RecognizeLocalFile(wav) → [{text, begin_ms, end_ms}, ...]
  → 本模块按每句 begin/end 从 wav 切片段 → cam++ 提取声纹向量
  → 与声纹库 JSON 比对余弦相似度 → 匹配=已知人 / 否则=新人自动命名 Speaker_N
  → 返回 [{speaker, text, begin_ms, end_ms}, ...]

模型文件统一放在 HandleTool/models/（MODELSCOPE_CACHE 指向此目录）。
声纹库 JSON = HandleTool/models/speaker_voiceprint.json，用户可直接编辑改标签名。
"""
import os
# 必须在 import funasr/modelscope 之前设置缓存目录，让模型下载到项目内
_HANDLETOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_MODELS_DIR = os.path.join(_HANDLETOOL_DIR, "models")
os.environ["MODELSCOPE_CACHE"] = _MODELS_DIR
os.environ["MODELSCOPE_HOME"] = _MODELS_DIR  # 避免 modelscope 访问 ~/.modelscope 被沙箱拦
os.makedirs(_MODELS_DIR, exist_ok=True)

import wave
import json
import time
import threading
import tempfile

# 声纹库文件
VOICEPRINT_DB_FILE = os.path.join(_MODELS_DIR, "speaker_voiceprint.json")

# 相似度阈值：cam++ 余弦相似度 >= 此值视为同一人。
# cam++ 同人通常 >0.8，不同人 <0.5；0.65 是稳妥的中间值，可调。
SIM_THRESHOLD = 0.65
# 最小片段时长(ms)：短于此值的句子声纹不准，沿用前一句的说话人
MIN_SEGMENT_MS = 500

_spk_model = None
_spk_lock = threading.Lock()
_db_lock = threading.Lock()


def _get_spk_model():
    """懒加载 cam++ 声纹模型（全局单例，CPU 推理）。"""
    global _spk_model
    if _spk_model is not None:
        return _spk_model
    with _spk_lock:
        if _spk_model is not None:
            return _spk_model
        from funasr import AutoModel
        _spk_model = AutoModel(model="cam++", device="cpu", disable_update=True)
        print("[声纹] cam++ 模型已加载")
        return _spk_model


def _load_db():
    """加载声纹库 JSON。返回 list[{label, name, embedding, sample_wav, first_seen, hit_count}]"""
    if not os.path.exists(VOICEPRINT_DB_FILE):
        return []
    try:
        with open(VOICEPRINT_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[声纹] 库读取失败: {e}")
        return []


def _save_db(db):
    """保存声纹库 JSON。"""
    try:
        with open(VOICEPRINT_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[声纹] 库保存失败: {e}")


def _slice_wav(wav_path, begin_ms, end_ms, out_path=None):
    """从 wav 按 begin_ms/end_ms（毫秒）切出片段，写到 out_path。返回 out_path。"""
    if out_path is None:
        out_path = tempfile.mktemp(suffix=".wav")
    with wave.open(wav_path, 'rb') as wf:
        framerate = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        total = wf.getnframes()
        start_frame = max(0, int(begin_ms / 1000 * framerate))
        end_frame = min(total, int(end_ms / 1000 * framerate)) if end_ms > 0 else total
        if end_frame <= start_frame:
            end_frame = min(total, start_frame + int(framerate * 0.5))
        wf.setpos(start_frame)
        frames = wf.readframes(end_frame - start_frame)
    with wave.open(out_path, 'wb') as out:
        out.setnchannels(n_channels)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)
        out.writeframes(frames)
    return out_path


def _extract_embedding(segment_path):
    """用 cam++ 从一段 wav 提取声纹向量。返回 list[float] 或 None。"""
    try:
        model = _get_spk_model()
        res = model.generate(input=segment_path)
        if not res:
            return None
        item = res[0]
        # cam++ 返回结构兼容多种 key
        for key in ("spk_embedding", "embedding", "spk_emb"):
            emb = item.get(key) if isinstance(item, dict) else None
            if emb is not None:
                import numpy as np
                # cam++ 返回 torch.Tensor，先转 numpy 再 flatten
                if hasattr(emb, "detach"):
                    emb = emb.detach().cpu().numpy()
                elif hasattr(emb, "numpy"):
                    emb = emb.numpy()
                arr = np.array(emb, dtype=np.float32).flatten()
                if arr.size > 0:
                    return arr.tolist()
        # 兜底：如果 item 本身就是向量
        if isinstance(item, (list, tuple)):
            import numpy as np
            arr = np.array(item, dtype=np.float32).flatten()
            if arr.size > 0:
                return arr.tolist()
        print(f"[声纹] 未能从 cam++ 返回提取 embedding，keys={list(item.keys()) if isinstance(item, dict) else type(item)}")
        return None
    except Exception as e:
        print(f"[声纹] embedding 提取异常: {e}")
        return None


def _cosine_sim(a, b):
    """余弦相似度。"""
    try:
        import numpy as np
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        na = np.linalg.norm(va)
        nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:
        return 0.0


def _match_in_db(emb, db):
    """在声纹库中找最高相似度匹配。返回 (best_name, best_sim, best_idx) 或 (None, 0, -1)。"""
    best_name, best_sim, best_idx = None, 0.0, -1
    for idx, entry in enumerate(db):
        ref = entry.get("embedding")
        if not ref:
            continue
        sim = _cosine_sim(emb, ref)
        if sim > best_sim:
            best_sim, best_name, best_idx = sim, entry.get("name") or entry.get("label"), idx
    return best_name, best_sim, best_idx


def _next_label(db):
    """生成下一个自动标签 Speaker_N。"""
    max_n = 0
    for entry in db:
        lbl = entry.get("label", "")
        if lbl.startswith("Speaker_"):
            try:
                max_n = max(max_n, int(lbl.split("_")[1]))
            except (ValueError, IndexError):
                pass
    return f"Speaker_{max_n + 1}"


def _register_new(emb, sample_wav):
    """注册新说话人，自动命名 Speaker_N。返回 name。"""
    db = _load_db()
    label = _next_label(db)
    entry = {
        "label": label,
        "name": label,  # 用户可改这个名字
        "embedding": emb,
        "sample_wav": sample_wav,
        "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hit_count": 0,
    }
    db.append(entry)
    _save_db(db)
    print(f"[声纹] 新说话人已注册: {label}（样本: {sample_wav}）")
    return label


def identify_speaker(wav_path, begin_ms, end_ms, sample_wav=None):
    """对 wav 中 begin_ms~end_ms 片段做说话人识别。

    返回 (speaker_name, is_new, similarity)。
    - 匹配到已知人 → (name, False, sim)
    - 新人 → 自动注册 Speaker_N → (label, True, 0.0)
    - 提取失败 → ("未知", False, 0.0)
    """
    seg_path = _slice_wav(wav_path, begin_ms, end_ms)
    emb = _extract_embedding(seg_path)
    try:
        os.unlink(seg_path)
    except OSError:
        pass
    if emb is None:
        return "未知", False, 0.0
    with _db_lock:
        db = _load_db()
        best_name, best_sim, best_idx = _match_in_db(emb, db)
        if best_name and best_sim >= SIM_THRESHOLD:
            db[best_idx]["hit_count"] = int(db[best_idx].get("hit_count", 0)) + 1
            _save_db(db)
            return best_name, False, best_sim
        # 新人
        name = _register_new(emb, sample_wav or wav_path)
        return name, True, 0.0


def annotate_sentences(wav_path, sentences):
    """给 ASR 返回的句子列表标注说话人。

    输入: [{text, begin_ms, end_ms}, ...]
    输出: [{speaker, text, begin_ms, end_ms}, ...]

    对每句切片段提取声纹比对。太短的句子沿用上一句的说话人（避免短片段声纹不准）。
    """
    result = []
    last_speaker = "未知"
    last_is_new = False
    for sent in sentences:
        begin = sent.get("begin_ms", 0)
        end = sent.get("end_ms", 0)
        seg_ms = end - begin if end > 0 else 0
        if seg_ms < MIN_SEGMENT_MS and seg_ms > 0:
            # 太短：沿用上一句说话人
            speaker = last_speaker
            is_new = False
            sim = 0.0
        else:
            speaker, is_new, sim = identify_speaker(wav_path, begin, end, sample_wav=wav_path)
            last_speaker = speaker
            last_is_new = is_new
        tag = "🆕新" if is_new else f"sim={sim:.2f}"
        print(f"  [{begin/1000:.1f}-{end/1000:.1f}s] {speaker}({tag}): {sent['text']}")
        result.append({
            "speaker": speaker,
            "text": sent["text"],
            "begin_ms": begin,
            "end_ms": end,
        })
    return result


def rename_speaker(old_name, new_name):
    """用户在声纹库文件里改标签名的便捷接口。"""
    with _db_lock:
        db = _load_db()
        changed = False
        for entry in db:
            if entry.get("name") == old_name or entry.get("label") == old_name:
                entry["name"] = new_name
                changed = True
        if changed:
            _save_db(db)
            print(f"[声纹] {old_name} → {new_name}")
        return changed


def list_speakers():
    """列出声纹库中所有已知说话人。"""
    db = _load_db()
    return [{"label": e.get("label"), "name": e.get("name"),
             "hit_count": e.get("hit_count", 0),
             "first_seen": e.get("first_seen")} for e in db]
