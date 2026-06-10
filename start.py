"""自动检测依赖并启动应用（跨平台）。

便携模式：libs/ 目录中已预装所有依赖，直接启动无需网络。
如 libs/ 缺失或 Python 版本不匹配，提示用户处理。
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

_PKG_IMPORT_MAP = {
    "opencv-python-headless": "cv2",
    "pymupdf": "fitz",
    "python-docx": "docx",
    "pillow": "PIL",
}

ROOT = Path(__file__).resolve().parent
REQ_FILE = ROOT / "requirements.txt"
LIBS_DIR = ROOT / "libs"


def _check_python_version() -> bool:
    """检查 Python 版本是否为 3.12。"""
    major, minor = sys.version_info[:2]
    if (major, minor) == (3, 12):
        return True
    print(f"[ERROR] 当前 Python {major}.{minor}，需要 3.12。")
    print(f"        libs/ 中的二进制包仅兼容 Python 3.12。")
    print()
    print("        解决方案：")
    print("          1. 安装 Python 3.12: https://www.python.org/downloads/")
    print("          2. 用 3.12 重建 libs/:")
    print(f"             python{major}.{minor} -m pip install --target libs -r requirements.txt")
    print("          3. 创建 3.12 虚拟环境:")
    print("             python3.12 -m venv venv")
    print("             venv/bin/pip install -r requirements.txt")
    return False


def _ensure_sys_path():
    """将 libs 目录加入 sys.path（最前面，优先于系统包）。"""
    if LIBS_DIR.exists():
        lib_str = str(LIBS_DIR)
        if lib_str not in sys.path:
            sys.path.insert(0, lib_str)
    for sub in ("site-packages", "."):
        p = LIBS_DIR / sub
        if p.exists():
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)


def _check_package(pkg_line: str) -> tuple[str, str, bool]:
    """检查单个包是否已安装（本地 libs + 系统）。"""
    pkg_line = pkg_line.strip()
    if not pkg_line or pkg_line.startswith("#"):
        return "", "", True

    pip_name = pkg_line.split()[0] if " " in pkg_line else pkg_line
    base_name = pip_name.split(">=")[0].split("<=")[0].split("==")[0].split("<")[0].split(">")[0]

    import_name = _PKG_IMPORT_MAP.get(base_name, base_name)
    import_name = import_name.replace("-", "_")

    try:
        importlib.import_module(import_name)
        return base_name, import_name, True
    except ImportError:
        return base_name, import_name, False


def ensure_deps() -> bool:
    """检测所有依赖是否可用。"""
    if not REQ_FILE.exists():
        print(f"[!] {REQ_FILE.name} not found, skipping check.")
        return True

    _ensure_sys_path()

    lines = REQ_FILE.read_text(encoding="utf-8").splitlines()
    missing = []
    installed = []

    for line in lines:
        pip_name, import_name, ok = _check_package(line)
        if not pip_name:
            continue
        if ok:
            installed.append(pip_name)
        else:
            missing.append(pip_name)

    if not missing:
        print(f"[OK] All dependencies ready ({len(installed)} packages)")
        return True

    print(f"[!] Missing {len(missing)} packages: {', '.join(missing)}")
    print()
    print("    Install manually:")
    print(f"    pip install --target libs -r requirements.txt")
    print()
    return False


def main():
    print("=" * 50)
    print("  File Locator Tool")
    print("=" * 50)

    if not _check_python_version():
        print("Press Enter to exit...")
        input()
        sys.exit(1)

    if not ensure_deps():
        print("Press Enter to exit...")
        input()
        sys.exit(1)

    print("\n[i] Starting Streamlit...\n")

    # 使用当前 Python 解释器启动 Streamlit
    # PYTHONPATH 确保子进程也能找到 libs/ 中的包
    import os
    env = os.environ.copy()
    libs_paths = [str(LIBS_DIR), str(LIBS_DIR / "site-packages")]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(libs_paths + [existing]) if existing else os.pathsep.join(libs_paths)

    cmd = [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py")]
    try:
        subprocess.run(cmd, cwd=str(ROOT), env=env)
    except KeyboardInterrupt:
        print("\n[i] Stopped.")


if __name__ == "__main__":
    main()
