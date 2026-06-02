"""索引构建：扫描文件夹 / 处理上传文件，抽取并缓存 block 列表。

缓存键 = 文件路径 + 修改时间(mtime)，文件未变则跳过重复抽取/OCR。
索引时即完成所有扫描页的 OCR，搜索结果写入 block 缓存，搜索阶段不再等待 OCR。
"""
from __future__ import annotations

import hashlib
import os
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core import config, extractor

_SUPPORTED = (".pdf", ".docx", ".doc")
_CACHE_VERSION = 10  # block 格式变更时递增，自动使旧缓存失效

_FILE_PARALLEL_THRESHOLD = 3
_OCR_WORKERS = 4  # 并行 OCR 线程数


def _cache_path(path: Path) -> Path:
    mtime = path.stat().st_mtime_ns
    key = f"{path.resolve()}|{mtime}|v{_CACHE_VERSION}"
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return config.CACHE_DIR / f"{digest}.pkl"


def _ocr_scanned_blocks(blocks: list[dict], progress=None) -> list[dict]:
    """对 block 列表中所有未 OCR 的扫描页做 OCR。

    按文件分组批量渲染（每个 PDF 只打开一次），然后多线程 OCR。
    支持纠偏预处理和低置信度重试。
    """
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor, as_completed

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

    # 按文件分组，减少 PDF 打开次数
    by_file: dict[str, list[dict]] = defaultdict(list)
    for b in need:
        by_file[b["render_info"]["pdf_path"]].append(b)

    # 批量渲染：每个文件只 fitz.open() 一次
    render_jobs: list[tuple[dict, np.ndarray]] = []

    for pdf_path, file_blocks in by_file.items():
        try:
            import io
            import sys

            # 抑制 MuPDF stderr 警告
            old_stderr = sys.stderr
            sys.stderr = io.StringIO()
            try:
                doc = fitz.open(pdf_path)
                try:
                    for b in file_blocks:
                        info = b["render_info"]
                        page = doc[info["page_idx"]]
                        pix = page.get_pixmap(dpi=info.get("dpi", 300))
                        img = Image.frombytes(
                            "RGB", (pix.width, pix.height), pix.samples)
                        render_jobs.append((b, np.array(img)))
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

    # 多线程 OCR（纠偏在 ocr.image_to_text_with_retry 内部执行）
    def _ocr_one(job):
        block, img = job
        try:
            text, conf = ocr.image_to_text_with_retry(img)
            return block, text, conf, None
        except Exception as e:
            return block, "", 0.0, (
                f"文件「{block.get('file', '?')}」第 {block.get('page', '?')} 页 "
                f"OCR 失败: {type(e).__name__}: {e}"
            )

    done = 0

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_ocr_one, job): job for job in render_jobs}
        for fut in as_completed(futures):
            block, text, conf, err = fut.result()
            block["text"] = text
            if err:
                block["_ocr_error"] = err
            if text:
                ch = extractor._detect_chapter(text)
                if ch:
                    block["chapter"] = ch
            done += 1
            if progress and done % 4 == 0:
                progress(done, total, f"OCR {done}/{total} 页 (置信度 {conf:.2f})...")

    if progress:
        progress(total, total, f"OCR 完成 ({total}/{total} 页)")

    return blocks


def _extract_cached(path: Path, progress=None) -> list[dict]:
    """带缓存的单文件抽取（含 OCR）。"""
    cache_file = _cache_path(path)
    if cache_file.exists():
        try:
            return pickle.loads(cache_file.read_bytes())
        except (pickle.PickleError, OSError):
            pass
    blocks = extractor.extract(path)
    blocks = _ocr_scanned_blocks(blocks, progress=progress)
    try:
        cache_file.write_bytes(pickle.dumps(blocks))
    except OSError:
        pass
    return blocks


def index_folder(folder: str, progress=None) -> list[dict]:
    """递归扫描文件夹下所有受支持文件，含 OCR 预处理，返回合并后的 block 列表。"""
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(f"路径不是有效文件夹：{folder}")

    config.ensure_dirs()
    files = [p for p in root.rglob("*") if p.suffix.lower() in _SUPPORTED]
    total = len(files)

    if total < _FILE_PARALLEL_THRESHOLD:
        blocks: list[dict] = []
        for idx, fp in enumerate(files, 1):
            if progress:
                progress(idx, total, fp.name)
            # 单文件内部已有子进度，不做额外包装
            file_blocks = _extract_cached(fp)
            blocks.extend(file_blocks)
        return blocks

    # 多文件并行处理（抽取 + OCR 在 _extract_cached 内完成）
    max_workers = min(total, os.cpu_count() or 1, 4)
    results: list[list[dict]] = [[] for _ in range(total)]
    done_count = 0

    def _process(idx: int, fp: Path) -> tuple[int, list[dict]]:
        # 子线程内不传 progress（避免 Streamlit 跨线程 UI 竞争）
        return idx, _extract_cached(fp)

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


def index_paths(paths: list[str], progress=None) -> list[dict]:
    """对一组具体文件路径建索引（用于上传文件已落盘的临时路径）。"""
    config.ensure_dirs()
    valid = [(i, Path(p)) for i, p in enumerate(paths)
             if Path(p).suffix.lower() in _SUPPORTED]
    total = len(valid)

    if total < _FILE_PARALLEL_THRESHOLD:
        blocks: list[dict] = []
        for idx, (i, fp) in enumerate(valid, 1):
            if progress:
                progress(idx, total, fp.name)
            blocks.extend(_extract_cached(fp))
        return blocks

    max_workers = min(total, os.cpu_count() or 1, 4)
    results: list[list[dict]] = [[] for _ in range(total)]
    done_count = 0

    def _process(idx: int, fp: Path) -> tuple[int, list[dict]]:
        return idx, _extract_cached(fp)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_process, i, fp) for i, fp in valid]
        for fut in futures:
            idx, file_blocks = fut.result()
            results[idx] = file_blocks
            done_count += 1
            if progress:
                progress(done_count, total, valid[idx][1].name)

    merged: list[dict] = []
    for r in results:
        merged.extend(r)
    return merged
