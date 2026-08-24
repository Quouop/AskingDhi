import os
import shutil
from pathlib import Path

def clean_project():
    """
    AskingDhi 自动清理脚本
    用于清除项目中的 __pycache__ 目录与 file/ 目录下的 .wav 音频文件
    """
    project_root = Path(__file__).parent
    
    print("[AskingDhi] 正在启动系统清理协议...\n")
    
    # 1. 清理 __pycache__ 目录
    pycache_count = 0
    for dirpath, dirnames, filenames in os.walk(project_root):
        # 避免进入虚拟环境目录，以免误删依赖
        if ".venv" in dirpath.split(os.sep):
            continue
        for dirname in dirnames:
            if dirname == "__pycache__":
                target_path = os.path.join(dirpath, dirname)
                try:
                    shutil.rmtree(target_path)
                    pycache_count += 1
                    print(f"[清理] 移除缓存目录: {target_path}")
                except Exception as e:
                    print(f"[警告] 无法删除 {target_path}: {e}")
                    
    # 2. 清理 file/ 目录下的 .wav 音频文件
    audio_dir = project_root / "file"
    wav_count = 0
    if audio_dir.exists() and audio_dir.is_dir():
        for wav_file in audio_dir.glob("*.wav"):
            try:
                wav_file.unlink()
                wav_count += 1
                print(f"[清理] 移除音频文件: {wav_file.name}")
            except Exception as e:
                print(f"[警告] 无法删除 {wav_file}: {e}")
    else:
        print("[提示] 未找到 file/ 目录，跳过音频清理。")
        
    print(f"\n[AskingDhi] 清理协议执行完毕。")
    print(f"[统计] 共移除 {pycache_count} 个缓存目录，{wav_count} 个音频文件。")
    print("[状态] 系统目录已恢复整洁，随时待命。")

if __name__ == "__main__":
    clean_project()
