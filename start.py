"""自动检测依赖并启动应用（跨平台）。

便携模式：libs/ 目录中已预装所有依赖，直接启动无需网络。
如 libs/ 缺失，提示用户手动安装。
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

    if not ensure_deps():
        print("Press Enter to exit...")
        input()
        sys.exit(1)

    print("\n[i] Starting Streamlit...\n")

    cmd = [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py")]
    try:
        subprocess.run(cmd, cwd=str(ROOT))
    except KeyboardInterrupt:
        print("\n[i] Stopped.")


if __name__ == "__main__":
    main()
