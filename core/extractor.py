"""文档抽取：PDF / Word 逐页(段)抽取文本，扫描页标记占位，识别章节标题。

扫描页不在此处 OCR（延迟到查询时按需识别），只存 render_info 元数据。

输出统一为 block 列表：
    {"file": 文件名, "path": 绝对路径, "page": 页码或None, "chapter": 章节,
     "text": 文本, "scanned": 是否扫描页, "render_info": 渲染元数据}
"""
from __future__ import annotations

import re
from pathlib import Path

# 中文章节标题：第X章 / 第X节 / 第X条 / 一、 / 1. / 1.1 等
_CHAPTER_PATTERNS = [
    re.compile(r"^\s*第\s*[一二三四五六七八九十百千零0-9]+\s*[章节節条].*"),
    re.compile(r"^\s*[一二三四五六七八九十]+\s*[、.\s].{0,40}$"),
    re.compile(r"^\s*\d+(\.\d+)*\s+\S.{0,40}$"),
]

# 文本极少则疑似扫描页，触发 OCR
_SCAN_TEXT_THRESHOLD = 10
# 有图像且文本少于此值也判定为扫描页（处理叠加了少量文字的图片 PDF）
_IMG_TEXT_THRESHOLD = 400  # 提升至 400，避免已 OCR 的 PDF 短表格页被误判
# OCR 渲染分辨率（DPI 300 对中文小字识别率显著优于 200）
_OCR_DPI = 300

# 扫描仪软件标识（出现在 PDF 元数据 Producer 字段中）
_SCANNER_KEYWORDS = (
    "scan", "twain", "wia", "naps2", "iris", "abbyy",
    "scansnap", "canoscan", "epson scan", "hp scan",
)


def _is_garbled(text: str) -> bool:
    """检测文本是否为乱码。

    统计有效 CJK + ASCII 字母数字 + 常用中文标点占比，
    低于 25% 判定为乱码（CID 字体缺 ToUnicode 映射的典型表现）。
    """
    if not text or len(text.strip()) < 20:
        return False
    total = len(text)
    valid = 0
    for ch in text:
        # CJK 统一表意文字 + 扩展A
        if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿':
            valid += 1
        elif ch.isalnum():
            valid += 1
        elif ch in '，。、；：""''（）《》【】—…· \t\n':
            valid += 1
    return valid / total < 0.25


def _has_text_blocks(page) -> bool:
    """检查页面是否包含真正的文本块（type=0）。

    扫描仪生成的 PDF 中图像常以内联方式存储，page.get_images() 无法检测到。
    通过 get_text("dict") 检查 block 类型是最可靠的方式：
    如果页面没有任何文本块，说明是纯图像页（扫描件）。
    """
    try:
        td = page.get_text("dict")
        return any(b.get("type") == 0 and b.get("spans") for b in td.get("blocks", []))
    except Exception:
        return len((page.get_text("text") or "").strip()) > 0


def _detect_chapter(text: str) -> str | None:
    """从一段文本的开头几行里找章节标题，找到则返回。"""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for pat in _CHAPTER_PATTERNS:
            if pat.match(line):
                return line[:60]
        break  # 只看第一行非空文本
    return None


def _classify_pdf(doc) -> str:
    """快速判别 PDF 类型：digital / scanned / mixed。

    1. 元数据 Producer 字段检测扫描仪标识（零成本）
    2. 抽样前 3 页：文字长度 + 图像数量
    """
    # 1. 元数据检测
    producer = (doc.metadata.get("producer") or "").lower()
    if any(kw in producer for kw in _SCANNER_KEYWORDS):
        return "scanned"

    # 2. 抽样前 3 页
    sample_count = min(3, len(doc))
    has_text_page = False
    has_image_page = False

    for i in range(sample_count):
        page = doc[i]
        text = page.get_text("text").strip()
        images = page.get_images(full=True)

        if len(text) >= 50:
            has_text_page = True
        if images:
            has_image_page = True

    if has_text_page and not has_image_page:
        return "digital"
    if has_image_page and not has_text_page:
        return "scanned"
    if has_text_page and has_image_page:
        return "mixed"
    return "digital"


def _scanned_block(path: Path, page_idx: int, chapter: str) -> dict:
    """生成扫描页 block（跳过文本提取，直接待 OCR）。"""
    return {
        "file": path.name,
        "path": str(path),
        "page": page_idx + 1,
        "chapter": chapter,
        "text": "",
        "scanned": True,
        "render_info": {
            "pdf_path": str(path.resolve()),
            "page_idx": page_idx,
            "dpi": _OCR_DPI,
        },
    }


def _text_block(path: Path, page_idx: int, chapter: str, text: str) -> dict:
    """生成文本块 block（已提取文字，无需 OCR）。"""
    return {
        "file": path.name,
        "path": str(path),
        "page": page_idx + 1,
        "chapter": chapter,
        "text": text,
        "scanned": False,
        "render_info": None,
    }


def _extract_scanned_pdf(doc, path: Path) -> list[dict]:
    """纯扫描 PDF：所有页面直接标记为扫描页，跳过文本提取。"""
    chapter = "（未识别章节）"
    return [_scanned_block(path, i, chapter) for i in range(len(doc))]


def _extract_digital_pdf(doc, path: Path) -> list[dict]:
    """数字 PDF：所有页面直接提取文本，跳过 OCR。"""
    blocks: list[dict] = []
    chapter = "（未识别章节）"
    for i, page in enumerate(doc):
        text = (page.get_text("text") or "").strip()
        ch = _detect_chapter(text)
        if ch:
            chapter = ch
        blocks.append(_text_block(path, i, chapter, text))
    return blocks


def _extract_mixed_pdf(doc, path: Path) -> list[dict]:
    """混合 PDF：逐页走 4 层判别逻辑（现有行为）。"""
    blocks: list[dict] = []
    chapter = "（未识别章节）"
    for i, page in enumerate(doc):
        text = (page.get_text("text") or "").strip()
        text_len = len(text)

        is_scanned = text_len < _SCAN_TEXT_THRESHOLD
        if not is_scanned and text_len < _IMG_TEXT_THRESHOLD:
            is_scanned = not _has_text_blocks(page)
        if not is_scanned and _is_garbled(text):
            is_scanned = True

        if is_scanned:
            blocks.append(_scanned_block(path, i, chapter))
        else:
            ch = _detect_chapter(text)
            if ch:
                chapter = ch
            blocks.append(_text_block(path, i, chapter, text))
    return blocks


def _extract_pdf(path: Path) -> list[dict]:
    """PDF 抽取入口：先判别文件类型，再走对应路径。"""
    import io
    import sys

    import fitz

    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        doc = fitz.open(path)
        try:
            pdf_type = _classify_pdf(doc)
            if pdf_type == "scanned":
                return _extract_scanned_pdf(doc, path)
            if pdf_type == "digital":
                return _extract_digital_pdf(doc, path)
            return _extract_mixed_pdf(doc, path)
        finally:
            doc.close()
    finally:
        sys.stderr = old_stderr


def render_pdf_page(path: str | Path, page_idx: int, dpi: int = _OCR_DPI):
    """按需渲染单个 PDF 页面为 numpy RGB 数组，供 OCR 使用。

    PyMuPDF 对格式不规范的 PDF 会打印 MuPDF error 到 stderr，
    这些通常是警告而非致命错误，渲染仍可继续。
    """
    import contextlib
    import io
    import sys

    import numpy as np
    from PIL import Image

    import fitz

    # 抑制 MuPDF stderr 警告（不中断处理）
    mupdf_stderr = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = mupdf_stderr
    try:
        doc = fitz.open(path)
        try:
            page = doc[page_idx]
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            return np.array(img)
        finally:
            doc.close()
    finally:
        sys.stderr = old_stderr


def _convert_doc_to_docx(src: Path) -> Path | None:
    """尝试将 .doc 转换为 .docx，返回转换后的路径（失败返回 None）。"""
    import shutil
    import subprocess
    import tempfile

    tmp_dir = tempfile.mkdtemp(prefix="doc2docx_")
    try:
        # 优先尝试 LibreOffice（无 Word 也能用）
        loffice = shutil.which("soffice") or shutil.which("libreoffice")
        if loffice:
            subprocess.run(
                [loffice, "--headless", "--convert-to", "docx",
                 "--outdir", tmp_dir, str(src)],
                timeout=60, check=True,
                capture_output=True,
            )
            converted = Path(tmp_dir) / (src.stem + ".docx")
            if converted.exists():
                return converted
    except Exception:
        pass

    try:
        # Windows 退路：Word COM 自动化
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            try:
                doc = word.Documents.Open(str(src), ReadOnly=True)
                out = Path(tmp_dir) / (src.stem + ".docx")
                doc.SaveAs2(str(out), FileFormat=16)
                doc.Close(False)
                if out.exists():
                    return out
            finally:
                word.Quit()
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass

    return None


def _parse_docx_blocks(path: Path, display_name: str) -> list[dict]:
    """从 .docx 解析段落并返回 block 列表。display_name 用于 block 的 file 字段。"""
    import docx

    document = docx.Document(path)
    blocks: list[dict] = []
    current_chapter = "（未识别章节）"
    buffer: list[str] = []

    def flush():
        if buffer:
            blocks.append(
                {
                    "file": display_name,
                    "path": str(path),
                    "page": None,
                    "chapter": current_chapter,
                    "text": "\n".join(buffer).strip(),
                    "scanned": False,
                    "render_info": None,
                }
            )
            buffer.clear()

    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower()
        is_heading = style.startswith("heading") or _detect_chapter(text) is not None
        if is_heading:
            flush()
            current_chapter = text[:60]
        buffer.append(text)
    flush()
    return blocks


def _extract_docx(path: Path) -> list[dict]:
    """解析 .docx 文件。"""
    return _parse_docx_blocks(path, path.name)


def _extract_doc(path: Path) -> list[dict]:
    """解析 .doc 文件（旧版 Word 二进制格式）。

    先尝试 python-docx 直接读取，失败则通过 LibreOffice/Word COM 转换为 .docx 再解析。
    """
    try:
        return _parse_docx_blocks(path, path.name)
    except Exception:
        pass

    converted = _convert_doc_to_docx(path)
    if converted:
        try:
            return _parse_docx_blocks(converted, path.name)
        finally:
            try:
                converted.unlink()
                converted.parent.rmdir()
            except OSError:
                pass

    return []


def extract(path: str | Path) -> list[dict]:
    """根据扩展名抽取单个文件，返回 block 列表。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".doc":
        return _extract_doc(path)
    return []
