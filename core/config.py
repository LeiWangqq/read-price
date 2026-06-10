"""本地配置读写：保存大模型连接信息到项目目录下（便携模式）。"""
from __future__ import annotations

import hashlib
import json
import pickle
from datetime import datetime
from pathlib import Path

# 配置和缓存存储在项目根目录的 .read_price 文件夹内
CONFIG_DIR = Path(__file__).resolve().parent.parent / ".read_price"
CONFIG_FILE = CONFIG_DIR / "config.json"
_DEFAULT_CACHE_DIR = CONFIG_DIR / "cache"
CACHE_DIR = _DEFAULT_CACHE_DIR  # 可被 set_cache_dir() 覆盖

_DEFAULT = {"base_url": "", "api_key": "", "model": "", "cache_dir": ""}


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            merged = {**_DEFAULT, **data}
            # 恢复用户自定义缓存目录
            if merged.get("cache_dir"):
                global CACHE_DIR
                CACHE_DIR = Path(merged["cache_dir"])
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
            return merged
        except (json.JSONDecodeError, OSError):
            return dict(_DEFAULT)
    return dict(_DEFAULT)


def save_config(base_url: str, api_key: str, model: str,
                cache_dir: str = "") -> None:
    ensure_dirs()
    payload = {
        "base_url": base_url.strip(),
        "api_key": api_key.strip(),
        "model": model.strip(),
        "cache_dir": cache_dir.strip(),
    }
    CONFIG_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def set_cache_dir(path: str) -> None:
    """动态切换缓存目录（用于 UI 选择外部缓存）。"""
    global CACHE_DIR
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    CACHE_DIR = p


def reset_cache_dir() -> None:
    """恢复为默认缓存目录。"""
    global CACHE_DIR
    CACHE_DIR = _DEFAULT_CACHE_DIR
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("base_url") and cfg.get("api_key") and cfg.get("model"))


# 缓存版本号，block 格式变更时递增，自动使旧缓存失效
_CACHE_VERSION = 10


def cache_path(file_path: Path, session_dir: Path | None = None) -> Path:
    """根据文件路径和修改时间生成缓存文件路径。session_dir 指定写入哪个会话文件夹。"""
    target_dir = session_dir or CACHE_DIR
    mtime = file_path.stat().st_mtime_ns
    key = f"{file_path.resolve()}|{mtime}|v{_CACHE_VERSION}"
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return target_dir / f"{digest}.pkl"


_SUPPORTED = (".pdf", ".docx", ".doc")


def load_cached_blocks_for(path: str, session_dir: Path | None = None) -> list[dict]:
    """加载指定文件或文件夹下所有已缓存的 blocks。

    session_dir: 指定在哪个会话文件夹中搜索（None 则搜索 CACHE_DIR）。
    搜索策略：遍历缓存 pkl 文件，检查内部 block 的 path 字段是否匹配。
    先尝试路径匹配，再按文件名匹配（处理 .doc 转 .docx 临时路径的情况）。
    """
    target = Path(path).resolve()
    search_dir = session_dir or CACHE_DIR
    if not search_dir.exists():
        return []

    # 如果目标就是搜索目录本身，直接加载全部缓存
    if target == search_dir.resolve():
        result: list[dict] = []
        for pkl_file in search_dir.glob("*.pkl"):
            try:
                result.extend(pickle.loads(pkl_file.read_bytes()))
            except (pickle.PickleError, OSError, IndexError):
                continue
        return result

    def _matches(blocks: list[dict]) -> bool:
        if not blocks:
            return False
        src = Path(blocks[0].get("path", "")).resolve()
        block_file = blocks[0].get("file", "")
        if target.is_file():
            if src == target:
                return True
            # 按文件名匹配（处理 .doc 转 .docx 临时路径的情况）
            if block_file == target.name:
                return True
            return src.name == target.name and src.parent == target.parent
        # 文件夹：检查源路径是否在目标文件夹下，或文件名属于该文件夹
        try:
            src.relative_to(target)
            return True
        except ValueError:
            pass
        # 按文件名匹配
        try:
            folder_files = {f.name for f in target.iterdir()}
            return block_file in folder_files
        except (OSError, PermissionError):
            return False

    result: list[dict] = []
    for pkl_file in search_dir.glob("*.pkl"):
        try:
            cached = pickle.loads(pkl_file.read_bytes())
            if _matches(cached):
                result.extend(cached)
        except (pickle.PickleError, OSError, IndexError):
            continue
    return result


def cache_stats() -> dict:
    """返回当前缓存目录的统计信息：{count, total_bytes, cache_dir}。"""
    if not CACHE_DIR.exists():
        return {"count": 0, "total_bytes": 0, "cache_dir": str(CACHE_DIR)}
    pkl_files = list(CACHE_DIR.glob("*.pkl"))
    total = sum(f.stat().st_size for f in pkl_files)
    return {
        "count": len(pkl_files),
        "total_bytes": total,
        "cache_dir": str(CACHE_DIR),
    }


def clear_stale_cache(blocks: list[dict]) -> int:
    """删除缓存中源文件已不存在的条目。返回删除的缓存文件数。"""
    import pickle

    if not CACHE_DIR.exists():
        return 0

    # 收集当前 blocks 中所有有效源文件路径
    active_paths: set[str] = set()
    for b in blocks:
        p = b.get("file", "")
        if p:
            active_paths.add(str(Path(p).resolve()))

    removed = 0
    for pkl_file in CACHE_DIR.glob("*.pkl"):
        try:
            cached = pickle.loads(pkl_file.read_bytes())
            if not cached:
                pkl_file.unlink(missing_ok=True)
                removed += 1
                continue
            # 取第一个 block 的文件路径判断源文件是否存在
            src = cached[0].get("file", "")
            if src and not Path(src).exists():
                pkl_file.unlink(missing_ok=True)
                removed += 1
        except (pickle.PickleError, OSError, IndexError):
            pkl_file.unlink(missing_ok=True)
            removed += 1
    return removed


# ─── 时间戳会话管理 ────────────────────────────────────────────


def new_cache_session() -> Path:
    """创建新的时间戳缓存文件夹，返回路径。"""
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_dir = CACHE_DIR / ts
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def list_cache_sessions() -> list[dict]:
    """列出所有缓存会话，按时间倒序。

    返回: [{name, path, file_count, total_bytes, time_str}]
    """
    if not CACHE_DIR.exists():
        return []

    sessions: list[dict] = []
    for d in sorted(CACHE_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        # 只接受时间戳格式的文件夹名
        try:
            datetime.strptime(d.name, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            continue
        pkl_files = list(d.glob("*.pkl"))
        total = sum(f.stat().st_size for f in pkl_files)
        # 从文件夹名解析可读时间
        parts = d.name.replace("_", " ").replace("-", "/", 2)
        # "2026/06/08 15-18-42" → "2026/06/08 15:18:42"
        date_part, time_part = parts.split(" ", 1) if " " in parts else (parts, "")
        time_str = date_part + " " + time_part.replace("-", ":")
        sessions.append({
            "name": d.name,
            "path": d,
            "file_count": len(pkl_files),
            "total_bytes": total,
            "time_str": time_str,
        })
    return sessions


def latest_cache_session() -> Path | None:
    """返回最新的缓存会话文件夹路径，无会话则返回 None。"""
    sessions = list_cache_sessions()
    if sessions:
        return sessions[0]["path"]
    return None


def load_session_blocks(session_dir: Path) -> list[dict]:
    """加载指定会话文件夹中所有 pkl 文件的 blocks。"""
    if not session_dir.exists() or not session_dir.is_dir():
        return []
    result: list[dict] = []
    for pkl_file in session_dir.glob("*.pkl"):
        try:
            result.extend(pickle.loads(pkl_file.read_bytes()))
        except (pickle.PickleError, OSError, IndexError):
            continue
    return result


def delete_cache_session(session_dir: Path) -> bool:
    """删除指定的缓存会话文件夹。返回是否成功。"""
    import shutil
    try:
        if session_dir.exists() and session_dir.is_dir():
            shutil.rmtree(session_dir)
            return True
    except OSError:
        pass
    return False
