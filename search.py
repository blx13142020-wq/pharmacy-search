"""搜索核心模块。

职责：
- 文本/货号归一化
- 候选粗筛（向量化）
- 单行打分（仅对候选）
- 主入口 search() / search_batch() / fallback_suggestions()
- 数据加载和预计算（包括 therapy 字段）
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from rules import (
    CATEGORY_RULES, CATEGORY_LABELS,
    THERAPY_RULES, ZONE_THERAPY_FALLBACK,
    ENGLISH_SYNONYMS,
    ZONE_HINT_RULES, ZONE_ORDER,
)


# ──────────────────────────────────────────────────────────────
#  常量
# ──────────────────────────────────────────────────────────────
REQUIRED_COLUMNS = ["位置", "段号", "商品名", "货号"]
MAX_RESULTS = 60

# 批量查询分隔符：斜杠 / 中英文逗号 / 中英文句号 / 中英文分号 / 顿号 / 竖线。
# 用其中任意一个分隔，都会拆成多个「独立查询」（每个出一张结果卡）。
# 注意：空格不在此列——空格用于「同一个药的多词细化」（AND 交集），
# 例如「阿莫西林 培彤」会精确命中含这两个词的那一个药，比拆成两次查询更有用。
_BATCH_SEP_RE = re.compile(r"[/,，.。；;、|]+")


def split_batch_terms(query):
    """按批量分隔符拆分查询，返回去空后的词列表。"""
    if not query:
        return []
    return [t.strip() for t in _BATCH_SEP_RE.split(str(query)) if t.strip()]


def has_batch_sep(query):
    """查询里是否含批量分隔符（用于判断是否走批量模式）。"""
    return bool(query) and bool(_BATCH_SEP_RE.search(str(query)))

# 打分阈值
SCORE_THRESHOLD_HIT = 70       # search() 命中
SCORE_THRESHOLD_FALLBACK = 56  # fallback 召回
SCORE_THRESHOLD_CODE = 78      # 货号相似召回


# ──────────────────────────────────────────────────────────────
#  归一化 & 排序键
# ──────────────────────────────────────────────────────────────
_NORM_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]")
_POS_RE = re.compile(r"([A-Z]+)(\d+)(.*)")


def normalize_text(value):
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    text = text.replace("（", "(").replace("）", ")")
    return _NORM_RE.sub("", text)


def normalize_code(value):
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\D", "", text)


def expand_query(query):
    """处理英文同义词和分词。

    返回 [(原 token, 归一化 token, 是否同义词展开)] 列表。
    这里只按 **空格 / 加号** 分词（同一个药的多词细化，做 AND 交集）；
    逗号、句号、斜杠等「批量分隔符」已在 search_batch 上游拆分，不在此处理。
    """
    if not query:
        return []
    raw_tokens = re.split(r"[\s+]+", str(query))
    out = []
    for tok in raw_tokens:
        tok = tok.strip()
        if not tok:
            continue
        norm = normalize_text(tok)
        expanded = False
        if norm in ENGLISH_SYNONYMS:
            norm = normalize_text(ENGLISH_SYNONYMS[norm])
            expanded = True
        out.append((tok, norm, expanded))
    return out


def zone_sort_value(zone):
    text = str(zone).strip()
    return (ZONE_ORDER.get(text, 99), text)


def natural_position_key(pos):
    text = "" if pos is None else str(pos).strip().upper()
    match = _POS_RE.fullmatch(text)
    if not match:
        return (text, -1, "")
    prefix, number, suffix = match.groups()
    return (prefix, int(number), suffix)


def segment_key(seg):
    text = "" if seg is None else str(seg).strip()
    return (int(text), "") if text.isdigit() else (999999, text)


def location_label(zone, pos, seg):
    zone = str(zone).strip()
    pos = str(pos).strip()
    seg = str(seg).strip()
    core = f"{pos}-{seg}" if seg else pos
    return f"{zone}·{core}" if zone else core


# ──────────────────────────────────────────────────────────────
#  分类 / 主治 / 分区提示
# ──────────────────────────────────────────────────────────────
def classify_product(name):
    text = normalize_text(name)
    for category, keywords in CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            return category
    return "box"


# 预编译 THERAPY_RULES：把每条规则的药品关键词归一化，并预拼接其症状串
# 结构：[(label, brief, [norm_kw...], 该规则的可搜索串)]
_COMPILED_THERAPY = []
for _label, _brief, _drug_kws, _sym_words in THERAPY_RULES:
    _norm_kws = [normalize_text(k) for k in _drug_kws]
    _search_str = normalize_text(_label) + "".join(normalize_text(s) for s in _sym_words)
    _COMPILED_THERAPY.append((_label, _brief, _norm_kws, _search_str))


def therapy_hint(name, zone="", category="box"):
    """返回 (短标签, 一句话主治)。保证每条都有返回。"""
    text = normalize_text(name)
    for label, brief, norm_kws, _ in _COMPILED_THERAPY:
        for kw in norm_kws:
            if kw in text:
                return (label, brief)
    zone = str(zone).strip()
    fallback = ZONE_THERAPY_FALLBACK.get((zone, category))
    if fallback:
        return (CATEGORY_LABELS.get(category, "药品").split("·")[-1], fallback)
    return ("药品", "按说明书或遵医嘱使用")


def therapy_full(name_norm, zone="", category="box"):
    """一次性返回 (label, brief, 可搜索串)，供 load_data 预计算用。
    避免 therapy_hint + _therapy_search_tokens 两次遍历规则。"""
    for label, brief, norm_kws, search_str in _COMPILED_THERAPY:
        for kw in norm_kws:
            if kw in name_norm:
                return (label, brief, search_str)
    zone = str(zone).strip()
    fallback = ZONE_THERAPY_FALLBACK.get((zone, category))
    if fallback:
        return (CATEGORY_LABELS.get(category, "药品").split("·")[-1], fallback, "")
    return ("药品", "按说明书或遵医嘱使用", "")


def guessed_zone_hints(query):
    text = normalize_text(query)
    if not text:
        return []
    hints = []
    for zone_text, keywords in ZONE_HINT_RULES:
        if any(normalize_text(keyword) in text for keyword in keywords):
            hints.append(zone_text)
    return hints[:3]


# ──────────────────────────────────────────────────────────────
#  数据加载
# ──────────────────────────────────────────────────────────────
def data_fingerprint(data_path: Path):
    if not data_path.exists():
        return 0, 0
    stat = data_path.stat()
    return stat.st_mtime_ns, stat.st_size


def _read_csv(path: Path):
    """带乱码探测的 CSV 读取。"""
    last_err = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            df = pd.read_csv(path, encoding=encoding, dtype=str).fillna("")
            sample = "".join(df.get("商品名", pd.Series(dtype=str)).head(20).tolist())
            if "\ufffd" in sample:
                continue
            return df, None
        except Exception as exc:
            last_err = exc
            continue
    return None, last_err


def load_data(data_path: Path):
    """加载 CSV 并预计算所有衍生字段。返回 (df, issues)."""
    if not data_path.exists():
        return None, ["未找到 data.csv，请放置在 app.py 同目录"]

    df, err = _read_csv(data_path)
    if df is None:
        return None, [f"读取 data.csv 失败：{err}"]

    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if "分区" not in df.columns:
        df["分区"] = ""

    # 基础归一化
    df["__name_norm"] = df["商品名"].map(normalize_text)
    df["__code_norm"] = df["货号"].map(normalize_code)
    if "适应症关键词" in df.columns:
        df["__keyword_norm"] = df["适应症关键词"].map(normalize_text)
    else:
        df["__keyword_norm"] = ""
    df["__zone_norm"] = df["分区"].astype(str).str.strip()
    df["__loc_label"] = [
        location_label(z, p, s)
        for z, p, s in zip(df["分区"], df["位置"], df["段号"])
    ]
    df["__category"] = df["商品名"].map(classify_product)

    # 主治 + 症状搜索索引（一次性遍历规则）
    labels = []
    briefs = []
    therapy_search = []
    for name, zone, cat, name_norm in zip(
        df["商品名"], df["__zone_norm"], df["__category"], df["__name_norm"]
    ):
        lbl, br, search_str = therapy_full(name_norm, zone, cat)
        labels.append(lbl)
        briefs.append(br)
        therapy_search.append(search_str)
    df["__therapy_label"] = labels
    df["__therapy_brief"] = briefs
    df["__therapy_search"] = therapy_search

    # 总搜索文本
    df["__search_text"] = (
        df["__name_norm"]
        + df["__keyword_norm"]
        + df["__therapy_search"]
    )

    # 柜位索引：cabinet = 位置去掉 -行号（列表推导比 apply 快）
    pos_str = df["位置"].astype(str).str.strip()
    df["__cabinet"] = [
        p.rsplit("-", 1)[0] if "-" in p else p for p in pos_str
    ]

    issues = validate_data(df)
    return df, issues


def validate_data(df):
    issues = []
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        issues.append(f"缺少字段：{', '.join(missing)}")
        return issues
    severe = False  # 是否包含严重问题（缺字段、多位置）
    for label in ("商品名", "货号", "位置"):
        empty = df[label].astype(str).str.strip().eq("").sum()
        if empty:
            issues.append(f"{empty} 条缺{label}")
            if empty > 3:
                severe = True
    multi = df.groupby("货号")["__loc_label"].nunique()
    multi_count = int((multi > 1).sum())
    if multi_count:
        issues.append(f"{multi_count} 个货号存在多个物理位置，建议核对")
        severe = True
    return issues if severe or len(issues) > 1 else []


# ──────────────────────────────────────────────────────────────
#  打分
# ──────────────────────────────────────────────────────────────
def code_match_score(query_code, code):
    """货号匹配。"""
    if not query_code or not code:
        return 0
    if query_code == code:
        return 150
    if len(query_code) == 4 and code.endswith(query_code):
        return 142
    if 4 < len(query_code) < len(code):
        if code.endswith(query_code):
            return 132
        tail = code[-len(query_code):]
        diffs = sum(1 for a, b in zip(query_code, tail) if a != b)
        if diffs == 1:
            return 96
        return 0
    if len(query_code) == len(code):
        a = query_code.lstrip("0")
        b = code.lstrip("0")
        if a and a == b:
            return 130
    return 0


def code_fuzzy_score(query_code, code):
    """fallback 用，比 code_match_score 宽松一档。"""
    if not query_code or not code:
        return 0
    direct = code_match_score(query_code, code)
    if direct:
        return direct
    if len(query_code) > len(code):
        return 0
    tail = code[-len(query_code):]
    diffs = sum(1 for a, b in zip(query_code, tail) if a != b)
    # 优先按差异位数判断
    if diffs == 1:
        return 104
    if diffs == 2 and len(query_code) >= 4:
        return 82
    # 数值距离
    try:
        delta = abs(int(query_code) - int(tail))
        if delta <= 10:
            return 92
        if delta <= 100:
            return 70
    except ValueError:
        pass
    return 0


def score_candidate(name_norm, code_norm, search_text, query_norm, query_code):
    """候选行打分。"""
    scores = []

    if query_code and code_norm:
        cs = code_match_score(query_code, code_norm)
        if cs:
            scores.append(cs)

    if query_norm:
        if query_norm in name_norm:
            pos = name_norm.find(query_norm)
            bonus = max(0, 10 - pos)
            scores.append(115 + bonus)
        elif query_norm in search_text:
            scores.append(96)
        if len(query_norm) >= 2:
            ratio = SequenceMatcher(None, query_norm, name_norm).ratio()
            if ratio >= 0.5:
                scores.append(55 + ratio * 45)

    return max(scores) if scores else 0


def _prefilter(df, query_norm, query_code):
    """向量化粗筛，返回候选 mask。"""
    mask = pd.Series(False, index=df.index)
    if query_norm:
        mask |= df["__search_text"].str.contains(query_norm, regex=False, na=False)
        if len(query_norm) >= 2:
            head = query_norm[0]
            tail = query_norm[-1]
            mask |= (
                df["__name_norm"].str.contains(head, regex=False, na=False)
                & df["__name_norm"].str.contains(tail, regex=False, na=False)
            )
    if query_code and len(query_code) >= 4:
        mask |= df["__code_norm"].str.endswith(query_code, na=False)
        if len(query_code) >= 6:
            mask |= df["__code_norm"].str.contains(query_code, regex=False, na=False)
    return mask


def _rank(df):
    out = df.copy()
    out["__zone_key"] = out["分区"].map(zone_sort_value)
    out["__pos_key"] = out["位置"].map(natural_position_key)
    out["__seg_key"] = out["段号"].map(segment_key)
    out = out.sort_values(
        by=["匹配分", "__zone_key", "__pos_key", "__seg_key"],
        ascending=[False, True, True, True],
    )
    return out.drop(columns=["__zone_key", "__pos_key", "__seg_key"])


def search(df, query):
    """主搜索函数（不带缓存，由调用方负责缓存）。

    支持英文同义词、空格/标点分词。多 token 时取**交集**：
    每个 token 独立搜，结果取共同命中的药。
    """
    tokens = expand_query(query)
    if not tokens:
        return pd.DataFrame()

    # 多 token：每个 token 跑一次单 token 搜索，取索引交集
    if len(tokens) > 1:
        joined = "".join(t[1] for t in tokens)
        primary_query = joined if joined else query
        primary_code = normalize_code(query)
        merged_result = _search_single(df, primary_query, primary_code)
        if not merged_result.empty:
            return merged_result
        sets = []
        results_by_token = []
        for raw, norm, expanded in tokens:
            r = _search_single(df, norm, normalize_code(raw), allow_short=expanded)
            if r.empty:
                return pd.DataFrame()
            sets.append(set(r.index.tolist()))
            results_by_token.append(r)
        common = set.intersection(*sets) if sets else set()
        if not common:
            return pd.DataFrame()
        base = results_by_token[0]
        # 用 index 去重（交集本身是 index 集合，base 可能有重复 index 吗？不会）
        return base[base.index.isin(common)]

    raw, norm, expanded = tokens[0]
    return _search_single(df, norm, normalize_code(raw), allow_short=expanded)


def _search_single(df, query_norm, query_code, allow_short=False):
    """单 token 搜索（query 已归一化）。

    allow_short: 若为 True（来自同义词展开，如 calcium→钙），
                 允许单字符查询不被拒绝。
    """
    if not query_norm and not query_code:
        return pd.DataFrame()
    is_pure_code = query_code and query_code == query_norm
    if is_pure_code and len(query_code) < 4:
        return pd.DataFrame()
    if not allow_short and len(query_norm) <= 1 and len(query_code) < 4:
        return pd.DataFrame()

    # 1) 4 位货号
    if is_pure_code and len(query_code) == 4:
        hits = df[df["__code_norm"].str.endswith(query_code, na=False)].copy()
        if hits.empty:
            return hits
        hits["匹配分"] = 142.0
        return _rank(hits)

    # 2) 完整货号严格匹配
    if len(query_code) >= 6:
        hits = df[df["__code_norm"].eq(query_code)].copy()
        if not hits.empty:
            hits["匹配分"] = 150.0
            return _rank(hits)
        # 2b) 严格不命中时，对长货号也允许"后 4 位"宽容（处理 00004340 → 02004340）
        if is_pure_code and len(query_code) >= 6:
            tail4 = query_code[-4:]
            hits = df[df["__code_norm"].str.endswith(tail4, na=False)].copy()
            if not hits.empty:
                hits["匹配分"] = 138.0
                return _rank(hits)

    # 3) 粗筛 → 打分
    mask = _prefilter(df, query_norm, query_code)
    cands = df[mask]
    if cands.empty:
        return cands

    name_norms = cands["__name_norm"].tolist()
    code_norms = cands["__code_norm"].tolist()
    search_texts = cands["__search_text"].tolist()
    scores = [
        score_candidate(n, c, s, query_norm, query_code)
        for n, c, s in zip(name_norms, code_norms, search_texts)
    ]
    cands = cands.copy()
    cands["匹配分"] = scores
    hits = cands[cands["匹配分"] >= SCORE_THRESHOLD_HIT]
    if hits.empty:
        return hits
    return _rank(hits)


def search_batch(df, query):
    """批量搜索：用 / 逗号 句号 分号 顿号 竖线 等分隔，每个词独立搜索。"""
    terms = split_batch_terms(query)
    if not terms:
        return pd.DataFrame(), []
    pieces = []
    missing = []
    seen_terms = set()
    for term in terms:
        # 去掉重复词（如「4915,4915」只搜一次），避免重复卡片
        key = term.strip().lower()
        if key in seen_terms:
            continue
        seen_terms.add(key)
        r = search(df, term)
        if r.empty:
            missing.append(term)
            continue
        r = r.copy()
        r["查询项"] = term
        pieces.append(r)
    if not pieces:
        return pd.DataFrame(), missing
    return pd.concat(pieces, ignore_index=True), missing


# ──────────────────────────────────────────────────────────────
#  Fallback 推荐（粗筛用 2-gram，更宽容）
# ──────────────────────────────────────────────────────────────
def _bigrams(text):
    """生成 2-gram 集合。"""
    if len(text) < 2:
        return set()
    return {text[i:i+2] for i in range(len(text) - 1)}


def fallback_suggestions(df, query, limit=6):
    """当 search 0 命中时给推测。"""
    query_norm = normalize_text(query)
    query_code = normalize_code(query)
    if not query_norm and not query_code:
        return pd.DataFrame(), []

    # 粗筛：用 2-gram 而不是首尾字符，捕获更多错别字情况
    # 例："感胃灵" 的 2-gram {感胃, 胃灵}，"感冒灵" 的 2-gram {感冒, 冒灵}
    #  二者交集为空，但加上"首+尾"匹配（感, 灵）可以救回
    mask = pd.Series(False, index=df.index)
    if query_norm and len(query_norm) >= 2:
        query_grams = _bigrams(query_norm)
        if query_grams:
            # 任一 2-gram 命中
            pattern = "|".join(re.escape(g) for g in query_grams)
            mask |= df["__name_norm"].str.contains(pattern, regex=True, na=False)
        # 兜底：首尾字符同时命中（处理 1 字之差的同音字）
        head = query_norm[0]
        tail = query_norm[-1]
        mask |= (
            df["__name_norm"].str.contains(head, regex=False, na=False)
            & df["__name_norm"].str.contains(tail, regex=False, na=False)
        )

    # 货号 fallback 仅在 query 以数字为主时触发（避免 "xyz123" 的 123 误触发）
    code_dominant = (
        query_code
        and len(query_code) >= 4
        and len(query_code) >= len(query_norm) - 1
    )
    if code_dominant:
        prefix3 = query_code[:3]
        suffix3 = query_code[-3:]
        mask |= df["__code_norm"].str.endswith(suffix3, na=False)
        mask |= df["__code_norm"].str[:3].eq(prefix3)

    cands = df[mask]
    if cands.empty:
        return pd.DataFrame(), guessed_zone_hints(query)

    rows = []
    for rec in cands.to_dict("records"):
        n_norm = rec["__name_norm"]
        c_norm = rec["__code_norm"]
        n_score = 0
        if query_norm:
            if query_norm in n_norm:
                n_score = 110
            else:
                ratio = SequenceMatcher(None, query_norm, n_norm).ratio()
                if ratio >= 0.40:  # 比之前 0.42 略松，给错别字更多机会
                    n_score = 55 + ratio * 45
        c_score = code_fuzzy_score(query_code, c_norm) if code_dominant else 0
        score = max(n_score, c_score)
        if score >= SCORE_THRESHOLD_FALLBACK or c_score >= SCORE_THRESHOLD_CODE:
            reasons = []
            if c_score >= 100:
                reasons.append("相似货号")
            elif c_score:
                reasons.append("货号接近")
            if n_score >= 90:
                reasons.append("药名包含")
            elif n_score:
                reasons.append("相似药名")
            rec_copy = dict(rec)
            rec_copy["匹配分"] = score
            rec_copy["推荐原因"] = " / ".join(reasons) or "可能相关"
            rows.append(rec_copy)

    if not rows:
        return pd.DataFrame(), guessed_zone_hints(query)

    return (
        pd.DataFrame(rows).sort_values("匹配分", ascending=False).head(limit * 4),
        guessed_zone_hints(query),
    )


# ──────────────────────────────────────────────────────────────
#  结果整理 → 卡片
# ──────────────────────────────────────────────────────────────
def to_cards(results, max_cards=MAX_RESULTS):
    """把搜索结果（每行一个物理位置）合并成卡片（每个商品一张）。"""
    if results.empty:
        return pd.DataFrame()

    grouped = results.groupby(["商品名", "货号"], dropna=False, sort=False)
    cards = []
    for (name, code), group in grouped:
        locations = list(dict.fromkeys(group["__loc_label"].tolist()))
        zones = list(dict.fromkeys(group["分区"].astype(str).tolist()))
        max_score = float(group["匹配分"].max())
        category = (
            group["__category"].iloc[0]
            if "__category" in group.columns
            else classify_product(name)
        )
        if "__therapy_label" in group.columns:
            t_label = group["__therapy_label"].iloc[0]
            t_brief = group["__therapy_brief"].iloc[0]
        else:
            t_label, t_brief = therapy_hint(
                name, zones[0] if zones else "", category
            )

        # 只保留 cabinet_grid_html 需要的字段，不再带整行 Series
        first = group.iloc[0]
        cabinet_info = {
            "分区": str(first.get("分区", "")),
            "位置": str(first.get("位置", "")),
            "段号": str(first.get("段号", "")),
            "__cabinet": str(first.get("__cabinet", "")),
        }
        cards.append({
            "商品名": name,
            "货号": code,
            "位置列表": locations,
            "首位置": locations[0] if locations else "",
            "分区列表": zones,
            "主分区": zones[0] if zones else "",
            "柜位信息": cabinet_info,
            "匹配分": max_score,
            "模糊匹配": max_score < 110,
            "多位置": len(locations) > 1,
            "分类": category,
            "主治标签": t_label,
            "主治简介": t_brief,
            "查询项": group["查询项"].iloc[0] if "查询项" in group.columns else "",
            "推荐原因": group["推荐原因"].iloc[0] if "推荐原因" in group.columns else "",
        })

    cards_df = pd.DataFrame(cards)
    cards_df = cards_df.sort_values(
        by=["匹配分"], ascending=[False]
    ).head(max_cards).reset_index(drop=True)
    return cards_df


def batch_sort_cards(cards_df):
    """批量查询时按分区聚集。"""
    if cards_df.empty:
        return cards_df
    zone_counts = cards_df["主分区"].value_counts().to_dict()
    cards_df = cards_df.copy()
    cards_df["__zc"] = cards_df["主分区"].map(zone_counts).fillna(0)
    cards_df["__zk"] = cards_df["主分区"].map(zone_sort_value)
    cards_df = cards_df.sort_values(
        by=["__zc", "__zk", "匹配分"], ascending=[False, True, False]
    )
    return cards_df.drop(columns=["__zc", "__zk"])
