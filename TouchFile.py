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
        # ========== ASR识别 ==========
        sentence = ""
        try:
            result = RecognizeLocalFile(output_filename)

            if isinstance(result, dict):
                # 优先取顶层 text
                sentence = result.get("text", "")
                # 如果顶层 text 为空或不存在，尝试从 words 拼接
                if not sentence and "words" in result:
                    words_list = result["words"]
                    # 拼接每个 word 的 text + punctuation
                    sentence = ''.join(w.get("text", "") + w.get("punctuation", "") for w in words_list)
            elif isinstance(result, list):
                sentence = result[0] if result else ""
            elif isinstance(result, str):
                sentence = result
            else:
                sentence = ""
        except Exception as e:
            print(f"ASR识别异常: {e}")
            sentence = ""
        if isinstance(sentence, dict):
            sentence = sentence.get("text", "")
        if sentence and sentence.strip():
            print("识别结果：", sentence)
            StreamDialogue(sentence)
        else:
            print("识别结果为空，不触发对话")