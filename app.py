"""药品检索 v3 — 主入口。

模块拆分：
- rules.py   规则字典（分类/主治/同义词）
- search.py  搜索算法 + 数据加载
- render.py  UI 渲染（图标/卡片/柜位图）
- style.css  样式
- app.py     Streamlit 装配 + 状态管理
"""
import datetime as dt
import html
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import search as S
import render as R

BASE_DIR = Path(__file__).parent
DATA_PATH = BASE_DIR / "data.csv"
STYLE_PATH = BASE_DIR / "style.css"
RECENT_PATH = BASE_DIR / "recent.json"
LOG_PATH = BASE_DIR / "search_log.csv"

DEFAULT_PAGE_SIZE = 12
COMPACT_PAGE_SIZE = 20
RECENT_MAX = 8


# ──────────────────────────────────────────────────────────────
#  页面配置 + 样式
# ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="药品检索", page_icon="·", layout="centered")


@st.cache_data(show_spinner=False)
def load_css(mtime):
    _ = mtime
    if STYLE_PATH.exists():
        return STYLE_PATH.read_text(encoding="utf-8")
    return ""


_css_mtime = STYLE_PATH.stat().st_mtime_ns if STYLE_PATH.exists() else 0
st.markdown(f"<style>{load_css(_css_mtime)}</style>", unsafe_allow_html=True)

# 手机浏览器从后台/其他页面返回时，Streamlit 的 websocket 偶尔会保持旧状态。
# 只在浏览器恢复 BFCache 页面时自动刷新一次，避免用户手动刷新。
components.html(
    """
    <script>
    window.addEventListener('pageshow', function(event) {
        if (event.persisted) {
            window.location.reload();
        }
    });
    </script>
    """,
    height=0,
)


# ──────────────────────────────────────────────────────────────
#  数据加载（缓存）
# ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def cached_load(mtime_ns, size):
    _ = (mtime_ns, size)
    return S.load_data(DATA_PATH)


@st.cache_data(show_spinner=False, max_entries=128)
def cached_search(mtime_ns, size, query):
    _ = (mtime_ns, size)
    df, _issues = cached_load(mtime_ns, size)
    if df is None:
        import pandas as pd
        return pd.DataFrame()
    return S.search(df, query)


@st.cache_data(show_spinner=False, max_entries=64)
def cached_batch(mtime_ns, size, query):
    _ = (mtime_ns, size)
    df, _issues = cached_load(mtime_ns, size)
    if df is None:
        import pandas as pd
        return pd.DataFrame(), []
    return S.search_batch(df, query)


@st.cache_data(show_spinner=False, max_entries=64)
def cached_fallback(mtime_ns, size, query):
    _ = (mtime_ns, size)
    df, _issues = cached_load(mtime_ns, size)
    if df is None:
        import pandas as pd
        return pd.DataFrame(), []
    return S.fallback_suggestions(df, query)


# ──────────────────────────────────────────────────────────────
#  最近搜索：持久化到 json
# ──────────────────────────────────────────────────────────────
def load_recent():
    try:
        if RECENT_PATH.exists():
            data = json.loads(RECENT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(x) for x in data][:RECENT_MAX]
    except Exception:
        pass
    return []


def save_recent(items):
    try:
        RECENT_PATH.write_text(
            json.dumps(items[:RECENT_MAX], ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def push_recent(term):
    term = term.strip()
    if not term:
        return
    recent = st.session_state.recent
    if term in recent:
        recent.remove(term)
    recent.insert(0, term)
    st.session_state.recent = recent[:RECENT_MAX]
    save_recent(st.session_state.recent)


def sync_query():
    """把输入框内容同步为真正用于搜索的 query。

    手机端输入框有时不会在清空后立即触发完整 rerun；按钮也会调用这个函数，
    所以连续查询时不用刷新页面。
    """
    st.session_state.active_query = st.session_state.get("query_input", "").strip()


def clear_query():
    st.session_state.query_input = ""
    st.session_state.active_query = ""
    st.session_state.expanded_query = ""


# ──────────────────────────────────────────────────────────────
#  搜索日志：记录未命中 query（数据改进的金矿）
# ──────────────────────────────────────────────────────────────
def log_search(query, hit_count):
    try:
        new = not LOG_PATH.exists()
        with LOG_PATH.open("a", encoding="utf-8") as f:
            if new:
                f.write("time,query,hits\n")
            ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            safe_q = str(query).replace('"', "'").replace("\n", " ")
            f.write(f'{ts},"{safe_q}",{hit_count}\n')
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
#  渲染辅助
# ──────────────────────────────────────────────────────────────
def pick_density(total):
    """按结果数选择密度和初屏条数。"""
    if total <= 4:
        return "spacious", total
    if total <= 12:
        return "default", total
    return "compact", min(COMPACT_PAGE_SIZE, total)


def render_result_block(cards, df, density, show_n, note):
    st.markdown(f"""
    <div class="result-meta">
        <div class="result-meta-count"><b>{show_n}</b> / {len(cards)} 结果</div>
        <div class="result-meta-note">{html.escape(note)}</div>
    </div>
    """, unsafe_allow_html=True)

    rows_html = []
    for i, card in cards.head(show_n).iterrows():
        rows_html.append(R.render_row(i + 1, card, df, density=density))
    st.markdown("".join(rows_html), unsafe_allow_html=True)

    total = len(cards)
    if total > show_n:
        if st.button(f"展开剩余 {total - show_n} 条", use_container_width=True):
            extra = []
            for i, card in cards.iloc[show_n:].iterrows():
                extra.append(R.render_row(i + 1, card, df, density=density))
            st.markdown("".join(extra), unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
#  主流程
# ──────────────────────────────────────────────────────────────
def main():
    fp = S.data_fingerprint(DATA_PATH)
    df, issues = cached_load(*fp)
    if df is None:
        st.error(issues[0] if issues else "数据加载失败")
        return

    # 顶部
    n_loc = df["__loc_label"].nunique()
    st.markdown(f"""
    <div class="masthead">
        <div class="masthead-title">药品检索</div>
        <div class="masthead-meta">{len(df)} ITEMS · {n_loc} LOCS</div>
    </div>
    """, unsafe_allow_html=True)

    # 状态初始化
    if "recent" not in st.session_state:
        st.session_state.recent = load_recent()
    if "query_input" not in st.session_state:
        st.session_state.query_input = ""
    if "active_query" not in st.session_state:
        st.session_state.active_query = st.session_state.query_input.strip()
    if "expanded_query" not in st.session_state:
        st.session_state.expanded_query = ""

    st.text_input(
        "搜索",
        placeholder="药名 · 品牌 · 货号后四位 · 症状",
        label_visibility="collapsed",
        key="query_input",
        on_change=sync_query,
    )

    action_cols = st.columns([1, 1, 4])
    with action_cols[0]:
        if st.button("搜索", use_container_width=True):
            sync_query()
            st.rerun()
    with action_cols[1]:
        if st.button("清空", use_container_width=True):
            clear_query()
            st.rerun()

    query = st.session_state.get("active_query", "").strip()

    st.markdown("""
    <div class="input-hint">
        <span>4位数字 = 货号后四位</span>
        <span>批量查询用 / 分隔</span>
    </div>
    """, unsafe_allow_html=True)

    # 数据校验：仅在有严重问题时显示（validate_data 已过滤）
    if issues:
        with st.expander(f"数据校验 · {len(issues)} 项提示", expanded=False):
            for issue in issues:
                st.write(f"· {issue}")

    if not query.strip():
        render_home(df)
        return

    is_batch = "/" in query
    if is_batch:
        results, missing = cached_batch(*fp, query)
    else:
        results = cached_search(*fp, query)
        missing = []

    if results.empty:
        log_search(query, 0)
        render_empty(query, df, fp)
    else:
        cards = S.to_cards(results)
        if is_batch:
            cards = S.batch_sort_cards(cards).reset_index(drop=True)
        log_search(query, len(cards))
        push_recent(query)
        render_hits(cards, df, is_batch, missing)

    render_colophon(df, fp[1])


def render_home(df):
    """空查询主页：最近搜索 + 提示。"""
    recent = st.session_state.recent
    if recent:
        st.markdown("<div class='section-head'>最近</div>", unsafe_allow_html=True)
        cols = st.columns(min(len(recent), 4))
        for i, term in enumerate(recent[:4]):
            with cols[i]:
                if st.button(term, key=f"recent_{i}", use_container_width=True):
                    st.session_state.query_input = term
                    st.session_state.active_query = term
                    st.rerun()
    else:
        st.markdown("""
        <div class="quick-row">
            <span class="quick-label">提示</span>
            <span class="quick-item">输入药名汉字</span>
            <span class="quick-item">货号后 4 位</span>
            <span class="quick-item">症状如「咳嗽」</span>
        </div>
        """, unsafe_allow_html=True)
    render_colophon(df, DATA_PATH.stat().st_size)


def render_hits(cards, df, is_batch, missing):
    total = len(cards)
    density, show_n = pick_density(total)

    note_parts = []
    if total > show_n:
        note_parts.append(f"还有 {total - show_n} 条")
    if missing:
        note_parts.append(f"未找到 {len(missing)} 项")
    if not note_parts:
        note_parts.append(
            {"compact": "紧凑视图", "spacious": "舒展视图", "default": "标准视图"}[density]
        )
    note = " · ".join(note_parts)

    render_result_block(cards, df, density, show_n, note)

    if missing:
        st.markdown(
            f"<div class='input-hint' style='margin-top:10px'>"
            f"<span>未找到</span><span>{html.escape(' / '.join(missing))}</span></div>",
            unsafe_allow_html=True,
        )


def render_empty(query, df, fp):
    query_code = S.normalize_code(query)
    is_code = bool(query_code) and len(query_code) >= 4 and query_code == S.normalize_text(query)
    title = f"货号 {html.escape(query_code)} 未命中" if is_code else f"「{html.escape(query)}」未命中"
    subtitle = R._fallback_subtitle(query)

    st.markdown(f"""
    <div class="empty">
        <div class="empty-mark">— NO MATCH —</div>
        <div class="empty-title">{title}</div>
        <div class="empty-sub">{html.escape(subtitle)}</div>
    </div>
    """, unsafe_allow_html=True)

    suggestions_raw, zone_hints = cached_fallback(*fp, query)

    if zone_hints:
        hints_html = "".join(f"<span>{html.escape(h)}</span>" for h in zone_hints)
        st.markdown(f"""
        <div class="zone-hints">
            <div class="zone-hint-label">可能相关分区</div>
            <div class="zone-hint-list">{hints_html}</div>
        </div>
        """, unsafe_allow_html=True)

    if not suggestions_raw.empty:
        cards = S.to_cards(suggestions_raw, max_cards=6)
        density = "spacious" if len(cards) <= 3 else "default"
        st.markdown(f"""
        <div class="result-meta">
            <div class="result-meta-count"><b>{len(cards)}</b> 条推测</div>
            <div class="result-meta-note">FALLBACK</div>
        </div>
        """, unsafe_allow_html=True)
        rows_html = []
        for i, card in cards.iterrows():
            rows_html.append(R.render_row(i + 1, card, df, density=density))
        st.markdown("".join(rows_html), unsafe_allow_html=True)


def render_colophon(df, size):
    st.markdown(f"""
    <div class="colophon">
        <span>药品检索 · v3</span>
        <span>{len(df)} REC · {size // 1024} KB</span>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
