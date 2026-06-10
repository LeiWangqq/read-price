"""文件快速定位工具 — Streamlit 入口。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# ── 版本检查：libs/ 中的 .pyd 仅兼容 Python 3.12 ──────────
if sys.version_info[:2] != (3, 12):
    print(
        f"[ERROR] 当前 Python {sys.version_info.major}.{sys.version_info.minor}，"
        f"需要 Python 3.12。\n"
        f"libs/ 中的二进制包（cv2、numpy 等）仅兼容 3.12。\n"
        f"解决方案：\n"
        f"  1. 安装 Python 3.12: https://www.python.org/downloads/\n"
        f"  2. 或用当前版本重建 libs/:\n"
        f"     python -m pip install --target libs -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)

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
    if "search_results" not in st.session_state:
        st.session_state.search_results = None
    if "search_query" not in st.session_state:
        st.session_state.search_query = ""


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
    st.sidebar.header("② 缓存提取")

    # 显示缓存版本列表
    sessions = config.list_cache_sessions()
    if sessions:
        st.sidebar.caption(f"缓存共 {len(sessions)} 个版本：")
        for s in sessions[:5]:  # 最多显示 5 个
            size_mb = s["total_bytes"] / (1024 * 1024)
            st.sidebar.caption(
                f"  {s['time_str']}  {s['file_count']} 文件  {size_mb:.1f} MB")
    else:
        st.sidebar.caption("暂无缓存。请先建立索引。")

    # 加载方式
    cache_mode = st.sidebar.radio(
        "加载方式", ["从最新缓存加载", "从历史缓存加载"],
        horizontal=True, key="cache_load_mode")

    if cache_mode == "从最新缓存加载":
        # 从文件夹 / 单个文件加载（搜索最新会话或全部缓存）
        sub_mode = st.sidebar.radio(
            "范围", ["文件夹", "单个文件"], horizontal=True,
            key="cache_sub_mode")

        if sub_mode == "文件夹":
            if "cache_folder_input" not in st.session_state:
                st.session_state.cache_folder_input = ""
            if st.sidebar.button("📁 浏览选择文件夹", use_container_width=True,
                                 key="btn_cache_browse"):
                picked = _pick_folder()
                if picked:
                    st.session_state.cache_folder_input = picked
            folder = st.sidebar.text_input(
                "文件夹路径", key="cache_folder_input",
                placeholder=r"D:\价格台账")
            if st.sidebar.button("加载缓存", use_container_width=True,
                                 type="primary", key="btn_cache_folder"):
                if not folder.strip():
                    st.sidebar.error("请填写文件夹路径")
                else:
                    # 优先搜索最新会话，再搜索全部缓存
                    latest = config.latest_cache_session()
                    blocks = config.load_cached_blocks_for(
                        folder.strip(), session_dir=latest)
                    if not blocks:
                        blocks = config.load_cached_blocks_for(folder.strip())
                    if blocks:
                        st.session_state.blocks = blocks
                        st.session_state.index_info = (
                            f"已从缓存加载 {len(blocks)} 个文本块。")
                        st.rerun()
                    else:
                        st.sidebar.warning("该文件夹下无缓存数据，请先建立索引。")
        else:
            if "cache_file_input" not in st.session_state:
                st.session_state.cache_file_input = ""
            if st.sidebar.button("📁 浏览选择文件", use_container_width=True,
                                 key="btn_cache_file_browse"):
                picked = _pick_file()
                if picked:
                    st.session_state.cache_file_input = picked
            single = st.sidebar.text_input(
                "文件路径", key="cache_file_input",
                placeholder=r"D:\合同\价格表.pdf")
            if st.sidebar.button("加载缓存", use_container_width=True,
                                 type="primary", key="btn_cache_file"):
                if not single.strip():
                    st.sidebar.error("请填写文件路径")
                elif not Path(single.strip()).is_file():
                    st.sidebar.error(f"文件不存在：{single.strip()}")
                else:
                    latest = config.latest_cache_session()
                    blocks = config.load_cached_blocks_for(
                        single.strip(), session_dir=latest)
                    if not blocks:
                        blocks = config.load_cached_blocks_for(single.strip())
                    if blocks:
                        st.session_state.blocks = blocks
                        st.session_state.index_info = (
                            f"已从缓存加载 {len(blocks)} 个文本块。")
                        st.rerun()
                    else:
                        st.sidebar.warning("该文件无缓存数据，请先建立索引。")

    elif cache_mode == "从历史缓存加载":
        if not sessions:
            st.sidebar.info("暂无历史缓存。")
        else:
            options = [
                f"{s['time_str']}  ({s['file_count']} 文件, "
                f"{s['total_bytes'] / (1024*1024):.1f} MB)"
                for s in sessions
            ]
            sel = st.sidebar.selectbox("选择历史版本", options, key="session_select")
            sel_idx = options.index(sel) if sel in options else 0
            sel_session = sessions[sel_idx]

            col_load, col_del = st.columns(2)
            with col_load:
                if st.button("📥 加载选中版本", use_container_width=True,
                             type="primary", key="btn_load_session"):
                    all_blocks = config.load_session_blocks(sel_session["path"])
                    if all_blocks:
                        st.session_state.blocks = all_blocks
                        st.session_state.index_info = (
                            f"已加载历史版本 {sel_session['time_str']}，"
                            f"共 {len(all_blocks)} 个文本块。")
                        st.rerun()
                    else:
                        st.sidebar.warning("该版本缓存为空。")
            with col_del:
                if st.button("🗑️ 删除此版本", use_container_width=True,
                             key="btn_del_session"):
                    if config.delete_cache_session(sel_session["path"]):
                        st.sidebar.success(f"已删除 {sel_session['time_str']}")
                        st.rerun()

    # 重建索引 & 清除所有缓存
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🔄 重建索引", use_container_width=True,
                     key="btn_rebuild"):
            st.session_state["_force_rebuild"] = True
            st.sidebar.info("已标记重建，下次扫描将跳过缓存")
    with col_b:
        if st.sidebar.button("🗑️ 清除所有缓存", use_container_width=True,
                             type="secondary", key="btn_clear_all_cache"):
            st.session_state.blocks = []
            st.session_state.index_info = ""
            cache_dir = config.CACHE_DIR
            if cache_dir.exists():
                import shutil
                shutil.rmtree(cache_dir, ignore_errors=True)
                cache_dir.mkdir(parents=True, exist_ok=True)
            st.sidebar.success("缓存已清除。")
            st.rerun()

    # 首次自动加载（最新会话，或旧格式平铺 pkl）
    if not st.session_state.blocks:
        all_blocks = []
        loaded_from = ""
        if sessions:
            latest_session = sessions[0]
            all_blocks = config.load_session_blocks(latest_session["path"])
            loaded_from = latest_session["time_str"]
        elif config.CACHE_DIR.exists():
            # 兼容旧格式：直接加载平铺在 cache/ 下的 pkl
            import pickle
            for pkl_file in config.CACHE_DIR.glob("*.pkl"):
                try:
                    all_blocks.extend(pickle.loads(pkl_file.read_bytes()))
                except (pickle.PickleError, OSError):
                    continue
            loaded_from = "旧格式缓存"
        if all_blocks:
            st.session_state.blocks = all_blocks
            st.session_state.index_info = (
                f"已自动加载 {loaded_from}，共 {len(all_blocks)} 个文本块。")


def _progress_factory(bar, label):
    def cb(idx, total, name):
        ratio = idx / total if total else 1.0
        bar.progress(min(ratio, 1.0))
        label.caption(f"正在处理 ({idx}/{total})：{name}")
    return cb


def _pick_folder() -> str:
    """弹出本机系统文件夹选择对话框，返回所选路径（取消/失败返回空串）。"""
    return _tk_dialog("filedialog.askdirectory()")


def _pick_file() -> str:
    """弹出本机系统文件选择对话框，返回所选路径（取消/失败返回空串）。"""
    return _tk_dialog(
        "filedialog.askopenfilename(filetypes=["
        "('PDF/Word','*.pdf *.docx *.doc'),('All','*.*')])")


def _tk_dialog(expr: str) -> str:
    """在子进程中运行 tkinter 对话框，通过临时文件回传路径。"""
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
        f"p = {expr}\n"
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
    source = st.sidebar.radio(
        "选择方式", ["本地文件夹", "上传文件"], horizontal=True)
    force = st.session_state.pop("_force_rebuild", False)

    if source == "本地文件夹":
        if "folder_input" not in st.session_state:
            st.session_state.folder_input = ""
        if st.sidebar.button("📁 浏览选择文件夹", use_container_width=True,
                             key="btn_src_browse"):
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
                _do_index(source, folder.strip(), force)

    elif source == "上传文件":
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
                _do_index(source, paths, force)


def _highlight(text: str, keywords: list[str]) -> str:
    """将关键词用黄色背景标记。单遍替换策略，避免多轮嵌套。"""
    import html as html_mod
    import re
    # 先对文本做 HTML 转义，防止注入
    text = html_mod.escape(text)
    # 将 Markdown 加粗语法 **xxx** 转为 <b>xxx</b>，以便后续统一处理
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    for kw in keywords:
        safe_kw = html_mod.escape(kw)
        pattern = re.compile(re.escape(safe_kw), re.IGNORECASE)
        text = pattern.sub(
            f'<span style="background:#ffd;padding:1px 4px;border-radius:3px">'
            f'<b>{safe_kw}</b></span>',
            text)
    return text


def _render_snippet_card(items: list[dict], keywords: list[str]) -> None:
    """非 LLM 回退：生成页码+内容表格。"""
    if not items:
        return
    lines = ["| 页码 | 内容 |", "|:----:|------|"]
    for r in items:
        page = r.get("page", "—")
        snippet = r.get("snippet", "")[:200]
        highlighted = _highlight(snippet, keywords)
        lines.append(f"| {page} | {highlighted} |")
    st.markdown("\n".join(lines), unsafe_allow_html=True)


def _do_index(source, paths_or_folder, force: bool, session_dir=None):
    """统一索引入口：显示进度条，更新 session_state。"""
    # 如果没有指定会话，创建新的时间戳会话
    if session_dir is None:
        session_dir = config.new_cache_session()
    bar = st.sidebar.progress(0.0)
    label = st.sidebar.empty()
    try:
        if source == "本地文件夹":
            blocks = indexer.index_folder(
                paths_or_folder, progress=_progress_factory(bar, label),
                force=force, session_dir=session_dir)
            desc = f"文件夹「{paths_or_folder}」"
        elif source == "单个文件":
            blocks = indexer.index_paths(
                [paths_or_folder], progress=_progress_factory(bar, label),
                force=force, session_dir=session_dir)
            desc = f"文件「{Path(paths_or_folder).name}」"
        else:  # 上传文件
            blocks = indexer.index_paths(
                paths_or_folder, progress=_progress_factory(bar, label),
                force=force, session_dir=session_dir)
            desc = f"{len(paths_or_folder)} 个上传文件"
        st.session_state.blocks = blocks
        st.session_state.index_info = (
            f"已索引{desc}，共 {len(blocks)} 个文本块。"
            f"（缓存版本：{session_dir.name}）")
        label.caption("完成")
    except NotADirectoryError as e:
        st.sidebar.error(str(e))
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"索引失败：{e}")


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
        # 清空旧结果
        st.session_state.search_results = None
        st.session_state.search_query = ""

        if not st.session_state.blocks:
            st.error("请先在左侧建立文档索引。")
            return
        if not query.strip():
            st.error("请输入查询内容。")
            return

        blocks = st.session_state.blocks

        with st.spinner("正在检索并定位..."):
            try:
                results, ocr_errors = locator.locate(blocks, query.strip(), cfg)
            except Exception as e:
                st.error(f"搜索出错：{type(e).__name__}: {e}")
                return

        # 存入 session_state，下次查询前会被清空
        st.session_state.search_query = query.strip()
        st.session_state.search_results = {
            "results": results,
            "ocr_errors": ocr_errors,
        }

    # 从 session_state 渲染结果（新查询时上面已清空，此处不会输出旧结果）
    data = st.session_state.search_results
    if data is None:
        return

    results = data["results"]
    ocr_errors = data["ocr_errors"]
    query_text = st.session_state.search_query
    blocks = st.session_state.blocks
    cfg = st.session_state.cfg

    for err in ocr_errors:
        st.warning(err)
    for b in blocks:
        if b.get("_ocr_error"):
            st.warning(b["_ocr_error"])

    # 诊断（折叠）
    with st.expander("诊断信息", expanded=False):
        kws = locator.keywords(query_text)
        ocr_done = sum(1 for b in blocks if b.get("scanned") and b.get("text"))
        ocr_total = sum(1 for b in blocks if b.get("scanned"))
        st.write(f"关键词: {kws} | 命中: {len(results)} 处 | OCR: {ocr_done}/{ocr_total}")

    if not results:
        st.info("未在已索引文档中找到匹配内容。可尝试更换关键词或确认已建立索引。")
        return

    from collections import OrderedDict
    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for r in results:
        grouped.setdefault(r.get("file", "?"), []).append(r)

    kws = locator.keywords(query_text)
    llm_ready = config.is_configured(cfg)
    total_files = len(grouped)
    st.success(f"找到 {total_files} 个文件共 {len(results)} 处命中：")

    # 总览表格
    summary_lines = ["| 文件 | 页码 |", "|------|------|"]
    for fn, items in grouped.items():
        pages = sorted({r.get("page", "—") for r in items}, key=lambda x: (x == "—", x))
        summary_lines.append(f"| {fn} | {'、'.join(str(p) for p in pages)} |")
    st.markdown("\n".join(summary_lines))

    for idx, (fn, items) in enumerate(grouped.items(), 1):
        pages = [r.get("page", "—") for r in items]
        pages_str = "、".join(str(p) for p in pages)
        title = f"📄 {fn} | 第{pages_str}页 ({len(items)}处)"

        with st.expander(title, expanded=idx == 1):
            if llm_ready:
                status = st.empty()
                status.caption(f"⏳ AI 整理 ({idx}/{total_files})：{fn}")
                try:
                    text_area = st.empty()
                    full_text = ""
                    for chunk in llm.polish_file(
                        cfg["base_url"], cfg["api_key"], cfg["model"],
                        query_text, fn, items):
                        full_text += chunk
                        text_area.markdown(full_text)
                    status.caption(f"✅ 完成 ({idx}/{total_files})：{fn}")
                except llm.LLMError as e:
                    status.caption(f"⚠️ AI 整理失败 ({idx}/{total_files})")
                    st.warning(str(e))
                    _render_snippet_card(items, kws)
                except Exception as e:
                    status.caption(f"⚠️ AI 整理异常 ({idx}/{total_files})")
                    st.warning(f"{type(e).__name__}: {e}")
                    _render_snippet_card(items, kws)
            else:
                _render_snippet_card(items, kws)


if __name__ == "__main__":
    main()
