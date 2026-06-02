"""OpenAI 兼容大模型客户端封装。"""
from __future__ import annotations


class LLMError(Exception):
    """对外暴露的、带友好提示的大模型调用异常。"""


def list_models(base_url: str, api_key: str) -> list[str]:
    """拉取 OpenAI 兼容服务的可用模型列表(GET /v1/models)。"""
    try:
        from openai import OpenAI
    except ImportError as e:  # pragma: no cover
        raise LLMError("未安装 openai 库，请运行 pip install -r requirements.txt") from e

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
    try:
        from openai import OpenAI
    except ImportError as e:  # pragma: no cover
        raise LLMError("未安装 openai 库，请运行 pip install -r requirements.txt") from e

    client = OpenAI(base_url=base_url, api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:  # 鉴权/网络/模型名等错误统一包装
        raise LLMError(f"大模型调用失败：{e}") from e
