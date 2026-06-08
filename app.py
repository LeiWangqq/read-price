"""文件快速定位工具 — Streamlit 入口。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 将项目本地 libs 目录加入 sys.path（优先于系统包）
_libs = Path(__file__).resolve().parent / "libs"
if _libs.exists():
    _s = str(_libs)
    if _s not in sys.path:
        sys.path.insert(0, _s)
    for _sub in ("site-packages", "."):
        _p = _libs / _sub
        if _p.exists():
            _ps = str(_p)
            if _ps not in sys.path:
                sys.path.insert(0, _ps)

import streamlit as st

from core import config, indexer, locator, llm

st.set_page_config(page_title="文件快速定位", page_icon="🔎", layout="wide")


def _init_state():
    if "cfg" not in st.session_state:
        st.session_state.cfg = config.load_config()
    if "blocks" not in st.session_state:
        st.session_state.blocks = []
    if "index_info" not in st.session_state:
        st.session_state.index_info = ""


def _sidebar_config():
    st.sidebar.header("① 大模型配置")
    cfg = st.session_state.cfg
    configured = config.is_configured(cfg)
    if configured:
        st.sidebar.success(f"已配置模型：{cfg['model']}")
    else:
        st.sidebar.warning("首次使用，请填写大模型接口信息（OpenAI 兼容）")

    with st.sidebar.expander("查看 / 修改配置", expanded=not configured):
        base_url = st.text_input("Base URL", value=cfg.get("base_url", ""),
                                 placeholder="https://api.example.com/v1")
        api_key = st.text_input("API Key", value=cfg.get("api_key", ""),
                                type="password")

        if st.button("获取模型列表", use_container_width=True):
            if not base_url.strip() or not api_key.strip():
                st.error("请先填写 Base URL 和 API Key")
            else:
                try:
                    st.session_state.model_options = llm.list_models(
                        base_url.strip(), api_key.strip())
                    if not st.session_state.model_options:
                        st.warning("该服务未返回任何模型，请手动填写。")
                except Exception as e:
                    st.error(f"获取模型列表失败：{type(e).__name__}: {e}")

        options = st.session_state.get("model_options", [])
        if options:
            cur = cfg.get("model", "")
            idx = options.index(cur) if cur in options else 0
            model = st.selectbox("模型名称 (model)", options, index=idx)
        else:
            model = st.text_input(
                "模型名称 (model)", value=cfg.get("model", ""),
                placeholder="先点上方“获取模型列表”，或手动填写")

        if st.button("保存配置", use_container_width=True):
            config.save_config(base_url, api_key, model)
            st.session_state.cfg = config.load_config()
            st.success("已保存。")
            st.rerun()


def _sidebar_cache():
    st.sidebar.header("② 缓存目录")
    cfg = st.session_state.cfg
    current_cache = cfg.get("cache_dir", "")

    if current_cache:
        st.sidebar.success(f"自定义缓存：{current_cache}")
    else:
        st.sidebar.caption(f"默认缓存：{config._DEFAULT_CACHE_DIR}")

    # 显示缓存统计
    cache_count = 0
    if config.CACHE_DIR.exists():
        cache_count = len(list(config.CACHE_DIR.glob("*.pkl")))
    if cache_count:
        st.sidebar.info(f"当前缓存中有 {cache_count} 个已索引文件")

    # 浏览选择缓存目录
    if "cache_dir_input" not in st.session_state:
        st.session_state.cache_dir_input = current_cache

    if st.sidebar.button("📁 浏览选择缓存目录", use_container_width=True):
        picked = _pick_folder()
        if picked:
            st.session_state.cache_dir_input = picked

    cache_input = st.sidebar.text_input(
        "缓存目录路径（可手动编辑）", key="cache_dir_input",
        placeholder=r"D:\read-price-cache")

    col1, col2 = st.columns(2)
    with col1:
        apply_clicked = st.button("应用", use_container_width=True)
    with col2:
        reset_clicked = st.button("恢复默认", use_container_width=True)

    if apply_clicked:
        if cache_input.strip():
            config.set_cache_dir(cache_input.strip())
            cfg["cache_dir"] = cache_input.strip()
            config.save_config(
                cfg["base_url"], cfg["api_key"], cfg["model"],
                cache_dir=cache_input.strip())
            st.session_state.cfg = cfg
            st.success("已切换缓存目录")
            st.rerun()
        else:
            st.warning("请输入或浏览选择缓存目录路径")

    if reset_clicked:
        config.reset_cache_dir()
        cfg["cache_dir"] = ""
        config.save_config(
            cfg["base_url"], cfg["api_key"], cfg["model"],
            cache_dir="")
        st.session_state.cfg = cfg
        st.success("已恢复默认缓存")
        st.rerun()

    # 自动加载缓存中的 blocks（首次进入且有缓存时）
    if not st.session_state.blocks and cache_count:
        blocks = _load_cache_blocks()
        if blocks:
            st.session_state.blocks = blocks
            st.session_state.index_info = (
                f"已从缓存加载 {len(blocks)} 个文本块（{cache_count} 个文件）。")


def _load_cache_blocks() -> list[dict]:
    """从当前缓存目录加载所有已缓存的 blocks。"""
    import pickle
    blocks = []
    for pkl_file in config.CACHE_DIR.glob("*.pkl"):
        try:
            file_blocks = pickle.loads(pkl_file.read_bytes())
            blocks.extend(file_blocks)
        except (pickle.PickleError, OSError):
            continue
    return blocks


def _progress_factory(bar, label):
    def cb(idx, total, name):
        ratio = idx / total if total else 1.0
        bar.progress(min(ratio, 1.0))
        label.caption(f"正在处理 ({idx}/{total})：{name}")
    return cb


def _pick_folder() -> str:
    """弹出本机系统文件夹选择对话框，返回所选路径（取消/失败返回空串）。

    Streamlit 脚本运行在工作线程，tkinter 必须在主线程，故放到独立子进程里运行，
    选中的路径通过临时文件以 UTF-8 回传，避免中文路径编码问题。
    """
    import os
    import subprocess
    import sys
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    tmp.close()
    code = (
        "import sys, tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk(); root.withdraw(); root.wm_attributes('-topmost', 1)\n"
        "p = filedialog.askdirectory()\n"
        "open(sys.argv[1], 'w', encoding='utf-8').write(p or '')\n"
    )
    kwargs = {"timeout": 300}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run([sys.executable, "-c", code, tmp.name], **kwargs)
        with open(tmp.name, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:  # noqa: BLE001
        return ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _sidebar_source():
    st.sidebar.header("③ 文档来源")
    source = st.sidebar.radio("选择方式", ["本地文件夹", "上传文件"], horizontal=True)

    if source == "本地文件夹":
        if "folder_input" not in st.session_state:
            st.session_state.folder_input = ""
        if st.sidebar.button("📁 浏览选择文件夹", use_container_width=True):
            picked = _pick_folder()
            if picked:
                st.session_state.folder_input = picked
            else:
                st.sidebar.caption("未选择（或当前环境不支持弹窗，可手动输入）")
        folder = st.sidebar.text_input(
            "文件夹路径（可手动编辑）", key="folder_input", placeholder=r"D:\价格台账")
        if st.sidebar.button("扫描并建立索引", use_container_width=True):
            if not folder.strip():
                st.sidebar.error("请填写文件夹路径")
            else:
                bar = st.sidebar.progress(0.0)
                label = st.sidebar.empty()
                try:
                    blocks = indexer.index_folder(
                        folder.strip(), progress=_progress_factory(bar, label))
                    st.session_state.blocks = blocks
                    st.session_state.index_info = (
                        f"已索引文件夹「{folder}」，共 {len(blocks)} 个文本块。")
                    label.caption("完成")
                except NotADirectoryError as e:
                    st.sidebar.error(str(e))
                except Exception as e:  # noqa: BLE001
                    st.sidebar.error(f"索引失败：{e}")
    else:
        uploaded = st.sidebar.file_uploader(
            "上传 PDF / Word", type=["pdf", "docx", "doc"],
            accept_multiple_files=True)
        if st.sidebar.button("建立索引", use_container_width=True):
            if not uploaded:
                st.sidebar.error("请先上传文件")
            else:
                tmp_dir = Path(tempfile.mkdtemp(prefix="readprice_"))
                paths = []
                for uf in uploaded:
                    p = tmp_dir / uf.name
                    p.write_bytes(uf.getbuffer())
                    paths.append(str(p))
                bar = st.sidebar.progress(0.0)
                label = st.sidebar.empty()
                try:
                    blocks = indexer.index_paths(
                        paths, progress=_progress_factory(bar, label))
                    st.session_state.blocks = blocks
                    st.session_state.index_info = (
                        f"已索引上传的 {len(uploaded)} 个文件，"
                        f"共 {len(blocks)} 个文本块。")
                    label.caption("完成")
                except Exception as e:  # noqa: BLE001
                    st.sidebar.error(f"索引失败：{e}")

    st.sidebar.divider()
    if st.sidebar.button("🗑️ 清除所有缓存", use_container_width=True,
                         type="secondary"):
        st.session_state.blocks = []
        st.session_state.index_info = ""
        cache_dir = config.CACHE_DIR
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir, ignore_errors=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
        st.sidebar.success("缓存已清除，请重新建立索引。")
        st.rerun()


def _highlight(text: str, keywords: list[str]) -> str:
    """将关键词用黄色背景标记。同时匹配裸关键词和 **加粗** 包裹的关键词。"""
    import re
    for kw in keywords:
        # 先匹配 **关键词**（LLM 可能把查询词加粗）
        pattern_bold = re.compile(re.escape(f"**{kw}**"))
        text = pattern_bold.sub(
            f'<span style="background:#ffd;padding:1px 4px;border-radius:3px"><b>{kw}</b></span>',
            text)
        # 再匹配裸关键词（仅匹配不在 <span> 标签内的）
        pattern_bare = re.compile(
            r'(?<!<b>)' + re.escape(kw) + r'(?!</b></span>)')
        text = pattern_bare.sub(
            f'<span style="background:#ffd;padding:1px 4px;border-radius:3px"><b>{kw}</b></span>',
            text)
    return text


def main():
    _init_state()
    st.title("🔎 文件快速定位工具")
    st.caption("在 PDF / Word（含扫描件）中定位内容所在的文件、页码、章节。")

    _sidebar_config()
    _sidebar_cache()
    _sidebar_source()

    if st.session_state.index_info:
        st.info(st.session_state.index_info)

    query = st.text_input(
        "④ 输入查询",
        placeholder="例如：防腐涂料的单价在哪个文件、第几页第几章")
    go = st.button("查询", type="primary")

    if go:
        cfg = st.session_state.cfg
        if not config.is_configured(cfg):
            st.error("请先在左侧配置大模型接口。")
            return
        if not st.session_state.blocks:
            st.error("请先在左侧建立文档索引。")
            return
        if not query.strip():
            st.error("请输入查询内容。")
            return

        # --- 诊断输出 ---
        blocks = st.session_state.blocks
        scanned_blocks = [b for b in blocks if b.get("scanned")]
        text_blocks = [b for b in blocks if not b.get("scanned") and b.get("text")]
        scanned_files = set(b["file"] for b in scanned_blocks)
        text_files = set(b["file"] for b in text_blocks)
        st.caption(
            f"索引状态：共 {len(blocks)} 块 | "
            f"文本块 {len(text_blocks)}（{len(text_files)} 文件）| "
            f"扫描块 {len(scanned_blocks)}（{len(scanned_files)} 文件）"
        )
        if scanned_files:
            with st.expander("扫描文件列表"):
                for fn in sorted(scanned_files):
                    cnt = sum(1 for b in scanned_blocks if b["file"] == fn)
                    ocr_done = sum(1 for b in scanned_blocks
                                   if b["file"] == fn and b.get("text"))
                    st.write(f"  {fn}: {cnt} 页 (已OCR: {ocr_done})")
        # --- 诊断输出结束 ---

        with st.spinner("正在检索并定位..."):
            try:
                results, ocr_errors = locator.locate(
                    st.session_state.blocks, query.strip(), cfg)
            except Exception as e:
                st.error(f"搜索出错：{type(e).__name__}: {e}")
                return

        # --- 搜索诊断 ---
        with st.expander("搜索诊断"):
            # 检查 OCR 后哪些文件有匹配
            kws = locator.keywords(query.strip())
            st.write(f"**关键词**: {kws}")
            file_match_counts: dict[str, int] = {}
            for b in st.session_state.blocks:
                text = b.get("text", "")
                if not text:
                    continue
                score = sum(text.count(k) for k in kws)
                if score > 0:
                    fn = b["file"]
                    file_match_counts[fn] = file_match_counts.get(fn, 0) + score
            if file_match_counts:
                st.write(f"**关键词命中的文件** ({len(file_match_counts)} 个):")
                for fn, cnt in sorted(file_match_counts.items(),
                                       key=lambda x: -x[1]):
                    st.write(f"  - {fn}: 命中 {cnt} 次")
            else:
                st.write("**没有文件包含这些关键词**（OCR 可能未完成或识别质量不佳）")
            scanned_done = sum(1 for b in st.session_state.blocks
                               if b.get("scanned") and b.get("text"))
            scanned_total = sum(1 for b in st.session_state.blocks
                                if b.get("scanned"))
            st.write(f"**扫描页 OCR 状态**: {scanned_done}/{scanned_total} 已完成")
        # --- 诊断结束 ---

        for err in ocr_errors:
            st.warning(err)

        # 显示 OCR 失败的页面
        ocr_block_errors = [
            b["_ocr_error"] for b in st.session_state.blocks
            if b.get("_ocr_error")
        ]
        for err in ocr_block_errors:
            st.warning(err)

        # 按文件分组，逐文件流式渲染
        if not results:
            st.info("未在已索引文档中找到匹配内容。可尝试更换关键词或确认已建立索引。")
        else:
            from collections import OrderedDict
            grouped: OrderedDict[str, list[dict]] = OrderedDict()
            for r in results:
                grouped.setdefault(r.get("file", "?"), []).append(r)

            kws = locator.keywords(query.strip())
            total_files = len(grouped)
            st.success(f"找到 {total_files} 个文件共 {len(results)} 处命中：")

            for idx, (fn, items) in enumerate(grouped.items(), 1):
                pages = [r.get("page", "—") for r in items]
                pages_str = "、".join(str(p) for p in pages)
                title = f"📄 {fn} | 第{pages_str}页 ({len(items)}处)"

                with st.expander(title, expanded=idx == 1):
                    # 先显示原始匹配信息
                    for r in items:
                        page = r.get("page", "—")
                        chapter = r.get("chapter", "—")
                        st.markdown(f"**第 {page} 页** [{chapter}]")

                    # 进度提示
                    status = st.empty()
                    status.caption(f"⏳ 正在 AI 整理 ({idx}/{total_files})：{fn}")

                    # 流式调用大模型整理
                    try:
                        st.markdown("---")
                        st.markdown("**AI 整理结果：**")
                        text_area = st.empty()
                        full_text = ""
                        for chunk in llm.polish_file(
                            cfg["base_url"], cfg["api_key"], cfg["model"],
                            query.strip(), fn, items):
                            full_text += chunk
                            text_area.markdown(
                                _highlight(full_text, kws),
                                unsafe_allow_html=True)
                        status.caption(f"✅ 已完成 ({idx}/{total_files})：{fn}")
                    except Exception:
                        status.caption(f"⚠️ AI 整理失败，显示原文 ({idx}/{total_files})：{fn}")
                        for r in items:
                            snippet = r.get("snippet", "")
                            if snippet:
                                st.markdown(
                                    _highlight(snippet, kws),
                                    unsafe_allow_html=True)


if __name__ == "__main__":
    main()
