"""渲染模块。

职责：
- 剂型线条图标
- 货号高亮
- 柜位定位点阵（带严格的溢出防御）
- 单条结果卡片 HTML（三档密度）
"""
import html


# ──────────────────────────────────────────────────────────────
#  剂型图标：单色描边，1.4 stroke，继承父色
# ──────────────────────────────────────────────────────────────
ICONS = {
    "pill": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="11" width="20" height="10" rx="5"/><line x1="16" y1="11" x2="16" y2="21"/></svg>',
    "granule": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 9 L23 9 L24 26 L8 26 Z"/><path d="M9 9 L11 6 L21 6 L23 9"/><line x1="12" y1="14" x2="20" y2="14"/></svg>',
    "oral_liquid": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13 5 L19 5 L19 10 L21 13 L21 25 Q21 27 19 27 L13 27 Q11 27 11 25 L11 13 L13 10 Z"/><line x1="11" y1="16" x2="21" y2="16"/></svg>',
    "ointment": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M7 14 L23 11 L26 14 L23 17 L7 14 Z"/><rect x="3" y="11" width="4" height="6" rx="0.5"/><path d="M26 14 L29 12 L29 16 Z"/></svg>',
    "liquid": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13 5 L19 5 L19 8 L22 11 L22 25 Q22 27 20 27 L12 27 Q10 27 10 25 L10 11 L13 8 Z"/><path d="M13 18 Q16 14 19 18 Q19 22 16 22 Q13 22 13 18"/></svg>',
    "eye": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="16" cy="18" rx="11" ry="6"/><circle cx="16" cy="18" r="3"/><path d="M22 6 L26 10 L22 14 Z"/></svg>',
    "spray": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="11" y="13" width="10" height="14" rx="1"/><path d="M14 13 L14 9 L18 9 L18 13"/><line x1="18" y1="11" x2="24" y2="11"/><line x1="24" y1="11" x2="24" y2="14"/><circle cx="27" cy="10" r="0.6" fill="currentColor"/><circle cx="28" cy="13" r="0.6" fill="currentColor"/><circle cx="26" cy="14" r="0.6" fill="currentColor"/></svg>',
    "patch": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="9" width="18" height="14" rx="2"/><line x1="14" y1="9" x2="14" y2="23"/><line x1="18" y1="9" x2="18" y2="23"/><circle cx="16" cy="16" r="1.5"/></svg>',
    "device": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="20" height="20" rx="2"/><line x1="16" y1="11" x2="16" y2="21"/><line x1="11" y1="16" x2="21" y2="16"/></svg>',
    "box": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="9" width="18" height="17" rx="1"/><path d="M10 9 L12 5 L20 5 L22 9"/><line x1="7" y1="15" x2="25" y2="15"/></svg>',
}

# 柜位点阵的硬上限：超过则不画点阵，只显示文字坐标
# （手机上 14 列以上的点阵本来就看不清，且容易溢出）
MAX_GRID_COLS = 14
MAX_GRID_ROWS = 16


def icon_svg(category):
    return ICONS.get(category, ICONS["box"])


def format_code(code):
    text = "" if code is None else str(code).strip()
    if not text:
        return ""
    safe = html.escape(text)
    if len(text) <= 4:
        return f"<span class='row-code-tail'>{safe}</span>"
    return f"{html.escape(text[:-4])}<span class='row-code-tail'>{html.escape(text[-4:])}</span>"


def cabinet_grid_html(cabinet_info, df):
    """柜位定位点阵。

    cabinet_info: dict with 分区/位置/段号/__cabinet
    df: 全表，用于查同柜其他位置以画出柜子轮廓

    防御：
    - 超过 MAX_GRID_COLS/ROWS 时不画点阵，只给文字坐标
    - grid 用固定 cell 尺寸，外层 overflow-x 兜底
    """
    zone = str(cabinet_info.get("分区", "")).strip()
    pos = str(cabinet_info.get("位置", "")).strip()
    seg = str(cabinet_info.get("段号", "")).strip()
    cabinet = str(cabinet_info.get("__cabinet", "")).strip()

    if not zone or not pos or not seg or "-" not in pos or not seg.isdigit():
        return ""
    shelf_row = pos.rsplit("-", 1)[-1]
    if not shelf_row.isdigit():
        return ""

    subset = df[
        (df["__zone_norm"] == zone)
        & (df["__cabinet"] == cabinet)
    ]
    if subset.empty:
        return ""
    subset = subset.copy()
    subset["__gr"] = subset["位置"].astype(str).str.rsplit("-", n=1).str[-1]
    subset = subset[
        subset["__gr"].str.isdigit()
        & subset["段号"].astype(str).str.strip().str.isdigit()
    ]
    if subset.empty:
        return ""

    rows = sorted({int(v) for v in subset["__gr"]})
    max_col = max(int(v) for v in subset["段号"])
    hit_row, hit_col = int(shelf_row), int(seg)

    cap = (
        "<div class='locator-cap'><span>柜位坐标</span>"
        f"<b>{html.escape(zone)} · {html.escape(cabinet)} · 行{hit_row} 列{hit_col}</b></div>"
    )

    # 超出上限：降级为纯文字，不画点阵（避免溢出）
    if len(rows) > MAX_GRID_ROWS or max_col > MAX_GRID_COLS:
        return f"<div class='locator locator-text'>{cap}</div>"

    # 预计算每行实际有多少列（画出柜子真实轮廓）
    row_max_col = {}
    for gr in rows:
        cols = subset[subset["__gr"].astype(int) == gr]["段号"].astype(int)
        row_max_col[gr] = int(cols.max()) if not cols.empty else max_col

    cells = []
    for gr in rows:
        rmax = row_max_col.get(gr, max_col)
        for col in range(1, max_col + 1):
            active = col <= rmax
            cls = "cell"
            if active and gr == hit_row and col == hit_col:
                cls += " hit"
            elif not active:
                cls += " empty"
            cells.append(f"<span class='{cls}'></span>")

    return (
        "<div class='locator'>"
        f"{cap}"
        "<div class='cabinet-scroll'>"
        f"<div class='cabinet-grid' style='grid-template-columns: repeat({max_col}, var(--cell-size));'>"
        f"{''.join(cells)}</div>"
        "</div></div>"
    )


def _fallback_subtitle(query):
    """根据 query 类型返回 fallback 文案。"""
    from search import normalize_text, normalize_code
    code = normalize_code(query)
    norm = normalize_text(query)
    if code and len(code) >= 4 and code == norm:
        return "下面是按相似货号给出的推测"
    # 含字母（拼音/英文）
    if any(c.isalpha() and ord(c) < 128 for c in norm):
        return "下面是按相似名称给出的推测"
    return "下面是按相似药名、主治给出的推测"


def render_row(idx, card, df, density="default"):
    """渲染单条结果卡片。

    density: compact / default / spacious
    """
    name = html.escape(str(card["商品名"]))
    code_html = format_code(card["货号"])
    locations = card["位置列表"]
    category = card["分类"]
    therapy_label = card.get("主治标签", "") if hasattr(card, "get") else card["主治标签"]
    therapy_brief = card.get("主治简介", "") if hasattr(card, "get") else card["主治简介"]
    cabinet_info = card["柜位信息"]

    if len(locations) > 1:
        loc_main = html.escape(locations[0])
        loc_extra = f"<span class='row-loc-extra'>+{len(locations)-1}处</span>"
    else:
        loc_main = html.escape(locations[0]) if locations else ""
        loc_extra = ""

    # tags
    from search import CATEGORY_LABELS
    tags = [CATEGORY_LABELS.get(category, "药品")]
    if therapy_label and therapy_label not in tags:
        tags.append(therapy_label)
    if card["查询项"]:
        tags.append(f"查：{card['查询项']}")
    if card["推荐原因"]:
        tags.append(card["推荐原因"])

    if density == "compact":
        if card["推荐原因"]:
            visible_tags = [card["推荐原因"]]
        elif therapy_label:
            visible_tags = [therapy_label]
        else:
            visible_tags = [tags[0]]
    else:
        visible_tags = tags

    tag_html_parts = [f"<span>{html.escape(t)}</span>" for t in visible_tags]
    if card["模糊匹配"] and not card["推荐原因"] and density != "compact":
        tag_html_parts.append("<span class='row-tag-warn'>模糊匹配</span>")
    if card["多位置"]:
        tag_html_parts.append("<span class='row-tag-danger'>多位置·需核对</span>")
    divider = "<span class='row-tag-divider'>/</span>"
    tag_inner = divider.join(tag_html_parts)

    # 主治行（compact 隐藏）
    if density != "compact":
        therapy_html = (
            "<div class='row-therapy'>"
            f"<span><b>{html.escape(therapy_label)}</b>{html.escape(therapy_brief)}</span>"
            "</div>"
        )
    else:
        therapy_html = ""

    # 柜位图：compact 不显示；单位置才显示
    show_locator = density != "compact" and len(locations) == 1
    locator = cabinet_grid_html(cabinet_info, df) if show_locator else ""

    icon_html = f"<div class='row-icon'>{icon_svg(category)}</div>"

    if density == "compact":
        return f"""
<div class="row compact">
    <div class="row-index"><span class="row-index-num">№{idx:02d}</span></div>
    <div class="row-body">
        <div class="row-loc">{loc_main}{loc_extra}</div>
        <div class="row-name">{name}</div>
        <div class="row-code">货号 {code_html}</div>
    </div>
    <div class="row-tags">{tag_inner}</div>
    {icon_html}
</div>
"""

    klass = "row" if density == "default" else f"row {density}"
    return f"""
<div class="{klass}">
    <div class="row-index"><span class="row-index-num">№{idx:02d}</span></div>
    <div class="row-body">
        <div class="row-loc">{loc_main}{loc_extra}</div>
        <div class="row-name">{name}</div>
        <div class="row-code">货号 {code_html}</div>
        <div class="row-tags">{tag_inner}</div>
        {therapy_html}
        {locator}
    </div>
    {icon_html}
</div>
"""
