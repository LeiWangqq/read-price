"""定位：关键词预筛候选 block，返回精确匹配结果。

不做 LLM 调用——prefilter 已用 `kw in text` 精确子串匹配，结果 100% 准确。
OCR 在索引阶段完成，搜索不再等待 OCR。
"""
from __future__ import annotations

import re

_MAX_FINAL_RESULTS = 500   # 最终结果上限
_SNIPPET_LEN = 600  # 每个结果的最大片段字符数

_STOPWORDS = {
    "在", "哪", "哪个", "文件", "中", "有", "提到", "第", "几", "页", "章",
    "里", "的", "是", "请", "问", "查", "找", "一下", "哪些", "哪里",
    "了", "吗", "呢", "这", "那",
}

# 查询中用于分隔多个独立关键词的连接词
_CONJUNCTIONS = {"和", "与", "及", "以及", "并且", "或者", "或"}


def keywords(query: str) -> list[str]:
    """从查询里提取关键词：先在连接词处拆分，再去停用词。

    例：
      "大地测量"              → ["大地测量"]
      "大地测量和电磁波测距"   → ["大地测量", "电磁波测距"]
      "防腐涂料在哪个文件"     → ["防腐涂料"]
    """
    conj_pattern = "|".join(re.escape(c) for c in sorted(_CONJUNCTIONS, key=len, reverse=True))
    if conj_pattern:
        segments = re.split(conj_pattern, query)
    else:
        segments = [query]

    keywords: list[str] = []
    for seg in segments:
        tokens = re.findall(r"[一-龥]+|[A-Za-z0-9._%-]+", seg)
        for tok in tokens:
            if tok in _STOPWORDS:
                continue
            if re.fullmatch(r"[一-龥]+", tok):
                # 在停用词处进一步拆分中文 token
                stop_pattern = "|".join(
                    re.escape(w) for w in sorted(_STOPWORDS, key=len, reverse=True)
                )
                if stop_pattern:
                    parts = re.split(stop_pattern, tok)
                else:
                    parts = [tok]
                for part in parts:
                    if part and part not in _STOPWORDS:
                        keywords.append(part)
            else:
                keywords.append(tok)

    # 去重保序
    seen: set[str] = set()
    result: list[str] = []
    for k in keywords:
        if k and k not in seen:
            seen.add(k)
            result.append(k)
    return result


def prefilter(blocks: list[dict], query: str) -> list[dict]:
    """按关键词命中数对 block 打分排序，返回所有命中候选。

    保证每个有匹配的文件至少保留一个最佳块。
    """
    kws = keywords(query)
    if not kws:
        return []

    scored: list[tuple[int, dict]] = []
    for b in blocks:
        text = b.get("text", "")
        if not text:
            continue
        score = sum(text.count(k) for k in kws)
        if score > 0:
            scored.append((score, b))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)

    seen_files: set[str] = set()
    per_file_best: list[dict] = []
    rest: list[dict] = []
    for score, b in scored:
        fn = b["file"]
        if fn not in seen_files:
            seen_files.add(fn)
            per_file_best.append(b)
        else:
            rest.append(b)

    # 返回所有命中块
    result: list[dict] = list(per_file_best)
    result.extend(rest)
    return result


def locate(blocks: list[dict], query: str, cfg: dict = None,
           progress=None) -> tuple[list[dict], list[str]]:
    """定位流程：预筛 → 精确匹配。"""
    kws = keywords(query)
    if not kws:
        return [], []

    # 预筛
    candidates = prefilter(blocks, query)

    if not candidates:
        return [], []

    # 精确匹配
    results: list[dict] = []
    for b in candidates:
        text = b.get("text", "")
        if all(kw in text for kw in kws):
            results.append({
                "file": b.get("file", ""),
                "page": str(b.get("page", "")),
                "chapter": b.get("chapter", ""),
                "snippet": text[:_SNIPPET_LEN],
                "reason": "关键词命中",
            })

    if len(results) > _MAX_FINAL_RESULTS:
        results = results[:_MAX_FINAL_RESULTS]
    return results, []
