"""OpenAI 兼容大模型客户端封装。"""
from __future__ import annotations

import sys
from pathlib import Path


class LLMError(Exception):
    """对外暴露的、带友好提示的大模型调用异常。"""


def _import_openai():
    """导入 openai，先确保 libs/ 在 sys.path 中，失败时给出精确诊断。"""
    # 先确保 libs/ 在 sys.path（无论之前是否已注入）
    libs = Path(__file__).resolve().parent.parent / "libs"
    if libs.exists():
        libs_str = str(libs)
        if libs_str not in sys.path:
            sys.path.insert(0, libs_str)

    try:
        from openai import OpenAI
        return OpenAI
    except ImportError as e:
        raise LLMError(
            f"无法导入 openai 库：{e}\n"
            f"Python 版本：{sys.version}\n"
            f"libs 路径：{libs}\n"
            f"libs 存在：{libs.exists()}\n"
            f"可能原因：libs/ 中的包与当前 Python 版本不兼容"
        )


def list_models(base_url: str, api_key: str) -> list[str]:
    """拉取 OpenAI 兼容服务的可用模型列表(GET /v1/models)。"""
    OpenAI = _import_openai()
    client = OpenAI(base_url=base_url, api_key=api_key)
    try:
        resp = client.models.list()
        ids = [m.id for m in resp.data]
        return sorted(ids)
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"获取模型列表失败：{e}") from e


def chat(base_url: str, api_key: str, model: str, messages: list[dict],
         temperature: float = 0.1) -> str:
    """调用 chat completions，返回回复文本。失败时抛出 LLMError。"""
    OpenAI = _import_openai()
    client = OpenAI(base_url=base_url, api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        raise LLMError(f"大模型调用失败：{e}") from e


def chat_stream(base_url: str, api_key: str, model: str,
                messages: list[dict], temperature: float = 0.1):
    """流式调用 chat completions，逐块 yield 文本片段。"""
    OpenAI = _import_openai()
    client = OpenAI(base_url=base_url, api_key=api_key)
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


def polish_file(base_url: str, api_key: str, model: str,
                query: str, filename: str, items: list[dict]):
    """流式整理单个文件的所有搜索结果，yield 文本片段。

    items: 该文件的所有结果 dict（含 file, page, chapter, snippet 等）。
    """
    snippets = []
    for r in items:
        page = r.get("page", "?")
        chapter = r.get("chapter", "")
        text = r.get("snippet", "")
        snippets.append(f"【第{page}页 {chapter}】\n{text}")

    combined = "\n\n".join(snippets)

    prompt = (
        f"用户在文件「{filename}」中搜索「{query}」，"
        f"找到了 {len(items)} 处匹配内容。\n\n"
        f"请将每段内容整理为清晰、美观的格式：\n"
        f"1. 修正 OCR 错误字符和乱码\n"
        f"2. 保留所有原始信息（数字、日期、金额、单位等不可修改）\n"
        f"3. 提取与「{query}」最相关的核心信息\n"
        f"4. 用 Markdown 格式输出，适当使用 **加粗**、表格、列表\n"
        f"5. 每段用 ### 标注页码和章节\n\n"
        f"以下是原始内容：\n\n{combined}"
    )

    yield from chat_stream(base_url, api_key, model, [
        {"role": "system", "content": "你是专业的文档内容整理助手，擅长将 OCR 识别的杂乱文字整理为清晰美观的格式。"},
        {"role": "user", "content": prompt},
    ], temperature=0.1)
