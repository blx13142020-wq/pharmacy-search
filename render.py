"""渲染模块 v4。

职责：
- 剂型线条图标
- 货号高亮
- 柜位定位点阵（带行列轴标 + 多命中点 + 自适应单元尺寸，绝不溢出/绝不静默消失）
- 批量查询的高密度概览条（顶部小字：药名 · 货号 · 物理位置）
- 单条结果卡片 HTML（三档密度）

设计目标（本次修复）：
1. 可视化"有时候不显示"——根因是宽柜被降级成纯文字、compact 视图整体藏图、
   多位置药完全没图。本版改为：单元尺寸随列数自适应缩小，最多到 21 列仍可画；
   只有在坐标数据真的无法解析时才退回文字。
2. 多药检索信息密度——顶部输出全部药品的 货号/物理位置 概览，再在下方逐条展示坐标图。
3. 防御渲染——任何一条卡片渲染异常都被捕获，降级为安全文字，不影响整页。
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

# 柜位点阵绝对上限（脏数据保护）。正常数据最大 11 行 × 21 列，远在限内。
HARD_MAX_COLS = 40
HARD_MAX_ROWS = 30


def icon_svg(category):
    return ICONS.get(category, ICONS["box"])


def _collapse_html(s):
    """去掉每行开头的缩进与空行，合并为无缩进的连续 HTML。

    关键修复：Streamlit 的 Markdown 解析器会把「4 个空格缩进的行」当成代码块，
    导致用三引号 f-string 写的卡片 HTML（带缩进）整体渲染成空白/乱码。
    这里把每行 lstrip 后用换行接回，既不影响 HTML 语义，又避免被当代码块。
    """
    if not s:
        return s
    lines = [ln.strip() for ln in str(s).splitlines()]
    return "".join(ln for ln in lines if ln)


def format_code(code):
    text = "" if code is None else str(code).strip()
    if not text:
        return ""
    safe = html.escape(text)
    if len(text) <= 4:
        return f"<span class='row-code-tail'>{safe}</span>"
    return f"{html.escape(text[:-4])}<span class='row-code-tail'>{html.escape(text[-4:])}</span>"


def _cell_size_for(max_col):
    """列数越多，单元越小，尽量让整张图在手机宽度内不必横滑。返回 (cell_px, gap_px)。"""
    if max_col <= 8:
        return 14, 4
    if max_col <= 12:
        return 12, 3
    if max_col <= 16:
        return 10, 3
    if max_col <= 22:
        return 8, 2
    return 6, 2


def cabinet_grid_html(cabinet_info, df, hit_points=None):
    """柜位定位点阵。

    cabinet_info: dict with 分区/位置/段号/__cabinet
    df: 全表，用于查同柜其他位置以画柜子轮廓
    hit_points: 可选 [(行,列), ...]，多位置同时高亮。None 时用 cabinet_info 的单点。
    """
    zone = str(cabinet_info.get("分区", "")).strip()
    pos = str(cabinet_info.get("位置", "")).strip()
    seg = str(cabinet_info.get("段号", "")).strip()
    cabinet = str(cabinet_info.get("__cabinet", "")).strip()

    if not zone or not pos or "-" not in pos or not seg or not seg.isdigit():
        return ""
    shelf_row = pos.rsplit("-", 1)[-1]
    if not shelf_row.isdigit():
        return ""

    subset = df[(df["__zone_norm"] == zone) & (df["__cabinet"] == cabinet)]
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
    if not rows or max_col < 1:
        return ""

    if hit_points:
        hits = {(int(r), int(c)) for r, c in hit_points}
    else:
        hits = {(int(shelf_row), int(seg))}
    primary = (int(shelf_row), int(seg))

    cap_coords = " / ".join(f"行{r} 列{c}" for r, c in sorted(hits))
    cap = (
        "<div class='locator-cap'><span>柜位坐标</span>"
        f"<b>{html.escape(zone)} · {html.escape(cabinet)} · {cap_coords}</b></div>"
    )

    if max(rows) > HARD_MAX_ROWS or max_col > HARD_MAX_COLS:
        return f"<div class='locator locator-text'>{cap}</div>"

    row_max_col = {}
    for gr in rows:
        cols = subset[subset["__gr"].astype(int) == gr]["段号"].astype(int)
        row_max_col[gr] = int(cols.max()) if not cols.empty else max_col

    cell_px, gap_px = _cell_size_for(max_col)

    step = 1 if max_col <= 12 else 2
    col_axis = ["<span class='axis-corner'></span>"]
    for c in range(1, max_col + 1):
        label = str(c) if (c == 1 or c == max_col or c % step == 0) else ""
        col_axis.append(f"<span class='axis-col'>{label}</span>")
    col_axis_html = (
        f"<div class='cabinet-axis-row' "
        f"style='grid-template-columns: var(--axis-w) repeat({max_col}, var(--cell-size));'>"
        f"{''.join(col_axis)}</div>"
    )

    body = []
    for gr in rows:
        rmax = row_max_col.get(gr, max_col)
        body.append(f"<span class='axis-row-label'>{gr}</span>")
        for col in range(1, max_col + 1):
            active = col <= rmax
            cls = "cell"
            if active and (gr, col) in hits:
                cls += " hit" if (gr, col) == primary else " hit hit-alt"
            elif not active:
                cls += " empty"
            body.append(f"<span class='{cls}'></span>")

    grid_html = (
        f"<div class='cabinet-grid' "
        f"style='grid-template-columns: var(--axis-w) repeat({max_col}, var(--cell-size)); "
        f"grid-auto-rows: var(--cell-size);'>"
        f"{''.join(body)}</div>"
    )

    style = f"--cell-size:{cell_px}px; --cell-gap:{gap_px}px;"
    return (
        f"<div class='locator' style='{style}'>"
        f"{cap}"
        "<div class='cabinet-scroll'>"
        f"<div class='cabinet-board'>{col_axis_html}{grid_html}</div>"
        "</div></div>"
    )


def _coords_from_card(card):
    """解析卡片所有 (行,列) 命中点。返回 (主柜 cabinet_info, hit_points or None)。

    仅当所有位置同柜时返回多点高亮；不同柜则只画首位置。
    """
    info = card["柜位信息"]
    locs = card.get("位置列表", []) if hasattr(card, "get") else card["位置列表"]
    if not locs or len(locs) <= 1:
        return info, None

    zone = str(info.get("分区", "")).strip()
    cabinet = str(info.get("__cabinet", "")).strip()
    points = []
    same_cabinet = True
    for label in locs:
        core = label.split("·", 1)[-1] if "·" in label else label
        parts = core.rsplit("-", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            cab_name = parts[0]
            label_zone = label.split("·", 1)[0] if "·" in label else ""
            if cab_name != cabinet or (label_zone and label_zone != zone):
                same_cabinet = False
                break
            points.append((int(parts[1]), int(parts[2])))
        else:
            same_cabinet = False
            break
    if same_cabinet and points:
        return info, points
    return info, None


def _fallback_subtitle(query):
    from search import normalize_text, normalize_code
    code = normalize_code(query)
    norm = normalize_text(query)
    if code and len(code) >= 4 and code == norm:
        return "下面是按相似货号给出的推测"
    if any(c.isalpha() and ord(c) < 128 for c in norm):
        return "下面是按相似名称给出的推测"
    return "下面是按相似药名、主治给出的推测"


# ──────────────────────────────────────────────────────────────
#  多结果柜位总览图：所有命中点同时标在各自柜子上
# ──────────────────────────────────────────────────────────────
def _parse_loc(label):
    """'RX·抗生素B-10-14' -> (zone, cabinet, row, col) 或 None。"""
    if not label:
        return None
    zone = label.split("·", 1)[0] if "·" in label else ""
    core = label.split("·", 1)[-1] if "·" in label else label
    parts = core.rsplit("-", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return (zone, parts[0], int(parts[1]), int(parts[2]))
    return None


import re as _re

# 剂型词：用于在区分标签里保留（颗粒/胶囊/片…），帮助区分同名不同剂型
_FORM_WORDS = ("干混悬剂", "缓释胶囊", "缓释片", "分散片", "咀嚼片", "泡腾片",
               "口服液", "混悬液", "滴丸", "颗粒", "胶囊", "片", "丸", "散",
               "膏", "栓", "贴", "喷雾剂", "气雾剂", "凝胶", "乳膏", "软膏",
               "滴眼液", "滴剂", "口服溶液", "注射液")


def short_drug_label(name, max_len=10):
    """从商品名提取「高区分度的简短标签」。

    去掉品牌括号、拉丁/拼音后缀、批次前缀，保留「成分+剂型」核心，
    例如「头孢克肟颗粒（小快克）HC」-> 「头孢克肟颗粒」，
    「4/7头孢呋辛酯片（达力新）」-> 「头孢呋辛酯片」。
    过长则截断。这样搜「头孢」时能一眼区分 克肟/氨苄/地尼 各是什么。
    """
    if not name:
        return ""
    s = str(name)
    s = _re.sub(r"^\d+/\d+", "", s)               # 批次前缀 4/7
    s = _re.sub(r"[（(][^）)]*[）)]", "", s)        # 品牌括号
    s = _re.sub(r"[A-Za-z]+", "", s)              # 拉丁/拼音码
    s = s.replace("　", "").strip()
    s = _re.sub(r"\s+", "", s)
    # 去掉罗马数字尾巴（Ⅱ Ⅲ 等）以更紧凑，但保留核心
    s = s.rstrip("ⅠⅡⅢⅣⅤ")
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


def overview_map_html(cards, df, max_cabinets=8):
    """把所有结果的命中点画在各自柜子上，并标注高区分度的简短药名。

    一屏看清「这类药都在哪些柜、每个位置具体是哪种」。每个柜子：
    - 一张小点阵显示命中位置（空间直觉）
    - 下方一份「坐标 → 简短药名」清单（克肟/氨苄/地尼 一眼区分）
    """
    if cards is None or cards.empty:
        return ""

    # 收集：cabinet -> {(row,col): [(简短药名, 卡片序号),...]}；保留 zone 用于查轮廓
    cab_cells = {}         # (zone, cabinet) -> dict[(r,c)] -> list[(label, idx)]
    cab_zone_norm = {}     # (zone, cabinet) -> __zone_norm
    for pos_i, (_, card) in enumerate(cards.iterrows()):
        card_idx = pos_i + 1  # 与 render_row 的 №/anchor 对齐
        info = card["柜位信息"]
        znorm = str(info.get("分区", "")).strip()
        label = short_drug_label(card["商品名"])
        for loc in (card["位置列表"] or []):
            parsed = _parse_loc(loc)
            if not parsed:
                continue
            zone, cabinet, row, col = parsed
            key = (zone, cabinet)
            cab_cells.setdefault(key, {}).setdefault((row, col), []).append((label, card_idx))
            cab_zone_norm.setdefault(key, znorm)

    if not cab_cells:
        return ""

    # 按命中点数降序
    def npoints(kv):
        return sum(len(v) for v in kv[1].values())
    ordered = sorted(cab_cells.items(), key=lambda kv: (-npoints(kv), kv[0]))
    shown = ordered[:max_cabinets]
    hidden_n = len(ordered) - len(shown)

    blocks = []
    for (zone, cabinet), cells_map in shown:
        n_hits = sum(len(v) for v in cells_map.values())
        znorm = cab_zone_norm.get((zone, cabinet), zone)
        points = set(cells_map.keys())

        # 坐标→药名清单（按行列排序，紧凑；点击滚动到对应详情卡）
        list_items = []
        for (r, c) in sorted(cells_map.keys()):
            entries = cells_map[(r, c)]
            seen = []
            for nm, cidx in entries:
                if (nm, cidx) not in seen:
                    seen.append((nm, cidx))
            links = " / ".join(
                f"<a class='ov-dn' href='#card-{cidx}'>{html.escape(nm)}</a>"
                for nm, cidx in seen
            )
            list_items.append(
                f"<div class='ov-item'><span class='ov-rc'>{r}-{c}</span>"
                f"<span class='ov-dn-wrap'>{links}</span></div>"
            )
        list_html = "".join(list_items)

        subset = df[(df["__zone_norm"] == znorm) & (df["__cabinet"] == cabinet)]
        grid = ""
        if not subset.empty:
            subset = subset.copy()
            subset["__gr"] = subset["位置"].astype(str).str.rsplit("-", n=1).str[-1]
            subset = subset[
                subset["__gr"].str.isdigit()
                & subset["段号"].astype(str).str.strip().str.isdigit()
            ]
            if not subset.empty:
                rows = sorted({int(v) for v in subset["__gr"]})
                max_col = max(int(v) for v in subset["段号"])
                if rows and max_col >= 1:
                    row_max_col = {}
                    for gr in rows:
                        cols = subset[subset["__gr"].astype(int) == gr]["段号"].astype(int)
                        row_max_col[gr] = int(cols.max()) if not cols.empty else max_col
                    cell = 9 if max_col <= 14 else (7 if max_col <= 20 else 6)
                    gap = 2
                    cells = []
                    for gr in rows:
                        rmax = row_max_col.get(gr, max_col)
                        for col in range(1, max_col + 1):
                            active = col <= rmax
                            if active and (gr, col) in points:
                                cls = "ov-cell hit"
                            elif active:
                                cls = "ov-cell"
                            else:
                                cls = "ov-cell empty"
                            cells.append(f"<span class='{cls}'></span>")
                    grid = (
                        f"<div class='ov-grid' style='grid-template-columns: repeat({max_col}, {cell}px); "
                        f"grid-auto-rows:{cell}px; gap:{gap}px;'>{''.join(cells)}</div>"
                    )

        grid_block = f"<div class='ov-scroll'>{grid}</div>" if grid else ""
        blocks.append(
            "<div class='ov-cab'>"
            f"<div class='ov-cab-head'><span class='ov-cab-name'>{html.escape(zone)}·{html.escape(cabinet)}</span>"
            f"<span class='ov-cab-count'>{n_hits}</span></div>"
            f"{grid_block}"
            f"<div class='ov-list'>{list_html}</div>"
            "</div>"
        )

    more = f"<div class='ov-more'>另有 {hidden_n} 个柜未展开</div>" if hidden_n > 0 else ""
    return (
        "<div class='overview'>"
        "<div class='overview-head'><span>位置总览</span>"
        f"<span class='overview-sub'>{len(cards)} 项 · {len(cab_cells)} 个柜</span></div>"
        f"<div class='overview-grid'>{''.join(blocks)}</div>"
        f"{more}</div>"
    )


# ──────────────────────────────────────────────────────────────
#  批量查询高密度概览条
# ──────────────────────────────────────────────────────────────
def aggregate_html(cards, df, render_row_fn):
    """同通用名聚合视图：同「成分+剂型」的多个品牌折叠成一组，可展开。

    用 HTML5 <details>/<summary> 原生折叠（无 JS，Streamlit 可用）。
    组内 <=2 个品牌默认展开，多的默认折叠。
    """
    if cards is None or cards.empty:
        return ""
    groups = {}
    order = []
    for i, card in cards.iterrows():
        g = short_drug_label(card["商品名"])
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append((i + 1, card))

    blocks = []
    for g in order:
        items = groups[g]
        open_attr = " open" if len(items) <= 2 else ""
        cards_html = "".join(
            render_row_fn(idx, card, df, density="compact") for idx, card in items
        )
        blocks.append(
            f"<details class='agg-group'{open_attr}>"
            f"<summary class='agg-head'>"
            f"<span class='agg-title'>{html.escape(g)}</span>"
            f"<span class='agg-count'>{len(items)}</span>"
            f"</summary>"
            f"<div class='agg-body'>{cards_html}</div>"
            f"</details>"
        )
    return f"<div class='agg'>{''.join(blocks)}</div>"


def picking_html(cards):
    """拣货视图：所有药按 分区→柜→行→列 物理顺序排列，按柜分组。

    爆单时照单走一遍货架即可拣完。行内 checkbox 为纯 CSS 勾选
    （客户端即时划掉，不触发 Streamlit 重跑；进度计数在离线版）。
    多位置药放在首位置，并标注 +N处。
    """
    if cards is None or cards.empty:
        return ""
    items = []
    for i, card in cards.iterrows():
        locs = card["位置列表"] or []
        p = _parse_loc(locs[0]) if locs else None
        items.append((p, i + 1, card, len(locs)))

    def sort_key(it):
        p = it[0]
        if p is None:
            return ("\uffff", "\uffff", 999, 999)
        return (p[0], p[1], p[2], p[3])

    items.sort(key=sort_key)

    cab_totals = {}
    for p, _, _, _ in items:
        name = f"{p[0]}·{p[1]}" if p else "未知位置"
        cab_totals[name] = cab_totals.get(name, 0) + 1

    rows = [
        "<div class='pick-bar'><span class='pt'>拣货清单</span>"
        f"<span class='pp'>共 <b>{len(items)}</b> 项 · {len(cab_totals)} 个柜</span></div>"
    ]
    cur = None
    for p, idx, card, nloc in items:
        cab_name = f"{p[0]}·{p[1]}" if p else "未知位置"
        if cab_name != cur:
            cur = cab_name
            rows.append(
                f"<div class='pick-cab-head'><span class='pcn'>{html.escape(cab_name)}</span>"
                f"<span class='pcc'>{cab_totals[cab_name]} 项</span></div>"
            )
        rc = f"{p[2]}-{p[3]}" if p else "—"
        multi = f"<span class='pick-multi'>+{nloc-1}处</span>" if nloc > 1 else ""
        name = html.escape(str(card["商品名"]))
        code_html = format_code(card["货号"])
        rows.append(
            f"<label class='pick-row' id='card-{idx}'>"
            "<input type='checkbox' class='pick-cb'>"
            "<span class='pick-check'>✓</span>"
            f"<span class='pick-rc'>{rc}</span>"
            f"<span class='pick-body'><span class='pick-name'>{name}{multi}</span>"
            f"<span class='pick-code'>货号 {code_html}</span></span>"
            "</label>"
        )
    return f"<div class='pick'>{''.join(rows)}</div>"


# ──────────────────────────────────────────────────────────────
#  批量查询高密度概览条
# ──────────────────────────────────────────────────────────────
def batch_summary_html(cards):
    """顶部小字概览：全部药品的 № · 药名 · 货号 · 物理位置。"""
    if cards is None or cards.empty:
        return ""
    items = []
    for i, card in cards.iterrows():
        idx = i + 1
        name = html.escape(str(card["商品名"]))
        code = str(card["货号"])
        code_tail = code[-4:] if len(code) >= 4 else code
        code_head = code[:-4] if len(code) >= 4 else ""
        locs = card["位置列表"] if card["位置列表"] else []
        if not locs:
            loc_html = "<span class='sum-loc sum-loc-none'>位置缺失</span>"
        elif len(locs) == 1:
            loc_html = f"<span class='sum-loc'>{html.escape(locs[0])}</span>"
        else:
            extra = "".join(
                f"<span class='sum-loc sum-loc-more'>{html.escape(l)}</span>"
                for l in locs
            )
            loc_html = f"<span class='sum-loc-multi'>{extra}</span>"
        items.append(
            "<div class='sum-item'>"
            f"<span class='sum-idx'>№{idx:02d}</span>"
            f"<span class='sum-name'>{name}</span>"
            f"<span class='sum-code'>{html.escape(code_head)}"
            f"<b>{html.escape(code_tail)}</b></span>"
            f"{loc_html}"
            "</div>"
        )
    return (
        "<div class='batch-summary'>"
        "<div class='batch-summary-head'>全部药品 · 货号 · 物理位置</div>"
        f"<div class='batch-summary-list'>{''.join(items)}</div>"
        "</div>"
    )


# ──────────────────────────────────────────────────────────────
#  单条结果卡片
# ──────────────────────────────────────────────────────────────
def render_row(idx, card, df, density="default", show_locator=None):
    """渲染单条结果卡片。单条异常被捕获降级，不拖垮整页。"""
    try:
        return _collapse_html(_render_row_inner(idx, card, df, density, show_locator))
    except Exception as exc:
        try:
            name = html.escape(str(card["商品名"]))
        except Exception:
            name = "（数据异常）"
        return _collapse_html(
            f"<div class='row'><div class='row-index'><span class='row-index-num'>"
            f"№{idx:02d}</span></div><div class='row-body'>"
            f"<div class='row-name'>{name}</div>"
            f"<div class='row-code' style='color:var(--mute)'>渲染降级：{html.escape(str(exc))[:60]}</div>"
            f"</div></div>"
        )


def _render_row_inner(idx, card, df, density, show_locator):
    name = html.escape(str(card["商品名"]))
    code_html = format_code(card["货号"])
    locations = card["位置列表"]
    category = card["分类"]
    therapy_label = card.get("主治标签", "") if hasattr(card, "get") else card["主治标签"]
    therapy_brief = card.get("主治简介", "") if hasattr(card, "get") else card["主治简介"]

    if len(locations) > 1:
        loc_main = html.escape(locations[0])
        loc_extra = f"<span class='row-loc-extra'>+{len(locations)-1}处</span>"
    else:
        loc_main = html.escape(locations[0]) if locations else ""
        loc_extra = ""

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

    if density != "compact":
        therapy_html = (
            "<div class='row-therapy'>"
            f"<span><b>{html.escape(therapy_label)}</b>{html.escape(therapy_brief)}</span>"
            "</div>"
        )
    else:
        therapy_html = ""

    if show_locator is None:
        show_locator = density != "compact"

    locator = ""
    if show_locator:
        info, hit_points = _coords_from_card(card)
        locator = cabinet_grid_html(info, df, hit_points=hit_points)
        if not locator and locations:
            locs_txt = " / ".join(html.escape(l) for l in locations)
            locator = (
                "<div class='locator locator-text'>"
                "<div class='locator-cap'><span>柜位坐标</span>"
                f"<b>{locs_txt}</b></div></div>"
            )

    icon_html = f"<div class='row-icon'>{icon_svg(category)}</div>"

    if density == "compact":
        locator_block = locator if locator else ""
        return f"""
<div class="row compact" id="card-{idx}">
    <div class="row-index"><span class="row-index-num">№{idx:02d}</span></div>
    <div class="row-body">
        <div class="row-topline">
            <span class="row-code-lead">货号 {code_html}</span>
            <span class="row-loc-lead">{loc_main}{loc_extra}</span>
        </div>
        <div class="row-name">{name}</div>
        {locator_block}
    </div>
    <div class="row-tags">{tag_inner}</div>
    {icon_html}
</div>
"""

    klass = "row" if density == "default" else f"row {density}"
    return f"""
<div class="{klass}" id="card-{idx}">
    <div class="row-index"><span class="row-index-num">№{idx:02d}</span></div>
    <div class="row-body">
        <div class="row-topline">
            <span class="row-code-lead">货号 {code_html}</span>
            <span class="row-loc-lead">{loc_main}{loc_extra}</span>
        </div>
        <div class="row-name">{name}</div>
        <div class="row-tags">{tag_inner}</div>
        {therapy_html}
        {locator}
    </div>
    {icon_html}
</div>
"""
