"""本地配置读写：保存大模型连接信息到项目目录下（便携模式）。"""
from __future__ import annotations

import json
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
