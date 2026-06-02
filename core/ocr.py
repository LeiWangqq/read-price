"""RapidOCR 封装：懒加载单例，对图片做中文 OCR。

使用 rapidocr-onnxruntime (ONNX Runtime 后端)，比 PaddleOCR 更快更轻量。
支持纠偏预处理和置信度追踪，低置信度结果可触发重试。
"""
from __future__ import annotations

import threading

import cv2
import numpy as np

_engine = None
_init_error = None
_lock = threading.Lock()

# 低于此置信度的结果被视为不可靠
_CONFIDENCE_THRESHOLD = 0.6


def _get_engine():
    """首次调用时才初始化 RapidOCR，线程安全。"""
    global _engine, _init_error
    if _engine is None:
        with _lock:
            if _engine is None:
                if _init_error is not None:
                    raise _init_error
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    _engine = RapidOCR()
                except Exception as e:
                    _init_error = e
                    raise RuntimeError(
                        f"OCR 引擎初始化失败: {e}\n"
                        "请运行: pip install rapidocr-onnxruntime"
                    ) from e
    return _engine


def _deskew(image: np.ndarray) -> np.ndarray:
    """自动纠偏：检测扫描件倾斜角度并旋转修正。"""
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    coords = cv2.findNonZero(bw)
    if coords is None or len(coords) < 50:
        return image
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    # minAreaRect 返回角度范围 [-90, 0)
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.5:
        return image
    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image, M, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def image_to_text(image: np.ndarray) -> tuple[str, float]:
    """对单张图片(numpy RGB)做 OCR，返回 (文本, 平均置信度)。"""
    engine = _get_engine()
    result, _elapse = engine(image)
    if not result:
        return "", 1.0
    lines = []
    confidences = []
    for item in result:
        if item[1]:
            lines.append(item[1])
            confidences.append(item[2])
    avg_conf = sum(confidences) / len(confidences) if confidences else 1.0
    return "\n".join(lines), avg_conf


def image_to_text_with_retry(image: np.ndarray) -> tuple[str, float]:
    """纠偏 + OCR：先纠偏再识别，返回 (文本, 平均置信度)。

    低置信度结果由调用方决定是否用更高 DPI 重新渲染后再次调用本函数。
    """
    deskewed = _deskew(image)
    text, conf = image_to_text(deskewed)
    return text, conf


def ocr_images(images: list[np.ndarray]) -> list[tuple[str, float]]:
    """对多张图片依次做 OCR，返回与输入等长的 (文本, 置信度) 列表。"""
    if not images:
        return []
    engine = _get_engine()
    results: list[tuple[str, float]] = []
    for img in images:
        result, _elapse = engine(img)
        if not result:
            results.append(("", 1.0))
        else:
            lines = []
            confs = []
            for item in result:
                if item[1]:
                    lines.append(item[1])
                    confs.append(item[2])
            avg = sum(confs) / len(confs) if confs else 1.0
            results.append(("\n".join(lines), avg))
    return results
