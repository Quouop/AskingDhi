from pynput import keyboard
import threading
import TouchFile as tf
from cleanup import clean_project
import subprocess
# 全局变量
recording_thread = None
stop_event = None
import os
import json
import zipfile
import tarfile
from pathlib import Path
from typing import Optional, Union

# 以下为可选依赖，使用前需 pip install
try:
    import py7zr
except ImportError:
    py7zr = None

try:
    import rarfile
except ImportError:
    rarfile = None


def ExtractAndMergeToolJson(archive_path: str, extract_to: Optional[str] = None) -> bool:
    """
    解压多种格式的压缩包（zip, tar, tar.gz, tar.bz2, 7z, rar），
    在其解压后的根目录查找以 .l.json 结尾的文件，
    读取 JSON 对象（字典）并追加到当前脚本所在目录的 tool_list.json 数组末尾。

    参数:
        archive_path (str): 压缩包文件路径。
        extract_to (str, optional): 解压目标目录。若为 None，则解压到压缩包所在目录下的同名文件夹。

    返回:
        bool: 成功返回 True，否则返回 False。
    """
    archive_path = Path(archive_path)
    if not archive_path.exists():
        print(f"错误: 压缩包不存在 -> {archive_path}")
        return False

    # 确定解压目标目录
    if extract_to is None:
        extract_to = archive_path.parent / archive_path.stem  # 注意：对于 .tar.gz 等，stem 会返回 "xxx.tar"，需特殊处理
        # 若扩展名为 .tar.gz，则更合理的是去掉 .tar.gz 而不仅是 .gz，但简化处理
    extract_to = Path(extract_to)
    extract_to.mkdir(parents=True, exist_ok=True)

    # 1. 解压部分 - 根据扩展名选择解压方法
    success = False
    suffix_lower = archive_path.suffix.lower()
    # 处理双重后缀（如 .tar.gz, .tar.bz2, .tgz）
    if archive_path.suffixes and len(archive_path.suffixes) >= 2:
        # 例如 .tar.gz，后缀列表为 ['.tar', '.gz']
        if archive_path.suffixes[-2].lower() == '.tar' and archive_path.suffixes[-1].lower() in ('.gz', '.bz2', '.xz'):
            # 使用 tarfile 打开
            try:
                with tarfile.open(archive_path, 'r:*') as tar:
                    tar.extractall(extract_to)
                success = True
                print(f"解压成功 (tar): {archive_path} -> {extract_to}")
            except Exception as e:
                print(f"tarfile 解压失败: {e}")
                return False
        else:
            # 其他双重后缀暂不处理，走单后缀逻辑
            pass
    else:
        # 单后缀
        if suffix_lower == '.zip':
            try:
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    zf.extractall(extract_to)
                success = True
                print(f"解压成功 (zip): {archive_path} -> {extract_to}")
            except Exception as e:
                print(f"zipfile 解压失败: {e}")
                return False

        elif suffix_lower in ('.tar', '.tgz', '.tbz2', '.txz'):
            try:
                with tarfile.open(archive_path, 'r:*') as tar:
                    tar.extractall(extract_to)
                success = True
                print(f"解压成功 (tar): {archive_path} -> {extract_to}")
            except Exception as e:
                print(f"tarfile 解压失败: {e}")
                return False

        elif suffix_lower == '.7z':
            if py7zr is None:
                print("错误: 解压 .7z 需要安装 py7zr，请执行: pip install py7zr")
                return False
            try:
                with py7zr.SevenZipFile(archive_path, mode='r') as sz:
                    sz.extractall(extract_to)
                success = True
                print(f"解压成功 (7z): {archive_path} -> {extract_to}")
            except Exception as e:
                print(f"py7zr 解压失败: {e}")
                return False

        elif suffix_lower == '.rar':
            if rarfile is None:
                print("错误: 解压 .rar 需要安装 rarfile，请执行: pip install rarfile")
                return False
            try:
                with rarfile.RarFile(archive_path) as rf:
                    rf.extractall(extract_to)
                success = True
                print(f"解压成功 (rar): {archive_path} -> {extract_to}")
            except Exception as e:
                print(f"rarfile 解压失败: {e}")
                return False

        else:
            print(f"错误: 不支持的压缩格式 '{suffix_lower}'")
            return False

    if not success:
        return False

    # 2. 查找根目录下的 .l.json 文件（只遍历根目录）
    ljson_files = list(extract_to.glob("*.l.json"))
    if not ljson_files:
        print(f"警告: 在 {extract_to} 根目录下未找到任何 .l.json 文件")
        return False

    target_file = ljson_files[0]  # 取第一个，可依需求修改
    print(f"找到 .l.json 文件: {target_file}")

    # 3. 读取 JSON 内容（必须是字典）
    try:
        with open(target_file, 'r', encoding='utf-8') as f:
            new_entry = json.load(f)
        if not isinstance(new_entry, dict):
            print(f"错误: {target_file} 内容不是 JSON 对象（字典），实际类型: {type(new_entry)}")
            return False
    except Exception as e:
        print(f"读取 {target_file} 失败: {e}")
        return False

    # 3.5 自动修正 ToolPath: 相对路径 → 绝对路径(相对于 .l.json 所在目录,即解压根目录)
    # 意图: 第三方工具 .l.json 里写相对路径(如 "mytool.py"),安装时自动替换为绝对路径
    rel_tool_path = new_entry.get("ToolPath")
    if rel_tool_path and isinstance(rel_tool_path, str):
        if os.path.isabs(rel_tool_path):
            # 已是绝对路径,保留(但提示开发者建议写相对路径)
            print(f"提示: ToolPath 已是绝对路径: {rel_tool_path}(建议 .l.json 里写相对路径,便于跨机器分发)")
        else:
            abs_tool_path = os.path.normpath(os.path.join(str(extract_to), rel_tool_path))
            if not os.path.exists(abs_tool_path):
                print(f"警告: 修正后的 ToolPath 文件不存在: {abs_tool_path}(相对路径: {rel_tool_path}),请确认工具文件已随压缩包解压")
            new_entry["ToolPath"] = abs_tool_path
            print(f"ToolPath 已自动修正为绝对路径: {rel_tool_path} -> {abs_tool_path}")
    else:
        print(f"警告: {target_file.name} 缺少 ToolPath 字段或类型错误,安装后可能无法调用")

    # 4. 读取/创建目标 tool_list.json（位于脚本目录）
    script_dir = Path(__file__).parent if '__file__' in globals() else Path.cwd()
    tool_list_path = script_dir / "tool_list.json"

    if tool_list_path.exists():
        try:
            with open(tool_list_path, 'r', encoding='utf-8') as f:
                tool_list = json.load(f)
            if not isinstance(tool_list, list):
                print(f"警告: {tool_list_path} 内容不是数组，将重置为空数组")
                tool_list = []
        except Exception as e:
            print(f"读取 {tool_list_path} 失败: {e}，将重新创建")
            tool_list = []
    else:
        tool_list = []

    # 5. 追加新条目
    tool_list.append(new_entry)

    # 6. 写回
    try:
        with open(tool_list_path, 'w', encoding='utf-8') as f:
            json.dump(tool_list, f, ensure_ascii=False, indent=4)
        print(f"成功追加工具条目到 {tool_list_path}")
        return True
    except Exception as e:
        print(f"写入 {tool_list_path} 失败: {e}")
        return False
def on_press(key):
    global recording_thread, stop_event
    if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r) and recording_thread is None:
        # 创建停止事件并启动录音线程
        stop_event = threading.Event()
        recorder = tf.CaptureTimestampAndUserAudioSaveFilesTranscribeToText()
        recording_thread = threading.Thread(target=recorder.RecordingAndSaveFile,
                                            args=(stop_event,))
        recording_thread.start()
    if key in (keyboard.Key.ctrl,keyboard.Key.ctrl_l,keyboard.Key.ctrl_r) and recording_thread is None:
        print("what way do you want to install tool?\n\tIs it a ***URL*** \nor\n\t a ***compressed archive***?()")
        if (WaysToDownload:=input().strip().lower()) in ['one','1','url','first']:
            subprocess.run(["git", "clone", "--depth", "1",input("\n\t└url(Full repository path**):")])
        elif WaysToDownload in ["two","2","compressed archive","second"]:
            ExtractAndMergeToolJson(input("FilePath(Don't any **Relative Path**):"))
        else:
            pass

def on_release(key):
    global recording_thread, stop_event                                                           
    if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r) and recording_thread is not None:
        # 发出停止信号，并等待线程结束
        stop_event.set()
        recording_thread.join()                               
        recording_thread = None
        stop_event = None
    # 按 ESC 退出监听
    if key == keyboard.Key.esc:
        return False

if __name__ == "__main__":
    print("[问渊AskingDhi] 系统已就绪。按住 Alt 键进行语音输入，按 ESC 键退出监听。")
    # 启动监听（非阻塞）
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    listener.join()  # 阻塞主线程直到监听结束
    
    # 退出监听后触发清理协议
    print("\n[问渊AskingDhi] 随时为您待命先生")
    clean_project()
