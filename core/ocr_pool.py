"""多进程 OCR 池：把多页扫描图分发到多个子进程并行识别。

每个子进程独立初始化一份 RapidOCR 引擎。
页数较少时直接用主进程串行，避免建池/加载模型的固定开销得不偿失。
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np

# 低于此页数走串行（建进程池 + 各子进程加载模型有固定开销）
_PARALLEL_THRESHOLD = 4

_worker_engine = None


def _init_worker():
    """子进程初始化：每进程加载一份引擎并复用。"""
    global _worker_engine
    from rapidocr_onnxruntime import RapidOCR
    _worker_engine = RapidOCR()


def _ocr_one(image: np.ndarray) -> str:
    result, _elapse = _worker_engine(image)
    if not result:
        return ""
    lines = [item[1] for item in result if item[1]]
    return "\n".join(lines)


def images_to_texts(images: list[np.ndarray], max_workers: int | None = None) -> list[str]:
    """对一批图片做 OCR，返回与输入等长的文本列表。"""
    if not images:
        return []

    from core import ocr

    # 少量图：主进程串行（引擎已暖，无额外开销）
    if len(images) < _PARALLEL_THRESHOLD:
        return [ocr.image_to_text(im)[0] for im in images]

    if max_workers is None:
        max_workers = min(4, os.cpu_count() or 1)
    if max_workers <= 1:
        return [ocr.image_to_text(im)[0] for im in images]

    try:
        with ProcessPoolExecutor(
            max_workers=max_workers, initializer=_init_worker
        ) as ex:
            return list(ex.map(_ocr_one, images))
    except Exception:  # noqa: BLE001 多进程失败则回退串行，保证可用
        return [ocr.image_to_text(im)[0] for im in images]
