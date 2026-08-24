#import subprocess
import os
import shlex
from hydra_sandbox import execute_python
import sys
import asyncio
from just_bash import Bash  # 导入 just-bash
import logging
from datetime import datetime
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 日志目录
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 日志文件路径（按天分割）
log_file = os.path.join(LOG_DIR, f"sandbox_{datetime.now().strftime('%Y%m%d')}.log")

# 配置 root logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()  # 也输出到控制台
    ]
)
logger = logging.getLogger(__name__)
def is_allowed(command: str) -> bool:
    # 纯静态黑名单检查，不执行任何东西
    if not command or not command.strip():
        return True
    parts = shlex.split(command)
    if not parts:
        return True
    cmd_name = os.path.basename(parts[0])
    BLACKLIST = {"rm", "shutdown", "dd", "mkfs", "curl", "wget"}
    return cmd_name not in BLACKLIST

def run_sandboxed(command: str, timeout: int = 10):
    """
    使用 just-bash 在纯Python模拟的Bash环境中执行命令。
    增加日志记录。
    """
    logger.info(f"执行命令: {command} (超时={timeout}s)")
    start_time = datetime.now()

    async def _exec():
        bash = Bash()
        result = await bash.exec(command)
        return result.stdout, result.stderr, result.exit_code

    try:
        stdout, stderr, exit_code = asyncio.run(
            asyncio.wait_for(_exec(), timeout=timeout)
        )
        elapsed = (datetime.now() - start_time).total_seconds()
        if exit_code == 0:
            logger.info(f"命令成功完成，耗时 {elapsed:.2f}s，返回码 0")
        else:
            logger.warning(f"命令执行失败，退出码 {exit_code}，耗时 {elapsed:.2f}s")
        if stderr:
            logger.warning(f"stderr: {stderr.strip()}")
        # 可选：如果 stdout 过长，只记录前200字符
        if stdout:
            logger.debug(f"stdout: {stdout.strip()[:200]}...")
        return stdout, stderr, exit_code
    except asyncio.TimeoutError:
        logger.error(f"命令超时 (>{timeout}秒): {command}")
        return "", f"命令执行超时 (>{timeout}秒)", -1
    except Exception as e:
        logger.exception(f"沙盒执行异常: {e}")  # 自动记录堆栈
        return "", f"Sandbox error: {e}", -1

def run_real(command: str, timeout: int = 30):
    """在真实主机上执行命令（shell=True）"""
    import subprocess
    logger.warning(f"真实环境执行命令: {command} (超时={timeout}s)")
    start_time = datetime.now()
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"真实命令完成，返回码 {result.returncode}，耗时 {elapsed:.2f}s")
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        logger.error(f"真实命令超时 (>{timeout}秒): {command}")
        return "", f"命令执行超时 (>{timeout}秒)", -1
    except Exception as e:
        logger.exception(f"真实执行异常: {e}")
        return "", f"执行异常: {e}", -1


def _format_output(stdout: str, stderr: str, code: int) -> str:
    """统一格式化命令输出"""
    content = f"错误码:{code}\n"
    if stdout:
        content += f"输出:\n{stdout}\n"
    if stderr:
        content += f"错误:\n{stderr}\n"
    return content


def run(params):
    command = params.get("command")
    if not command:
        return "状态:Error, 原因:缺少 command 参数"

    # shell 参数决定执行路径：False(默认)→沙箱，True→真实执行
    use_real_shell = bool(params.get("shell", False))

    # ----- 解析命令名，用于黑名单判断 -----
    try:
        cmd_parts = shlex.split(command)
        cmd_name = os.path.basename(cmd_parts[0]).lower() if cmd_parts else ""
    except ValueError:
        cmd_name = ""

    # 危险命令黑名单（真实执行时需要强警告）
    DANGEROUS = {"rm", "shutdown", "poweroff", "halt", "reboot", "dd", "mkfs", "format"}
    # 网络命令黑名单（真实执行时需要确认）
    NETWORK = {"curl", "wget"}

    if use_real_shell:
        # ============ 真实执行路径 ============
        is_dangerous = cmd_name in DANGEROUS
        is_network = cmd_name in NETWORK

        if is_dangerous:
            prompt = f"⚠️  危险命令 '{command}' 即将在真实主机执行！\n确认执行？(y/yes/是): "
        elif is_network:
            prompt = f"⚠️  网络命令 '{command}' 即将在真实主机执行。\n确认执行？(y/yes/是): "
        else:
            prompt = f"即将在真实主机执行：'{command}'\n确认执行？(y/yes/是): "

        confirm = input(prompt).strip().lower()
        if confirm not in {'y', 'yes', '是'}:
            logger.info(f"用户取消真实执行命令: {command}")
            return "⏹️ 执行已取消"

        logger.warning(f"用户确认真实执行命令: {command}")
        stdout, stderr, code = run_real(command)
        return _format_output(stdout, stderr, code)
    else:
        # ============ 沙箱执行路径 ============
        # 沙箱本身安全，黑名单命令也直接跑（沙箱会拦掉危险操作）
        stdout, stderr, code = run_sandboxed(command)
        return _format_output(stdout, stderr, code)