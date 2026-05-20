import html
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import streamlit as st


REQUIRED_COLUMNS = ["位置", "段号", "商品名", "货号"]
MAX_RESULTS = 40
DATA_PATH = Path(__file__).parent / "data.csv"

CATEGORY_LABELS = {
    "ointment": "膏/凝胶",
    "liquid": "洗液/溶液",
    "device": "器材",
    "pill": "片/胶囊",
    "granule": "颗粒/散剂",
    "oral_liquid": "口服液",
    "eye": "眼用",
    "spray": "喷雾/吸入",
    "patch": "贴膏",
    "box": "药品",
}


st.set_page_config(page_title="药品检索", page_icon="💊", layout="centered")

st.markdown(
    """
<style>
:root {
    --ink: #17242b;
    --muted: #64727a;
    --line: #dde6e8;
    --soft: #f6f9f9;
    --brand: #286f72;
    --warn: #9a6a13;
    --danger: #a54236;
}

* {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "Microsoft YaHei", sans-serif;
}
.stApp { background: #fbfdfd; }
.block-container { padding-top: 1.25rem; max-width: 640px; }

.stTextInput > div > div > input {
    font-size: 17px !important;
    padding: 14px 15px !important;
    border-radius: 8px !important;
    border: 1.5px solid #cfdadc !important;
    background: #fff !important;
}
.stTextInput > div > div > input:focus {
    border-color: var(--brand) !important;
    box-shadow: 0 0 0 2px rgba(40,111,114,.12) !important;
}

.header { padding: .25rem 0 .95rem; }
.header h1 {
    color: var(--ink);
    font-size: 24px;
    font-weight: 760;
    margin: 0;
    line-height: 1.2;
}
.header p {
    color: var(--muted);
    font-size: 13px;
    margin: 6px 0 0;
}
.header-mark {
    display: inline-block;
    width: 8px;
    height: 19px;
    margin-right: 8px;
    transform: translateY(3px);
    border-radius: 2px;
    background: var(--brand);
}

.result-card {
    display: grid;
    grid-template-columns: 1fr 52px;
    gap: 12px;
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 12px 13px;
    margin: 9px 0;
}
.result-main { min-width: 0; }
.location {
    color: var(--brand);
    font-size: 22px;
    font-weight: 760;
    line-height: 1.18;
    letter-spacing: 0;
    word-break: break-word;
}
.drug-name {
    color: var(--ink);
    font-size: 15.5px;
    font-weight: 680;
    line-height: 1.36;
    margin-top: 7px;
    word-break: break-word;
}
.drug-code {
    color: #52636b;
    font-family: Consolas, "SFMono-Regular", monospace;
    font-size: 12.5px;
    margin-top: 4px;
}
.code-tail {
    color: var(--ink);
    border-bottom: 2px solid #8cb8b5;
    padding: 0 1px;
    font-weight: 720;
}
.badges {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin-top: 8px;
}
.badge {
    border-radius: 999px;
    font-size: 11.5px;
    font-weight: 620;
    line-height: 1;
    padding: 4px 7px;
    background: #f0f3f3;
    color: #516268;
}
.badge-warn { background: #fff6df; color: var(--warn); }
.badge-danger { background: #fff0ee; color: var(--danger); }

.icon-box {
    align-self: center;
    justify-self: end;
    width: 48px;
    height: 48px;
    border-radius: 8px;
    background: var(--soft);
    border: 1px solid #e0e8e8;
    display: grid;
    place-items: center;
}
.icon-box svg {
    width: 34px;
    height: 34px;
    stroke: #5f8585;
    fill: none;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
}

.notice {
    color: #4f6269;
    background: #f5f8f8;
    border: 1px solid #e0e8e8;
    border-radius: 8px;
    padding: 9px 11px;
    font-size: 13px;
    margin: 8px 0 12px;
}
.no-result {
    color: #61747c;
    text-align: center;
    padding: 1.9rem .8rem .9rem;
    font-size: 15px;
}
.hint-list {
    color: #52636b;
    font-size: 13px;
    background: #fff;
    border: 1px solid #e3eaeb;
    border-radius: 8px;
    padding: 10px 12px;
    margin: 8px 0 12px;
}
.stats {
    color: #8b989e;
    text-align: center;
    font-size: 12.5px;
    padding: 1rem 0;
    border-top: 1px solid #edf2f3;
    margin-top: 1rem;
}

@media (max-width: 520px) {
    .block-container { padding-left: 1rem; padding-right: 1rem; }
    .result-card { grid-template-columns: 1fr 46px; }
    .location { font-size: 20px; }
    .icon-box { width: 44px; height: 44px; }
    .icon-box svg { width: 31px; height: 31px; }
}
</style>
""",
    unsafe_allow_html=True,
)


def normalize_text(value):
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", text)


def normalize_code(value):
    return re.sub(r"\D", "", "" if value is None else str(value))


def zone_sort_value(zone):
    text = str(zone).strip()
    order = {"OTC": 0, "RX": 1, "消杀": 2, "器械": 3, "中柜": 4, "侧柜": 5, "保健区L柜": 6, "保健区长柜": 7}
    return (order.get(text, 99), text)


def natural_position_key(pos):
    text = "" if pos is None else str(pos).strip().upper()
    match = re.fullmatch(r"([A-Z]+)(\d+)(.*)", text)
    if not match:
        return (text, -1, "")
    prefix, number, suffix = match.groups()
    return (prefix, int(number), suffix)


def segment_key(seg):
    text = "" if seg is None else str(seg).strip()
    return (int(text), "") if text.isdigit() else (999999, text)


def location_label(row):
    zone = str(row.get("分区", "")).strip()
    pos = str(row.get("位置", "")).strip()
    seg = str(row.get("段号", "")).strip()
    core = f"{pos}-{seg}" if seg else pos
    return f"{zone}-{core}" if zone else core


def ordered_match_score(query, target):
    if not query or not target:
        return 0
    start = 0
    hits = 0
    for char in query:
        found = target.find(char, start)
        if found < 0:
            continue
        hits += 1
        start = found + 1
    if hits != len(query):
        return 0
    compactness = min(1.0, len(query) / max(len(target), 1))
    return 64 + compactness * 18


def classify_product(name):
    text = normalize_text(name)
    rules = [
        ("eye", ["滴眼液", "眼膏", "眼药水", "玻璃酸钠滴眼液"]),
        ("spray", ["喷雾", "吸入", "气雾剂", "雾化"]),
        ("patch", ["贴膏", "膏药", "贴片", "退热贴", "创可贴"]),
        ("ointment", ["药膏", "软膏", "乳膏", "凝胶", "搽剂", "痔疮膏"]),
        ("liquid", ["洗液", "溶液", "消毒", "碘伏", "酒精", "酊", "洗剂"]),
        ("device", ["器材", "口罩", "棉签", "纱布", "绷带", "血糖仪", "试纸", "体温计", "针头"]),
        ("oral_liquid", ["口服液", "糖浆", "合剂", "露"]),
        ("granule", ["颗粒", "散", "冲剂"]),
        ("pill", ["胶囊", "片", "丸", "栓"]),
    ]
    for category, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return category
    return "box"


def icon_svg(category):
    icons = {
        "ointment": '<svg viewBox="0 0 48 48"><path d="M12 30l16-16 8 8-16 16H12z"/><path d="M28 14l4-4 8 8-4 4"/><path d="M16 30l6 6"/></svg>',
        "liquid": '<svg viewBox="0 0 48 48"><path d="M18 14h12"/><path d="M20 14v-4h8v4"/><rect x="16" y="14" width="16" height="26" rx="4"/><path d="M16 25h16"/><path d="M23 31c0-4 3-6 3-6s3 2 3 6a3 3 0 0 1-6 0z"/></svg>',
        "device": '<svg viewBox="0 0 48 48"><rect x="10" y="16" width="28" height="20" rx="3"/><path d="M18 16v-4h12v4"/><path d="M24 21v10"/><path d="M19 26h10"/></svg>',
        "pill": '<svg viewBox="0 0 48 48"><path d="M16 30a8 8 0 0 1 0-11l3-3a8 8 0 0 1 11 11l-3 3a8 8 0 0 1-11 0z"/><path d="M20 16l12 12"/><circle cx="33" cy="33" r="5"/><circle cx="16" cy="35" r="3"/></svg>',
        "granule": '<svg viewBox="0 0 48 48"><path d="M15 10h18l-2 30H17z"/><path d="M17 18h14"/><circle cx="21" cy="28" r="1.5"/><circle cx="26" cy="31" r="1.5"/><circle cx="23" cy="35" r="1.5"/></svg>',
        "oral_liquid": '<svg viewBox="0 0 48 48"><path d="M19 10h10"/><path d="M21 10v8l-5 8v10a4 4 0 0 0 4 4h8a4 4 0 0 0 4-4V26l-5-8v-8"/><path d="M17 29h14"/></svg>',
        "eye": '<svg viewBox="0 0 48 48"><path d="M8 25s6-9 16-9 16 9 16 9-6 9-16 9-16-9-16-9z"/><circle cx="24" cy="25" r="4"/><path d="M34 9l5 5"/><path d="M39 14l-8 8"/></svg>',
        "spray": '<svg viewBox="0 0 48 48"><path d="M18 18h14v22H18z"/><path d="M21 18v-5h8v5"/><path d="M29 13h9"/><path d="M38 13v5"/><path d="M11 14h2"/><path d="M9 22h3"/><path d="M12 30h2"/></svg>',
        "patch": '<svg viewBox="0 0 48 48"><rect x="10" y="15" width="28" height="18" rx="4"/><path d="M20 15v18"/><path d="M28 15v18"/><circle cx="24" cy="24" r="2"/></svg>',
        "box": '<svg viewBox="0 0 48 48"><rect x="11" y="14" width="26" height="24" rx="3"/><path d="M16 14l3-5h10l3 5"/><path d="M24 20v12"/><path d="M18 26h12"/></svg>',
    }
    return icons.get(category, icons["box"])


def format_code(code):
    text = "" if code is None else str(code).strip()
    safe = html.escape(text)
    if len(text) <= 4:
        return f"<span class='code-tail'>{safe}</span>" if text else ""
    return f"{html.escape(text[:-4])}<span class='code-tail'>{html.escape(text[-4:])}</span>"


def validate_data(df):
    issues = []
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        issues.append(f"缺少字段：{', '.join(missing)}")
        return issues
    if "分区" not in df.columns:
        issues.append("缺少分区字段，将按旧数据兼容显示")

    for label, col in [("商品名", "商品名"), ("货号", "货号"), ("位置", "位置")]:
        empty_count = df[col].astype(str).str.strip().eq("").sum()
        if empty_count:
            issues.append(f"{empty_count} 条缺{label}")

    code_locations = df.assign(__loc=df.apply(location_label, axis=1)).groupby("货号")["__loc"].nunique()
    multi_location = int((code_locations > 1).sum())
    if multi_location:
        issues.append(f"{multi_location} 个货号存在多个物理位置，建议人工校验")
    return issues


def data_fingerprint():
    if not DATA_PATH.exists():
        return 0, 0
    stat = DATA_PATH.stat()
    return stat.st_mtime_ns, stat.st_size


@st.cache_data(show_spinner=False)
def load_data(csv_mtime_ns, csv_size):
    _ = (csv_mtime_ns, csv_size)
    csv_path = DATA_PATH
    if not csv_path.exists():
        return None, ["未找到 data.csv，请将 CSV 文件放在 app.py 同目录下"]

    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str).fillna("")
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if "分区" not in df.columns:
        df["分区"] = ""

    issues = validate_data(df)
    df["__name_norm"] = df["商品名"].map(normalize_text)
    df["__code_norm"] = df["货号"].map(normalize_code)
    if "适应症关键词" in df.columns:
        df["__keyword_norm"] = df["适应症关键词"].map(normalize_text)
    else:
        df["__keyword_norm"] = ""
    df["__search_text"] = df["__name_norm"] + df["__keyword_norm"]
    return df, issues


def score_row(row, query):
    query_norm = normalize_text(query)
    query_code = normalize_code(query)
    if not query_norm:
        return 0

    name = row["__name_norm"]
    code = row["__code_norm"]
    search_text = row["__search_text"]
    scores = []

    if query_code and code and len(query_code) >= 4:
        if query_code == code:
            scores.append(150)
        elif len(query_code) == 4 and code.endswith(query_code):
            scores.append(142)
        elif query_code in code:
            scores.append(125)
        elif len(query_code) >= 4 and query_code == code.lstrip("0"):
            scores.append(118)

    if query_norm in name:
        scores.append(115)
    if query_norm in row["__keyword_norm"]:
        scores.append(96)
    if len(query_norm) >= 2:
        scores.append(ordered_match_score(query_norm, search_text))
        ratio = SequenceMatcher(None, query_norm, name).ratio()
        if ratio >= 0.5:
            scores.append(55 + ratio * 45)

    return round(max(scores) if scores else 0, 1)


def sort_results(df):
    return df.sort_values(
        by=["匹配分", "分区", "位置", "段号"],
        ascending=[False, True, True, True],
        key=lambda s: s.map(zone_sort_value)
        if s.name == "分区"
        else (s.map(natural_position_key) if s.name == "位置" else s.map(segment_key) if s.name == "段号" else s),
    )


def search(df, query):
    if not query.strip():
        return pd.DataFrame()

    query_norm = normalize_text(query)
    query_code = normalize_code(query)
    if query_code and query_code == query_norm and len(query_code) < 4:
        return pd.DataFrame()
    if len(query_norm) <= 1 and len(query_code) < 4:
        return pd.DataFrame()

    scored = df.copy()
    scored["匹配分"] = scored.apply(lambda row: score_row(row, query), axis=1)

    if query_code and query_code == query_norm and len(query_code) == 4:
        tail_hits = scored[scored["__code_norm"].str.endswith(query_code, na=False)]
        return sort_results(tail_hits) if not tail_hits.empty else tail_hits

    if len(query_code) >= 6:
        code_hits = scored[
            scored["__code_norm"].eq(query_code)
            | scored["__code_norm"].str.contains(query_code, regex=False, na=False)
            | scored["__code_norm"].str.lstrip("0").eq(query_code)
        ]
        if not code_hits.empty:
            return sort_results(code_hits)

    scored = scored[scored["匹配分"] >= 68]
    return sort_results(scored) if not scored.empty else scored


def location_group_key(location):
    parts = str(location).split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return str(location)


def batch_sort_cards(cards_df):
    if cards_df.empty:
        return cards_df
    zone_counts = cards_df["主分区"].value_counts().to_dict()
    group_counts = cards_df["位置组"].value_counts().to_dict()
    sorted_df = cards_df.copy()
    sorted_df["__zone_count"] = sorted_df["主分区"].map(zone_counts).fillna(0)
    sorted_df["__group_count"] = sorted_df["位置组"].map(group_counts).fillna(0)
    sorted_df = sorted_df.sort_values(
        by=["__group_count", "__zone_count", "主分区", "位置组", "查询项", "匹配分"],
        ascending=[False, False, True, True, True, False],
        kind="mergesort",
    )
    return sorted_df.drop(columns=["__zone_count", "__group_count"])


def build_result_cards(results, max_results=MAX_RESULTS):
    cards = []
    grouped = results.groupby(["商品名", "货号"], dropna=False, sort=False)
    for (name, code), group in grouped:
        group = group.sort_values(
            by=["分区", "位置", "段号"],
            key=lambda s: s.map(zone_sort_value)
            if s.name == "分区"
            else (s.map(natural_position_key) if s.name == "位置" else s.map(segment_key)),
        )
        locations = []
        for _, row in group.iterrows():
            label = location_label(row)
            if label and label not in locations:
                locations.append(label)

        max_score = group["匹配分"].max()
        cards.append(
            {
                "查询项": group["查询项"].iloc[0] if "查询项" in group.columns else "",
                "商品名": name,
                "货号": code,
                "物理位置": ", ".join(locations),
                "主分区": str(group["分区"].iloc[0]) if "分区" in group.columns else "",
                "位置组": location_group_key(locations[0]) if locations else "",
                "位置数": len(locations),
                "匹配分": max_score,
                "模糊匹配": max_score < 100,
                "多位置警告": len(locations) > 1,
                "分类": classify_product(name),
            }
        )

    cards_df = pd.DataFrame(cards)
    if cards_df.empty:
        return cards_df, 0
    cards_df = cards_df.sort_values(by=["匹配分", "位置数"], ascending=[False, False])
    return cards_df.head(max_results), len(cards_df)


def search_batch(df, query):
    terms = [term.strip() for term in str(query).split("/") if term.strip()]
    if not terms:
        return pd.DataFrame(), []
    pieces = []
    missing = []
    for term in terms:
        result = search(df, term)
        if result.empty:
            missing.append(term)
            continue
        result = result.copy()
        result["查询项"] = term
        pieces.append(result)
    if not pieces:
        return pd.DataFrame(), missing
    return pd.concat(pieces, ignore_index=True), missing


def fallback_suggestions(df, query):
    query_norm = normalize_text(query)
    query_code = normalize_code(query)
    if not query_norm:
        return pd.DataFrame(), []

    scored = df.copy()
    scored["匹配分"] = scored.apply(lambda row: score_row(row, query), axis=1)
    if query_code:
        loose = scored[scored["__code_norm"].str.contains(query_code, regex=False, na=False)]
    else:
        loose = scored[scored["匹配分"] >= 45]

    zone_hints = []
    if not loose.empty:
        zone_counts = loose["分区"].fillna("").replace("", "未分区").value_counts().head(4)
        zone_hints = [f"{zone}({count})" for zone, count in zone_counts.items()]
    cards, _ = build_result_cards(sort_results(loose).head(30), max_results=5) if not loose.empty else (pd.DataFrame(), 0)
    return cards, zone_hints


def render_card(row):
    name = html.escape(str(row["商品名"]))
    code_html = format_code(row["货号"])
    locations = html.escape(str(row["物理位置"]))
    icon = icon_svg(row["分类"])
    category_label = CATEGORY_LABELS.get(str(row["分类"]), "药品")
    badges = [f"<span class='badge'>{html.escape(category_label)}</span>"]
    if row.get("查询项", ""):
        badges.append(f"<span class='badge'>查：{html.escape(str(row['查询项']))}</span>")
    if row["模糊匹配"]:
        badges.append("<span class='badge badge-warn'>模糊匹配</span>")
    if row["多位置警告"]:
        badges.append("<span class='badge badge-danger'>多位置需校验</span>")

    return f"""
    <div class="result-card">
        <div class="result-main">
            <div class="location">{locations}</div>
            <div class="drug-name">{name}</div>
            <div class="drug-code">货号 {code_html}</div>
            <div class="badges">{''.join(badges)}</div>
        </div>
        <div class="icon-box">{icon}</div>
    </div>
    """


def main():
    data_mtime_ns, data_size = data_fingerprint()
    df, issues = load_data(data_mtime_ns, data_size)
    if df is None:
        st.error(issues[0])
        return

    st.markdown(
        """
    <div class="header">
        <h1><span class="header-mark"></span>药品检索</h1>
        <p>药名 / 品牌 / 货号，4 位数字默认查货号后四位，批量用 / 分隔</p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    if issues:
        with st.expander("数据校验提示", expanded=False):
            for issue in issues:
                st.write(f"- {issue}")

    query = st.text_input("搜索", placeholder="药名 / 品牌 / 货号后四位，批量用 / 分隔", label_visibility="collapsed")

    if query:
        is_batch = "/" in query
        results, missing_terms = search_batch(df, query) if is_batch else (search(df, query), [])
        if results.empty:
            query_code = normalize_code(query)
            msg = f"没有货号后四位为 {html.escape(query_code)} 的记录" if query_code and len(query_code) == 4 else f"未找到「{html.escape(query)}」"
            st.markdown(f'<div class="no-result">{msg}</div>', unsafe_allow_html=True)
            suggestions, zones = fallback_suggestions(df, query)
            if zones:
                st.markdown(f"<div class='hint-list'>可能相关分区：{'、'.join(map(html.escape, zones))}</div>", unsafe_allow_html=True)
            if not suggestions.empty:
                st.markdown("<div class='notice'>可能相关的药品</div>", unsafe_allow_html=True)
                for _, row in suggestions.iterrows():
                    st.markdown(render_card(row), unsafe_allow_html=True)
        else:
            cards, total_cards = build_result_cards(results)
            if is_batch:
                cards = batch_sort_cards(cards)
            hidden_count = max(total_cards - len(cards), 0)
            missing_text = f" · 未找到：{html.escape(' / '.join(missing_terms))}" if missing_terms else ""
            extra = f" · 还有 {hidden_count} 个结果，请继续输入缩小范围" if hidden_count else ""
            st.markdown(f"<div class='notice'>显示最相关的 <b>{len(cards)}</b> 个药品{extra}{missing_text}</div>", unsafe_allow_html=True)
            for _, row in cards.iterrows():
                st.markdown(render_card(row), unsafe_allow_html=True)

    st.markdown(
        f"""
    <div class="stats">
        共收录 {len(df)} 条记录 · {df.apply(location_label, axis=1).nunique()} 个物理位置 · 数据 {data_size // 1024} KB
    </div>
    """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
