"""药品检索 v5 — 主入口。

模块拆分：
- rules.py   规则字典（分类/主治/同义词）
- search.py  搜索算法 + 数据加载
- render.py  UI 渲染（图标/卡片/柜位图）
- style.css  样式
- app.py     Streamlit 装配 + 状态管理

v5 重写要点（针对手机端体验问题，逐条对应）：
1. 「飘红/报错」「清空键无功能」「历史记录无功能」：
   根因是在控件实例化后又用 session_state 改写控件 key（Streamlit 禁止，会抛
   StreamlitAPIException）。本版改为 **回调模式**：所有对输入框 key 的写入只在
   on_change / on_click 回调里发生（回调先于控件重建执行，是合法时机）。
2. 「切后台重进后第一次输入被吞、要输两次」：
   根因是旧版 pageshow 时 window.location.reload() 强制整页刷新，与手机键盘
   「失焦才提交」赛跑，把没来得及提交的输入吞掉。本版 **彻底移除强制刷新**，
   改为把查询写进 URL 查询参数 ?q=，URL 成为「唯一真相源」——切后台/恢复/断线
   重连都能从 URL 还原，不依赖易失的 websocket 会话状态。
3. 「上次查询内容不清空」：URL 不带 ?q= 即为初始态；清空键会同时清掉 URL 参数。
4. 「重新加载过慢」：去掉强制 reload，BFCache 恢复时直接复用页面；仅做一次轻量
   可见性探活（不刷新页面），避免长时间后台导致的连接僵死。
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

# 输入框控件 key（与「已提交查询」分离：控件 key 只在回调里写）
BOX_KEY = "qbox"
QP_KEY = "q"  # URL 查询参数名 ?q=...


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

# 轻量探活：手机切后台再回来时，如果 websocket 已断，仅触发一次「温和」重连，
# 不强制 reload、不动输入框，避免吞输入与白屏。BFCache 恢复(persisted)时也不再
# 强制刷新——因为查询已存在 URL，页面状态可直接复用。
components.html(
    """
    <script>
    (function () {
      // 不再 reload BFCache 页面；只在长时间隐藏后回前台时，让 Streamlit 自检连接。
      var hiddenAt = null;
      document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') {
          hiddenAt = Date.now();
        } else if (document.visibilityState === 'visible') {
          // 后台超过 ~5 分钟，连接很可能已僵死：此时才刷新一次以恢复。
          // 由于查询在 URL(?q=)，刷新后内容会自动还原，不会丢输入。
          if (hiddenAt && (Date.now() - hiddenAt > 300000)) {
            window.location.reload();
          }
          hiddenAt = null;
        }
      });
    })();
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


@st.cache_data(show_spinner=False, max_entries=256)
def cached_search(mtime_ns, size, query):
    _ = (mtime_ns, size)
    df, _issues = cached_load(mtime_ns, size)
    if df is None:
        import pandas as pd
        return pd.DataFrame()
    return S.search(df, query)


@st.cache_data(show_spinner=False, max_entries=128)
def cached_batch(mtime_ns, size, query):
    _ = (mtime_ns, size)
    df, _issues = cached_load(mtime_ns, size)
    if df is None:
        import pandas as pd
        return pd.DataFrame(), []
    return S.search_batch(df, query)


@st.cache_data(show_spinner=False, max_entries=128)
def cached_fallback(mtime_ns, size, query):
    _ = (mtime_ns, size)
    df, _issues = cached_load(mtime_ns, size)
    if df is None:
        import pandas as pd
        return pd.DataFrame(), []
    return S.fallback_suggestions(df, query)


# ──────────────────────────────────────────────────────────────
#  URL 查询参数：唯一真相源
# ──────────────────────────────────────────────────────────────
def get_url_query():
    """从 URL ?q= 读取当前查询（兼容新旧 Streamlit API）。"""
    try:
        v = st.query_params.get(QP_KEY, "")
        if isinstance(v, list):
            v = v[0] if v else ""
        return str(v or "").strip()
    except Exception:
        try:
            qp = st.experimental_get_query_params()
            v = qp.get(QP_KEY, [""])
            return str((v[0] if isinstance(v, list) else v) or "").strip()
        except Exception:
            return ""


def set_url_query(value):
    """把查询写入 URL ?q=（空则删除该参数）。"""
    value = (value or "").strip()
    try:
        if value:
            st.query_params[QP_KEY] = value
        else:
            try:
                del st.query_params[QP_KEY]
            except Exception:
                st.query_params.clear()
    except Exception:
        try:
            if value:
                st.experimental_set_query_params(**{QP_KEY: value})
            else:
                st.experimental_set_query_params()
        except Exception:
            pass


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
    term = (term or "").strip()
    if not term:
        return
    recent = st.session_state.get("recent", [])
    if term in recent:
        recent.remove(term)
    recent.insert(0, term)
    st.session_state.recent = recent[:RECENT_MAX]
    save_recent(st.session_state.recent)


# ──────────────────────────────────────────────────────────────
#  回调（合法地修改控件 key：回调先于控件重建执行）
# ──────────────────────────────────────────────────────────────
def _commit(term):
    """把 term 设为已提交查询：写 URL，复位展开态。"""
    term = (term or "").strip()
    set_url_query(term)
    st.session_state.results_expanded = False
    st.session_state.last_query = term


def on_submit():
    """输入框回车 / 点「搜索」：用输入框当前值提交。"""
    _commit(st.session_state.get(BOX_KEY, ""))


def on_clear():
    """点「清空」：清掉输入框 + URL 查询，回到初始态。"""
    st.session_state[BOX_KEY] = ""   # 合法：在回调里改控件 key
    _commit("")


def make_recent_cb(term):
    def _cb():
        st.session_state[BOX_KEY] = term  # 合法：回调里写控件 key
        _commit(term)
    return _cb


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
def pick_density(total, is_batch=False):
    """按结果数选择密度和初屏条数。"""
    if is_batch:
        if total <= 3:
            return "spacious", total
        if total <= 20:
            return "default", total
        return "compact", min(COMPACT_PAGE_SIZE, total)
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

    total = len(cards)
    expanded = st.session_state.get("results_expanded", False)
    visible_n = total if expanded else show_n

    rows_html = []
    for i, card in cards.head(visible_n).iterrows():
        rows_html.append(R.render_row(i + 1, card, df, density=density))
    st.markdown("".join(rows_html), unsafe_allow_html=True)

    if total > show_n and not expanded:
        if st.button(
            f"展开剩余 {total - show_n} 条",
            key="btn_expand_results",
            use_container_width=True,
            on_click=lambda: st.session_state.update(results_expanded=True),
        ):
            pass


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

    # ── 状态初始化 ──
    if "recent" not in st.session_state:
        st.session_state.recent = load_recent()
    if "results_expanded" not in st.session_state:
        st.session_state.results_expanded = False
    if "last_query" not in st.session_state:
        st.session_state.last_query = None

    # URL 是唯一真相源：当前查询来自 ?q=
    url_q = get_url_query()

    # 输入框初值：首次进入用 URL 的 q 预填，让切后台回来时输入框与结果一致。
    # 之后输入框的值由用户与回调维护，不再在主体里改它的 key。
    if BOX_KEY not in st.session_state:
        st.session_state[BOX_KEY] = url_q

    st.text_input(
        "搜索",
        placeholder="药名 · 品牌 · 货号后四位 · 症状",
        label_visibility="collapsed",
        key=BOX_KEY,
        on_change=on_submit,
    )

    action_cols = st.columns([1, 1, 4])
    with action_cols[0]:
        st.button("搜索", key="btn_search", use_container_width=True, on_click=on_submit)
    with action_cols[1]:
        st.button("清空", key="btn_clear", use_container_width=True, on_click=on_clear)

    st.markdown("""
    <div class="input-hint">
        <span>4位数字 = 货号后四位</span>
        <span>多药用 / , 。 分隔</span>
    </div>
    """, unsafe_allow_html=True)

    # 数据校验：仅在有严重问题时显示（validate_data 已过滤）
    if issues:
        with st.expander(f"数据校验 · {len(issues)} 项提示", expanded=False):
            for issue in issues:
                st.write(f"· {issue}")

    # 真正用于搜索的 query = URL 的 q
    query = url_q

    if not query:
        render_home(df)
        return

    # 查询变化时复位展开态（用 URL 比对，跨刷新也稳定）
    if st.session_state.last_query != query:
        st.session_state.results_expanded = False
        st.session_state.last_query = query

    is_batch = S.has_batch_sep(query)
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
    recent = st.session_state.get("recent", [])
    n_recent = min(len(recent), 4)
    if n_recent >= 1:
        head_cols = st.columns([3, 1])
        with head_cols[0]:
            st.markdown("<div class='section-head'>最近</div>", unsafe_allow_html=True)
        with head_cols[1]:
            st.button(
                "清除",
                key="btn_clear_recent",
                use_container_width=True,
                on_click=_clear_recent,
            )
        cols = st.columns(n_recent)
        for i, term in enumerate(recent[:n_recent]):
            with cols[i]:
                st.button(
                    term,
                    key=f"recent_{i}",
                    use_container_width=True,
                    on_click=make_recent_cb(term),
                )
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


def _clear_recent():
    st.session_state.recent = []
    save_recent([])


def _count_cabinets(cards):
    """统计结果涉及多少个不同柜子。"""
    cabs = set()
    for _, card in cards.iterrows():
        for loc in (card["位置列表"] or []):
            p = R._parse_loc(loc)
            if p:
                cabs.add((p[0], p[1]))
    return len(cabs)


def render_hits(cards, df, is_batch, missing):
    total = len(cards)
    density, show_n = pick_density(total, is_batch=is_batch)

    # 位置总览图：结果分布在多个柜子时，先给一张"全都在哪"的总览，
    # 让找药一眼定位（尤其头孢这类大类药）。单柜或结果很少时不需要。
    # 触发：≥5 条且 ≥2 柜，或 ≥3 柜（即使条数少，跨柜多也值得给图）。
    n_cab = _count_cabinets(cards)
    show_overview = (total >= 5 and n_cab >= 2) or (n_cab >= 3)
    if show_overview:
        st.markdown(R.overview_map_html(cards, df), unsafe_allow_html=True)

    # 多药检索：再给一份 货号 + 物理位置 的文字概览（高信息密度，便于逐项核对）
    if is_batch and total >= 2:
        st.markdown(R.batch_summary_html(cards), unsafe_allow_html=True)

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
        <span>药品检索 · v5</span>
        <span>{len(df)} REC · {size // 1024} KB</span>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
