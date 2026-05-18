import html
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import streamlit as st


st.set_page_config(page_title="药品检索", page_icon="💊", layout="centered")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap');

* { font-family: 'Noto Sans SC', sans-serif; }

.block-container { padding-top: 1.5rem; max-width: 560px; }

.stTextInput > div > div > input {
    font-size: 18px !important;
    padding: 14px 16px !important;
    border-radius: 10px !important;
    border: 2px solid #d7dde5 !important;
}
.stTextInput > div > div > input:focus {
    border-color: #1a73e8 !important;
    box-shadow: 0 0 0 3px rgba(26,115,232,0.14) !important;
}

div[data-testid="stMetric"] { display: none; }

.header {
    text-align: center;
    padding: 0.5rem 0 1rem;
}
.header h1 {
    font-size: 24px;
    font-weight: 700;
    margin: 0;
    color: #1a1a1a;
}
.header p {
    font-size: 14px;
    color: #777;
    margin: 4px 0 0;
}

.result-card {
    background: #ffffff;
    border: 1px solid #dfe5ec;
    border-radius: 8px;
    padding: 14px 16px;
    margin: 10px 0;
    box-shadow: 0 1px 3px rgba(20, 31, 43, 0.06);
}
.result-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
}
.result-name {
    flex: 1;
    min-width: 0;
    font-size: 17px;
    font-weight: 700;
    color: #17212b;
    line-height: 1.4;
    word-break: break-word;
}
.result-score {
    color: #8a97a6;
    font-size: 12px;
    line-height: 1.5;
    white-space: nowrap;
}
.result-code {
    color: #576575;
    font-size: 13px;
    margin-top: 5px;
    font-family: Consolas, monospace;
}
.result-location {
    margin-top: 10px;
    color: #0b57d0;
    font-size: 20px;
    font-weight: 800;
    line-height: 1.35;
    word-break: break-word;
}
.result-location-label {
    display: inline-block;
    color: #607080;
    font-size: 12px;
    font-weight: 500;
    margin-right: 6px;
}
.result-keywords {
    color: #8793a1;
    font-size: 12px;
    margin-top: 6px;
}

.no-result {
    text-align: center;
    padding: 3rem 1rem;
    color: #888;
    font-size: 15px;
}

.stats {
    text-align: center;
    font-size: 13px;
    color: #9aa4af;
    padding: 1rem 0;
    border-top: 1px solid #edf0f3;
    margin-top: 1rem;
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


@st.cache_data
def load_data():
    csv_path = Path(__file__).parent / "data.csv"
    if not csv_path.exists():
        return None

    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str).fillna("")
    for col in ["位置", "段号", "商品名", "货号"]:
        if col not in df.columns:
            df[col] = ""

    df["__name_norm"] = df["商品名"].map(normalize_text)
    df["__code_norm"] = df["货号"].map(normalize_code)
    if "适应症关键词" in df.columns:
        df["__keyword_norm"] = df["适应症关键词"].map(normalize_text)
    else:
        df["__keyword_norm"] = ""
    df["__search_text"] = df["__name_norm"] + df["__keyword_norm"]
    return df


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


def search(df, query):
    if not query.strip():
        return pd.DataFrame()

    scored = df.copy()
    scored["匹配分"] = scored.apply(lambda row: score_row(row, query), axis=1)
    query_norm = normalize_text(query)
    threshold = 82 if len(query_norm) <= 1 else 58
    scored = scored[scored["匹配分"] >= threshold]
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

        cards.append(
            {
                "商品名": name,
                "货号": code,
                "物理位置": ", ".join(locations),
                "位置数": len(locations),
                "匹配分": group["匹配分"].max(),
                "适应症关键词": keywords,
            }
        )

    return pd.DataFrame(cards).sort_values(by=["匹配分", "位置数"], ascending=[False, False]).head(50)


def main():
    df = load_data()
    if df is None:
        st.error("未找到 data.csv，请将 CSV 文件放在 app.py 同目录下")
        return

    st.markdown(
        """
    <div class="header">
        <h1>💊 药品检索</h1>
        <p>输入药名、品牌、货号或症状，快速定位物理货架</p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    query = st.text_input(
        "搜索",
        placeholder="例如：洁尔阴、金戈、02004340、咳嗽...",
        label_visibility="collapsed",
    )

    if query:
        results = search(df, query)
        if results.empty:
            st.markdown(f'<div class="no-result">未找到「{html.escape(query)}」相关药品</div>', unsafe_allow_html=True)
        else:
            cards = build_result_cards(results)
            total_locations = sum(card.count(",") + 1 for card in cards["物理位置"] if card)
            st.markdown(
                f"<p style='font-size:14px;color:#596575;margin:0 0 4px;'>找到 <b>{len(cards)}</b> 个药品 · <b>{total_locations}</b> 个位置</p>",
                unsafe_allow_html=True,
            )

            for _, row in cards.iterrows():
                name = html.escape(str(row["商品名"]))
                code = html.escape(str(row["货号"]))
                locations = html.escape(str(row["物理位置"]))
                score = html.escape(str(row["匹配分"]))
                keywords = html.escape(str(row.get("适应症关键词", "")))
                keyword_html = f"<div class='result-keywords'>{keywords}</div>" if keywords else ""

                st.markdown(
                    f"""
                <div class="result-card">
                    <div class="result-top">
                        <div class="result-name">{name}</div>
                        <div class="result-score">匹配 {score}</div>
                    </div>
                    <div class="result-code">货号 {code}</div>
                    <div class="result-location"><span class="result-location-label">位置</span>{locations}</div>
                    {keyword_html}
                </div>
                """,
                    unsafe_allow_html=True,
                )

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
