from http import HTTPStatus
import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback
import os
import wave
import struct
import threading

dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

# ---------------- 可调节阈值 ----------------
MIN_DURATION_SEC = 0.3       # 最小音频时长，小于该秒判定为太短，跳过
RMS_THRESHOLD = 250.0        # RMS音量阈值，根据你的录音设备微调；值越大越严格
SAMPLE_RATE_EXPECT = 16000

def calc_audio_rms(wf: wave.Wave_read) -> float:
    """
    计算16bit PCM wav的RMS均方根能量，表征响度
    只支持16位深度；单声道
    """
    n_frames = wf.getnframes()
    wf.rewind()
    frames = wf.readframes(n_frames)
    sample_width = wf.getsampwidth()

    if sample_width != 2:
        return 0.0

    count = len(frames) // 2
    sum_sq = 0.0
    # unpack 16‑bit little‑endian short
    for i in range(count):
        val = struct.unpack_from("<h", frames, i * 2)[0]
        sum_sq += val * val
    rms = (sum_sq / count) ** 0.5
    return rms


class _SentenceCollector(RecognitionCallback):
    """收集 ASR 识别出的所有句子（含时间戳）。

    paraformer-realtime-v2 的 Recognition 在文件模式下会逐句回调，
    每次 sentence_end=True 时 get_sentence() 返回该句的完整 dict：
      {text, begin_time(毫秒), end_time(毫秒), words:[{text,begin_time,end_time,punctuation}]}
    本类把所有句子收集成统一列表，供上层按时间戳切片做声纹比对。
    """
    def __init__(self):
        self.sentences = []
        self._lock = threading.Lock()

    def on_event(self, result):
        try:
            if not result.is_sentence_end():
                return
            sent = result.get_sentence()
            if isinstance(sent, dict):
                text = sent.get("text", "") or ""
                begin = sent.get("begin_time", 0) or 0
                end = sent.get("end_time", 0) or 0
            elif isinstance(sent, str):
                text, begin, end = sent, 0, 0
            else:
                text, begin, end = str(sent), 0, 0
            if text and text.strip():
                with self._lock:
                    self.sentences.append({
                        "text": text.strip(),
                        "begin_ms": int(begin),
                        "end_ms": int(end),
                    })
        except Exception as e:
            print(f"[ASR callback] 解析句子异常: {e}")

    def on_complete(self):
        pass


def RecognizeLocalFile(file_path):
    """识别本地 wav，返回句子列表 [{text, begin_ms, end_ms}, ...]。

    - 保留原有前置过滤：时长过短 / 音量过低 直接返回空列表
    - 用 callback 收集每句的 text + begin_ms + end_ms（毫秒）
    - 如果 callback 收集为空，兜底从最终 result 取单句
    """
    try:
        with wave.open(file_path, 'rb') as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()

            print(f"实际音频格式: channels={channels}, "
                  f"sampwidth={sampwidth}, framerate={framerate}, "
                  f"nframes={nframes}")

            duration = nframes / framerate
            print(f"音频时长: {duration:.2f} s")

            if duration < MIN_DURATION_SEC:
                print(f"[前置过滤]音频过短 {duration:.2f}s < {MIN_DURATION_SEC}s，跳过ASR调用")
                return []

            if channels != 1 or sampwidth != 2:
                print("[前置过滤]格式不符合预期，直接送入ASR")
            else:
                rms = calc_audio_rms(wf)
                print(f"音频RMS音量：{rms:.1f}")
                if rms < RMS_THRESHOLD:
                    print(f"[前置过滤]音量过低 RMS={rms:.1f} < {RMS_THRESHOLD}，跳过ASR调用")
                    return []

        collector = _SentenceCollector()
        recognition = Recognition(
            model='paraformer-realtime-v2',
            format='wav',
            sample_rate=16000,
            callback=collector,
            max_sentence_silence=6000
        )
        result = recognition.call(file_path)
        if result.status_code == HTTPStatus.OK:
            if collector.sentences:
                print(f"[ASR] 识别到 {len(collector.sentences)} 个句子")
                return collector.sentences
            # 兜底：callback 没收到句子，从最终 result 取
            text = result.get_sentence()
            if isinstance(text, dict):
                t = text.get("text", "")
                if t and t.strip():
                    return [{"text": t.strip(),
                             "begin_ms": int(text.get("begin_time", 0) or 0),
                             "end_ms": int(text.get("end_time", 0) or 0)}]
            elif isinstance(text, str) and text.strip():
                return [{"text": text.strip(), "begin_ms": 0, "end_ms": 0}]
            return []
        else:
            print(f'ASR接口报错: {result.message}')
            return []
    except Exception as e:
        print(f"ASR调用异常：{str(e)}")
        return []
