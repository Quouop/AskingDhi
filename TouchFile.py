from datetime import datetime
import pyaudio
import wave
import time
import os
from asr import RecognizeLocalFile
from Call_Llm import StreamDialogue

class CaptureTimestampAndUserAudioSaveFilesTranscribeToText:
    def GetTime(self):
        CurrentTimeStr = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        return CurrentTimeStr

    def RecordingAndSaveFile(self, stop_event, chunk=1024, fmt=pyaudio.paInt16,
                             channels=1, rate=16000):
        """
        :param stop_event: threading.Event 对象，调用 .set() 时停止录音
        """
        # 自动创建file文件夹，防止路径不存在报错
        script_dir = os.path.dirname(os.path.abspath(__file__))
        save_folder = os.path.join(script_dir, "file")
        if not os.path.exists(save_folder):
            os.makedirs(save_folder)
        output_filename = os.path.join(save_folder, f"{self.GetTime()}.wav")

        p = pyaudio.PyAudio()
        stream = p.open(format=fmt,
                        channels=channels,
                        rate=rate,
                        input=True,
                        frames_per_buffer=chunk)

        print("开始录音... (按住空格录音，松开自动停止)")
        frames = []

        # 循环读取音频，松开空格stop_event.set() 跳出循环
        while not stop_event.is_set():
            data = stream.read(chunk, exception_on_overflow=False)
            frames.append(data)

        # 释放音频硬件资源
        stream.stop_stream()
        stream.close()
        p.terminate()

        # 写入wav音频文件
        wf = wave.open(output_filename, 'wb')
        wf.setnchannels(channels)
        wf.setsampwidth(p.get_sample_size(fmt))
        wf.setframerate(rate)
        wf.writeframes(b''.join(frames))
        wf.close()
        time.sleep(0.1)
        print(f"录音已保存，文件大小：{os.path.getsize(output_filename)} bytes")
        # ========== ASR识别（返回句子列表，含时间戳） ==========
        sentences = []
        try:
            sentences = RecognizeLocalFile(output_filename)
        except Exception as e:
            print(f"ASR识别异常: {e}")
            sentences = []
        if not sentences:
            print("识别结果为空，不触发对话")
            return

        # ========== 说话人声纹识别 ==========
        # 按每句时间戳切wav片段 → cam++提取声纹 → 与声纹库比对 → 新人自动命名Speaker_N
        # 失败时退化为"未知: 内容"，不阻塞对话
        annotated = []
        try:
            from HandleTool.speaker_voiceprint import annotate_sentences
            annotated = annotate_sentences(output_filename, sentences)
        except Exception as e:
            print(f"声纹识别异常(退化为未知说话人): {e}")
            annotated = [{"speaker": "未知", "text": s["text"],
                         "begin_ms": s.get("begin_ms", 0), "end_ms": s.get("end_ms", 0)}
                        for s in sentences]

        # 拼成 "人物: 内容\n人物: 内容" 喂给 StreamDialogue，LLM 即可知道谁说了什么
        dialogue_text = "\n".join(f"{a['speaker']}: {a['text']}" for a in annotated)
        print("识别结果：", dialogue_text)
        StreamDialogue(dialogue_text)