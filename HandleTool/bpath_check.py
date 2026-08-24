"""
.bpath 黑名单检查：文件操作类工具在访问路径前，统一调用本模块。
.bpath 规则（每行一条，# 开头为注释，空行忽略）：
    memory/           # 以 / 结尾 = 目录，目录内所有文件/子目录均禁止
    config.toml       # 精确文件名（相对 PROJECT_PATH）
    secrets/*.key     # 支持 * 和 ** 通配（用 fnmatch）
路径统一解析为绝对路径后，与 .bpath 中的规则比较。
"""
from pathlib import Path
import fnmatch

BPATH_FILE = Path(__file__).parent.parent / ".bpath"
PROJECT_PATH = Path(__file__).parent.parent.resolve()

# 缓存（避免每次工具调用都读文件；进程内单例）
_rules_cache = None
_cache_key = None  # (文件mtime, 文件大小)


def _read_bpath():
    """读取 .bpath 并解析为 (dir_rules, file_rules, glob_rules)"""
    global _rules_cache, _cache_key

    if not BPATH_FILE.exists():
        _rules_cache = (set(), set(), [])
        _cache_key = (None, None)
        return _rules_cache

    stat = BPATH_FILE.stat()
    cache_key = (stat.st_mtime, stat.st_size)
    if _cache_key == cache_key and _rules_cache is not None:
        return _rules_cache

    dir_rules = set()    # 目录黑名单（Path 对象，绝对路径）
    file_rules = set()   # 文件黑名单（Path 对象，绝对路径）
    glob_rules = []      # 通配符规则（str，相对路径，含 * 或 ?）

    with open(BPATH_FILE, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # 目录规则：以 / 结尾（Windows 下也统一接收）
            if line.endswith("/") or line.endswith("\\"):
                dir_name = line.rstrip("/\\")
                abs_dir = (PROJECT_PATH / dir_name).resolve()
                dir_rules.add(str(abs_dir))
                continue

            # 含通配符 → glob 规则
            if "*" in line or "?" in line:
                glob_rules.append(line)
                continue

            # 否则：精确文件规则
            abs_file = (PROJECT_PATH / line).resolve()
            file_rules.add(str(abs_file))

    _rules_cache = (dir_rules, file_rules, glob_rules)
    _cache_key = cache_key
    return _rules_cache


def check_path(path, tool_name: str = None) -> str:
    """
    检查单个路径是否命中黑名单。
    返回空字符串 "" = 放行；非空 = 错误信息（直接返回给用户的状态文本）。
    """
    try:
        target = Path(path).resolve()
    except Exception:
        # 路径解析失败，交给具体工具后续处理报错
        return ""

    target_str = str(target)
    dir_rules, file_rules, glob_rules = _read_bpath()

    # 1. 精确文件匹配
    if target_str in file_rules:
        rel = _rel_display(target)
        extra = f"（工具: {tool_name}）" if tool_name else ""
        return f"状态:Error, 原因:路径 '{rel}' 在 .bpath 黑名单中，禁止访问{extra}"

    # 2. 目录/子目录匹配
    for d in dir_rules:
        # target 是目录本身或其子项
        try:
            target.relative_to(Path(d))
            rel_target = _rel_display(target)
            rel_dir = _rel_display(Path(d))
            extra = f"（工具: {tool_name}）" if tool_name else ""
            return f"状态:Error, 原因:路径 '{rel_target}' 位于黑名单目录 '{rel_dir}' 下，禁止访问{extra}"
        except ValueError:
            continue

    # 3. glob 通配符匹配
    try:
        rel_path = target.relative_to(PROJECT_PATH).as_posix()
    except ValueError:
        rel_path = None

    if rel_path is not None:
        for pattern in glob_rules:
            pat_posix = pattern.replace("\\", "/")
            if fnmatch.fnmatch(rel_path, pat_posix):
                extra = f"（工具: {tool_name}）" if tool_name else ""
                return f"状态:Error, 原因:路径 '{rel_path}' 匹配 .bpath 通配规则 '{pattern}'，禁止访问{extra}"

    return ""


def filter_files(file_list, tool_name: str = None):
    """
    过滤文件列表：返回 (allowed_list, blocked_messages)。
    allowed_list 是未命中的文件 Path 对象；
    blocked_messages 是被拦的描述列表（供调用方追加到返回结果末尾）。
    """
    allowed = []
    blocked = []
    for fp in file_list:
        msg = check_path(str(fp), tool_name)
        if msg:
            # 提取路径名用于展示
            try:
                rel = _rel_display(Path(fp))
            except Exception:
                rel = str(fp)
            blocked.append(rel)
        else:
            allowed.append(fp)
    return allowed, blocked


def _rel_display(p: Path) -> str:
    """尽量显示相对于项目根的路径，更易读"""
    try:
        rel = p.resolve().relative_to(PROJECT_PATH).as_posix()
        return rel
    except ValueError:
        return str(p.resolve())
