"""索引构建：扫描文件夹 / 处理上传文件，抽取并缓存 block 列表。

缓存键 = 文件路径 + 修改时间(mtime)，文件未变则跳过重复抽取/OCR。
索引时即完成所有扫描页的 OCR，搜索结果写入 block 缓存，搜索阶段不再等待 OCR。
"""
from __future__ import annotations

import os
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core import config, extractor

_SUPPORTED = (".pdf", ".docx", ".doc")
_FILE_PARALLEL_THRESHOLD = 3


def _ocr_scanned_blocks(blocks: list[dict], progress=None) -> list[dict]:
    """对 block 列表中所有未 OCR 的扫描页做 OCR。

    流水线架构：主线程串行渲染 → 队列 → 8 线程并行 OCR。
    渲染速度 (~0.07s/页) 远快于 OCR (~0.8s/页)，队列始终满载，OCR 线程无空闲。
    """
    from collections import defaultdict
    from queue import Queue
    from threading import Thread

    import fitz
    import numpy as np
    from PIL import Image

    from core import ocr

    need = [b for b in blocks if b.get("scanned") and not b.get("text")]
    if not need:
        return blocks

    total = len(need)
    if progress:
        progress(0, total, f"正在渲染并识别 {total} 页扫描内容...")

    by_file: dict[str, list[dict]] = defaultdict(list)
    for b in need:
        by_file[b["render_info"]["pdf_path"]].append(b)

    # 流水线：渲染线程往队列写 (block, image)，OCR 工作线程从队列读
    q: Queue = Queue(maxsize=8)
    SENTINEL = None  # 结束信号

    def render_all():
        """主线程串行渲染所有页面，复用 doc handle。"""
        for pdf_path, file_blocks in by_file.items():
            try:
                import io
                import sys
                old_stderr = sys.stderr
                sys.stderr = io.StringIO()
                try:
                    doc = fitz.open(pdf_path)
                    try:
                        for b in file_blocks:
                            info = b["render_info"]
                            page = doc[info["page_idx"]]
                            pix = page.get_pixmap(dpi=info.get("dpi", 300))
                            img = np.array(Image.frombytes(
                                "RGB", (pix.width, pix.height), pix.samples))
                            q.put((b, img))
                    finally:
                        doc.close()
                finally:
                    sys.stderr = old_stderr
            except Exception as e:
                for b in file_blocks:
                    b["text"] = ""
                    b["_ocr_error"] = (
                        f"文件「{b.get('file', '?')}」第 {b.get('page', '?')} 页 "
                        f"渲染失败: {type(e).__name__}: {e}"
                    )
        q.put(SENTINEL)

    def ocr_worker():
        """OCR 工作线程：从队列取图，做 OCR，写回 block。"""
        while True:
            item = q.get()
            if item is SENTINEL:
                q.put(SENTINEL)  # 传递给下一个 worker
                break
            block, img = item
            try:
                text, conf = ocr.image_to_text_with_retry(img)
                block["text"] = text
                if text:
                    ch = extractor._detect_chapter(text)
                    if ch:
                        block["chapter"] = ch
            except Exception as e:
                block["text"] = ""
                block["_ocr_error"] = (
                    f"文件「{block.get('file', '?')}」第 {block.get('page', '?')} 页 "
                    f"OCR 失败: {type(e).__name__}: {e}"
                )

    # 启动 OCR 工作线程
    ocr_workers = []
    for _ in range(8):
        t = Thread(target=ocr_worker, daemon=True)
        t.start()
        ocr_workers.append(t)

    # 主线程做渲染（阻塞直到全部完成）
    render_all()

    # 等待所有 OCR 完成
    for t in ocr_workers:
        t.join()

    if progress:
        progress(total, total, f"OCR 完成 ({total}/{total} 页)")

    return blocks


def _extract_cached(path: Path, progress=None, force: bool = False,
                    session_dir=None) -> list[dict]:
    """带缓存的单文件抽取（含 OCR）。force=True 时跳过缓存强制重新抽取。"""
    cache_file = config.cache_path(path, session_dir=session_dir)
    if not force and cache_file.exists():
        try:
            return pickle.loads(cache_file.read_bytes())
        except (pickle.PickleError, OSError):
            pass
    else:
        # force 模式或缓存损坏，删除旧缓存
        cache_file.unlink(missing_ok=True)
    blocks = extractor.extract(path)
    blocks = _ocr_scanned_blocks(blocks, progress=progress)
    try:
        cache_file.write_bytes(pickle.dumps(blocks))
    except OSError:
        pass
    return blocks


def _process_files(files: list[Path], progress=None, force: bool = False,
                   session_dir=None) -> list[dict]:
    """对一组文件路径建索引，串行或并行处理，返回合并后的 block 列表。"""
    config.ensure_dirs()
    total = len(files)

    if total < _FILE_PARALLEL_THRESHOLD:
        blocks: list[dict] = []
        for idx, fp in enumerate(files, 1):
            if progress:
                progress(idx, total, fp.name)
            blocks.extend(_extract_cached(fp, force=force, session_dir=session_dir))
        return blocks

    max_workers = min(total, os.cpu_count() or 1, 4)
    results: list[list[dict]] = [[] for _ in range(total)]
    done_count = 0

    def _process(idx: int, fp: Path) -> tuple[int, list[dict]]:
        return idx, _extract_cached(fp, force=force, session_dir=session_dir)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_process, i, fp) for i, fp in enumerate(files)]
        for fut in futures:
            idx, file_blocks = fut.result()
            results[idx] = file_blocks
            done_count += 1
            if progress:
                progress(done_count, total, files[idx].name)

    merged: list[dict] = []
    for r in results:
        merged.extend(r)
    return merged


def index_folder(folder: str, progress=None, force: bool = False,
                 session_dir=None) -> list[dict]:
    """递归扫描文件夹下所有受支持文件，含 OCR 预处理，返回合并后的 block 列表。
    force=True 时跳过缓存强制重新抽取。"""
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(f"路径不是有效文件夹：{folder}")
    files = [p for p in root.rglob("*") if p.suffix.lower() in _SUPPORTED]
    return _process_files(files, progress=progress, force=force,
                          session_dir=session_dir)


def index_paths(paths: list[str], progress=None, force: bool = False,
                session_dir=None) -> list[dict]:
    """对一组具体文件路径建索引（用于上传文件已落盘的临时路径）。
    force=True 时跳过缓存强制重新抽取。"""
    files = [Path(p) for p in paths if Path(p).suffix.lower() in _SUPPORTED]
    return _process_files(files, progress=progress, force=force,
                          session_dir=session_dir)
