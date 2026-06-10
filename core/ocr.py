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
    """自动纠偏：先缩小检测角度（快），仅真正倾斜时全尺寸旋转。

    缩小到 800px 宽做角度检测，耗时从 ~0.15s 降至 ~0.01s。
    """
    h, w = image.shape[:2]
    # 缩小用于角度检测
    scale = min(1.0, 800 / max(w, h))
    if scale < 1.0:
        small = cv2.resize(image, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
    else:
        small = image

    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    coords = cv2.findNonZero(bw)
    if coords is None or len(coords) < 50:
        return image
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 1.0:
        return image
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
    _get_engine()  # 确保引擎已初始化
    return [image_to_text(img) for img in images]
