import streamlit as st
import pandas as pd
from pathlib import Path

st.set_page_config(page_title="药品检索", page_icon="💊", layout="centered")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap');

* { font-family: 'Noto Sans SC', sans-serif; }

.block-container { padding-top: 1.5rem; max-width: 480px; }

.stTextInput > div > div > input {
    font-size: 18px !important;
    padding: 14px 16px !important;
    border-radius: 12px !important;
    border: 2px solid #e0e0e0 !important;
}
.stTextInput > div > div > input:focus {
    border-color: #1a73e8 !important;
    box-shadow: 0 0 0 3px rgba(26,115,232,0.15) !important;
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
    color: #888;
    margin: 4px 0 0;
}

.result-card {
    background: #f8fafc;
    border: 1px solid #e8ecf0;
    border-radius: 12px;
    padding: 14px 18px;
    margin: 10px 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
}
.result-left {
    flex: 1;
    min-width: 0;
}
.result-name {
    font-size: 16px;
    font-weight: 500;
    color: #1a1a1a;
    line-height: 1.4;
    word-break: break-all;
}
.result-code {
    font-size: 12px;
    color: #999;
    margin-top: 2px;
    font-family: monospace;
}
.result-pos {
    background: #1a73e8;
    color: white;
    font-size: 18px;
    font-weight: 700;
    padding: 8px 14px;
    border-radius: 10px;
    white-space: nowrap;
    min-width: 48px;
    text-align: center;
    line-height: 1.2;
}
.result-pos small {
    display: block;
    font-size: 11px;
    font-weight: 400;
    opacity: 0.8;
    margin-top: 2px;
}

.no-result {
    text-align: center;
    padding: 3rem 1rem;
    color: #999;
    font-size: 15px;
}

.stats {
    text-align: center;
    font-size: 13px;
    color: #bbb;
    padding: 1rem 0;
    border-top: 1px solid #f0f0f0;
    margin-top: 1rem;
}

.quick-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0.5rem 0 1.5rem;
    justify-content: center;
}
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load_data():
    csv_path = Path(__file__).parent / "data.csv"
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str).fillna("")
    return df


def search(df, query):
    query = query.strip()
    if not query:
        return pd.DataFrame()
    mask = df["商品名"].str.contains(query, case=False, na=False)
    if "货号" in df.columns:
        mask = mask | df["货号"].str.contains(query, case=False, na=False)
    if "适应症关键词" in df.columns:
        mask = mask | df["适应症关键词"].str.contains(query, case=False, na=False)
    return df[mask].drop_duplicates(subset=["商品名", "位置"])


def main():
    df = load_data()
    if df is None:
        st.error("未找到药品库.csv，请将CSV文件放在app.py同目录下")
        return

    st.markdown("""
    <div class="header">
        <h1>💊 药品检索</h1>
        <p>输入药名或货号，快速定位货架</p>
    </div>
    """, unsafe_allow_html=True)

    query = st.text_input(
        "搜索",
        placeholder="输入药名、货号或症状...",
        label_visibility="collapsed"
    )

    if query:
        results = search(df, query)
        if results.empty:
            st.markdown(f'<div class="no-result">未找到「{query}」相关药品</div>', unsafe_allow_html=True)
        else:
            st.markdown(f"<p style='font-size:14px;color:#666;margin:0 0 4px;'>找到 <b>{len(results)}</b> 个结果</p>", unsafe_allow_html=True)
            for _, row in results.iterrows():
                pos = row.get("位置", "?")
                seg = row.get("段号", "")
                name = row.get("商品名", "")
                code = row.get("货号", "")
                keywords = row.get("适应症关键词", "")

                seg_html = f"<small>第{seg}位</small>" if seg else ""
                code_html = f"<div class='result-code'>货号 {code}</div>" if code else ""
                kw_html = f"<div class='result-code'>{keywords}</div>" if keywords else ""

                st.markdown(f"""
                <div class="result-card">
                    <div class="result-left">
                        <div class="result-name">{name}</div>
                        {code_html}
                        {kw_html}
                    </div>
                    <div class="result-pos">
                        {pos}
                        {seg_html}
                    </div>
                </div>
                """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="stats">
        共收录 {len(df)} 种药品 · {df["位置"].nunique()} 个货架位置
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
