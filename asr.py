from http import HTTPStatus
import dashscope
from dashscope.audio.asr import Recognition
import os
import wave
import struct

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


def RecognizeLocalFile(file_path):
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

            # 条件1：时长过短，直接跳过ASR
            if duration < MIN_DURATION_SEC:
                print(f"[前置过滤]音频过短 {duration:.2f}s < {MIN_DURATION_SEC}s，跳过ASR调用")
                return ""

            # 只处理单声道16bit音频（你的录音输出）
            if channels != 1 or sampwidth != 2:
                print("[前置过滤]格式不符合预期，直接送入ASR")
            else:
                rms = calc_audio_rms(wf)
                print(f"音频RMS音量：{rms:.1f}")
                # 条件2：音量太低，几乎静音，跳过
                if rms < RMS_THRESHOLD:
                    print(f"[前置过滤]音量过低 RMS={rms:.1f} < {RMS_THRESHOLD}，跳过ASR调用")
                    return ""

        # 只有通过全部前置检查，才真正调用ASR接口
        recognition = Recognition(
            model='paraformer-realtime-v2',
            format='wav',
            sample_rate=16000,
            callback=None,
            max_sentence_silence=6000
        )
        result = recognition.call(file_path)
        if result.status_code == HTTPStatus.OK:
            text = result.get_sentence()
            return text if text else ""
        else:
            print(f'ASR接口报错: {result.message}')
            return ""
    except Exception as e:
        print(f"ASR调用异常：{str(e)}")
        return ""