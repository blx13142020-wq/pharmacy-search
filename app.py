import html
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import streamlit as st


REQUIRED_COLUMNS = ["位置", "段号", "商品名", "货号"]
MAX_RESULTS = 15
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
    --ink: #10212b;
    --muted: #637381;
    --line: #d9e4e7;
    --soft: #f4faf9;
    --brand: #0f766e;
    --brand-blue: #1261a6;
    --warn: #b45309;
    --danger: #b42318;
}

* {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "Microsoft YaHei", sans-serif;
}

.stApp {
    background: linear-gradient(180deg, #f7fbfb 0%, #ffffff 34%);
}

.block-container {
    padding-top: 1.35rem;
    max-width: 620px;
}

.stTextInput > div > div > input {
    font-size: 18px !important;
    padding: 15px 16px !important;
    border-radius: 8px !important;
    border: 2px solid #cbdde0 !important;
    background: #ffffff !important;
}
.stTextInput > div > div > input:focus {
    border-color: var(--brand) !important;
    box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.14) !important;
}

.header {
    padding: 0.4rem 0 1rem;
}
.header h1 {
    color: var(--ink);
    font-size: 26px;
    font-weight: 800;
    line-height: 1.15;
    margin: 0;
}
.header p {
    color: var(--muted);
    font-size: 14px;
    margin: 7px 0 0;
}
.header-mark {
    display: inline-block;
    width: 11px;
    height: 22px;
    margin-right: 8px;
    transform: translateY(3px);
    border-radius: 3px;
    background: linear-gradient(180deg, #0f766e, #38bdf8);
}

.result-card {
    position: relative;
    display: grid;
    grid-template-columns: 1fr 68px;
    gap: 12px;
    background: #ffffff;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 14px 14px 13px;
    margin: 10px 0;
    box-shadow: 0 2px 7px rgba(15, 35, 45, 0.055);
}
.result-main {
    min-width: 0;
}
.location {
    color: var(--brand-blue);
    font-size: 28px;
    font-weight: 850;
    letter-spacing: 0;
    line-height: 1.1;
    word-break: break-word;
}
.drug-name {
    color: var(--ink);
    font-size: 16px;
    font-weight: 700;
    line-height: 1.35;
    margin-top: 8px;
    word-break: break-word;
}
.drug-code {
    color: #506272;
    font-family: Consolas, "SFMono-Regular", monospace;
    font-size: 13px;
    margin-top: 4px;
}
.code-tail {
    color: var(--ink);
    background: #edf5f2;
    border-bottom: 2px solid var(--brand);
    border-radius: 3px;
    padding: 0 3px;
    margin-left: 1px;
    font-weight: 750;
    letter-spacing: 0;
}
.badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 8px;
}
.badge {
    border-radius: 999px;
    font-size: 12px;
    font-weight: 650;
    line-height: 1;
    padding: 5px 8px;
}
.badge-soft {
    background: #e6f4f1;
    color: var(--brand);
}
.badge-warn {
    background: #fff4df;
    color: var(--warn);
}
.badge-danger {
    background: #fff0ee;
    color: var(--danger);
}
.icon-box {
    align-self: center;
    justify-self: end;
    width: 58px;
    height: 58px;
    border-radius: 8px;
    background: var(--soft);
    border: 1px solid #dcebea;
    display: grid;
    place-items: center;
}
.icon-box svg {
    width: 42px;
    height: 42px;
    stroke: var(--brand);
    fill: none;
    stroke-width: 2.1;
    stroke-linecap: round;
    stroke-linejoin: round;
}

.notice {
    color: #506272;
    background: #f2f7f8;
    border: 1px solid #dce8eb;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 13px;
    margin: 8px 0 12px;
}
.no-result {
    color: #667985;
    text-align: center;
    padding: 3rem 1rem;
    font-size: 15px;
}
.stats {
    color: #8b99a5;
    text-align: center;
    font-size: 13px;
    padding: 1rem 0;
    border-top: 1px solid #edf2f3;
    margin-top: 1rem;
}

@media (max-width: 520px) {
    .block-container { padding-left: 1rem; padding-right: 1rem; }
    .result-card { grid-template-columns: 1fr 54px; }
    .location { font-size: 25px; }
    .icon-box { width: 50px; height: 50px; }
    .icon-box svg { width: 36px; height: 36px; }
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


def natural_position_key(pos):
    text = "" if pos is None else str(pos).strip()
    match = re.fullmatch(r"([A-Za-z]+)(\d+)(.*)", text)
    if not match:
        return (text, -1, "")
    prefix, number, suffix = match.groups()
    return (prefix.upper(), int(number), suffix)


def segment_key(seg):
    text = "" if seg is None else str(seg).strip()
    return (int(text), "") if text.isdigit() else (999999, text)


def location_label(row):
    pos = str(row.get("位置", "")).strip()
    seg = str(row.get("段号", "")).strip()
    return f"{pos}-{seg}" if seg else pos


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
    if len(text) <= 3:
        return f"<span class='code-tail'>{safe}</span>" if text else ""
    return f"{html.escape(text[:-3])}<span class='code-tail'>{html.escape(text[-3:])}</span>"


def validate_data(df):
    issues = []
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        issues.append(f"缺少字段：{', '.join(missing)}")
        return issues

    empty_name = df["商品名"].astype(str).str.strip().eq("").sum()
    empty_code = df["货号"].astype(str).str.strip().eq("").sum()
    empty_location = df["位置"].astype(str).str.strip().eq("").sum()
    if empty_name:
        issues.append(f"{empty_name} 条缺商品名")
    if empty_code:
        issues.append(f"{empty_code} 条缺货号")
    if empty_location:
        issues.append(f"{empty_location} 条缺位置")

    code_locations = df.assign(__loc=df.apply(location_label, axis=1)).groupby("货号")["__loc"].nunique()
    multi_location = int((code_locations > 1).sum())
    if multi_location:
        issues.append(f"{multi_location} 个货号存在多个物理位置，建议人工校验")
    return issues


@st.cache_data
def load_data():
    csv_path = Path(__file__).parent / "data.csv"
    if not csv_path.exists():
        return None, ["未找到 data.csv，请将 CSV 文件放在 app.py 同目录下"]

    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str).fillna("")
    issues = validate_data(df)
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""

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
    keywords = row["__keyword_norm"]
    search_text = row["__search_text"]
    scores = []

    if query_code and code:
        if query_code == code:
            scores.append(150)
        elif query_code in code:
            scores.append(125)
        elif len(query_code) >= 4 and query_code == code.lstrip("0"):
            scores.append(118)

    if query_norm in name:
        scores.append(115)
    if query_norm in keywords:
        scores.append(96)
    if len(query_norm) >= 2:
        scores.append(ordered_match_score(query_norm, search_text))
        ratio = SequenceMatcher(None, query_norm, name).ratio()
        if ratio >= 0.5:
            scores.append(55 + ratio * 45)

    return round(max(scores) if scores else 0, 1)


def code_match_mask(df, query_code):
    if not query_code:
        return pd.Series(False, index=df.index)
    return (
        df["__code_norm"].eq(query_code)
        | df["__code_norm"].str.contains(query_code, regex=False, na=False)
        | df["__code_norm"].str.lstrip("0").eq(query_code)
    )


def search(df, query):
    if not query.strip():
        return pd.DataFrame()

    query_norm = normalize_text(query)
    query_code = normalize_code(query)
    if len(query_norm) <= 1 and len(query_code) < 4:
        return pd.DataFrame()

    scored = df.copy()
    scored["匹配分"] = scored.apply(lambda row: score_row(row, query), axis=1)

    if len(query_code) >= 6:
        code_hits = scored[code_match_mask(scored, query_code)]
        if not code_hits.empty:
            return code_hits.sort_values(
                by=["匹配分", "位置", "段号"],
                ascending=[False, True, True],
                key=lambda s: s.map(natural_position_key)
                if s.name == "位置"
                else (s.map(segment_key) if s.name == "段号" else s),
            )

    scored = scored[scored["匹配分"] >= 68]
    if scored.empty:
        return scored

    return scored.sort_values(
        by=["匹配分", "位置", "段号"],
        ascending=[False, True, True],
        key=lambda s: s.map(natural_position_key) if s.name == "位置" else (s.map(segment_key) if s.name == "段号" else s),
    )


def build_result_cards(results):
    cards = []
    grouped = results.groupby(["商品名", "货号"], dropna=False, sort=False)
    for (name, code), group in grouped:
        group = group.sort_values(
            by=["位置", "段号"],
            key=lambda s: s.map(natural_position_key) if s.name == "位置" else s.map(segment_key),
        )
        locations = []
        for _, row in group.iterrows():
            label = location_label(row)
            if label and label not in locations:
                locations.append(label)

        keywords = ""
        if "适应症关键词" in group.columns:
            keywords = "；".join([x for x in group["适应症关键词"].dropna().unique() if str(x).strip()])

        max_score = group["匹配分"].max()
        cards.append(
            {
                "商品名": name,
                "货号": code,
                "物理位置": ", ".join(locations),
                "位置数": len(locations),
                "匹配分": max_score,
                "模糊匹配": max_score < 100,
                "多位置警告": len(locations) > 1,
                "分类": classify_product(name),
                "适应症关键词": keywords,
            }
        )

    cards_df = pd.DataFrame(cards)
    if cards_df.empty:
        return cards_df, 0
    cards_df = cards_df.sort_values(by=["匹配分", "位置数"], ascending=[False, False])
    return cards_df.head(MAX_RESULTS), len(cards_df)


def render_card(row):
    name = html.escape(str(row["商品名"]))
    code_html = format_code(row["货号"])
    locations = html.escape(str(row["物理位置"]))
    icon = icon_svg(row["分类"])

    category_label = CATEGORY_LABELS.get(str(row["分类"]), "药品")
    badges = [f"<span class='badge badge-soft'>{html.escape(category_label)}</span>"]
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
    df, issues = load_data()
    if df is None:
        st.error(issues[0])
        return

    st.markdown(
        """
    <div class="header">
        <h1><span class="header-mark"></span>药品检索</h1>
        <p>药名 / 品牌 / 货号，快速定位货架</p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    if issues:
        with st.expander("数据校验提示", expanded=False):
            for issue in issues:
                st.write(f"- {issue}")

    query = st.text_input(
        "搜索",
        placeholder="药名 / 品牌 / 货号",
        label_visibility="collapsed",
    )

    if query:
        results = search(df, query)
        if results.empty:
            st.markdown(f'<div class="no-result">未找到「{html.escape(query)}」相关药品</div>', unsafe_allow_html=True)
        else:
            cards, total_cards = build_result_cards(results)
            hidden_count = max(total_cards - len(cards), 0)
            extra = f" · 还有 {hidden_count} 个结果，请继续输入缩小范围" if hidden_count else ""
            st.markdown(
                f"<div class='notice'>显示最相关的 <b>{len(cards)}</b> 个药品{extra}</div>",
                unsafe_allow_html=True,
            )
            for _, row in cards.iterrows():
                st.markdown(render_card(row), unsafe_allow_html=True)

    st.markdown(
        f"""
    <div class="stats">
        共收录 {len(df)} 条记录 · {df["位置"].nunique()} 个货架位置
    </div>
    """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
