"""
药品检索 v2
- 性能：先粗筛后打分；缓存搜索结果；预计算排序键
- 体验：编辑风极简 UI，衬线 + 等宽混排；左侧索引线
- 逻辑：去除 ordered_match 对短中文的误命中；货号匹配分级
"""
import html
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import streamlit as st


# ──────────────────────────────────────────────────────────────
#  常量
# ──────────────────────────────────────────────────────────────
REQUIRED_COLUMNS = ["位置", "段号", "商品名", "货号"]
DATA_PATH = Path(__file__).parent / "data.csv"

DEFAULT_PAGE_SIZE = 12
MAX_RESULTS = 60

# 打分阈值集中管理
SCORE_THRESHOLD_HIT = 70      # search() 命中阈值
SCORE_THRESHOLD_FALLBACK = 56  # fallback 召回阈值
SCORE_THRESHOLD_CODE = 78      # 货号相似召回阈值

# 分区显示顺序
ZONE_ORDER = {
    "OTC": 0, "RX": 1, "消杀": 2, "器械": 3,
    "中柜": 4, "侧柜": 5, "保健区L柜": 6, "保健区长柜": 7,
}

CATEGORY_LABELS = {
    "ointment": "外用·膏剂",
    "liquid": "外用·液体",
    "device": "器械",
    "pill": "口服·片丸",
    "granule": "口服·颗粒",
    "oral_liquid": "口服·液体",
    "eye": "眼用",
    "spray": "喷雾",
    "patch": "贴剂",
    "box": "药品",
}

CATEGORY_RULES = [
    ("eye", ["滴眼", "眼膏", "眼药水"]),
    ("spray", ["喷雾", "吸入", "气雾", "雾化"]),
    ("patch", ["贴膏", "膏药", "贴片", "退热贴", "创可贴", "热敷贴", "暖宝", "止痛膏", "麝香膏", "伤湿", "壮骨膏", "祛痛膏", "镇痛膏"]),
    ("ointment", ["药膏", "软膏", "乳膏", "凝胶", "搽剂", "痔疮膏", "霜剂"]),
    ("liquid", ["洗液", "溶液", "消毒", "碘伏", "酒精", "酊", "洗剂", "爽肤水", "含漱液", "漱口水"]),
    ("device", ["器材", "口罩", "棉签", "纱布", "绷带", "血糖仪", "试纸", "体温计", "针头", "护腰", "护膝", "拐杖", "血压计"]),
    ("oral_liquid", ["口服液", "糖浆", "合剂", "混悬液", "悬液", "饮"]),
    ("granule", ["颗粒", "散剂", "冲剂", "干混悬剂"]),
    ("pill", ["胶囊", "片", "丸", "栓", "分散片"]),
]

# 主治弱提示规则 - 三元组：(短标签, 一句话主治, 关键词列表)
# 顺序很重要：上面的优先级高，先匹配到先返回
THERAPY_RULES = [
    # —— 抗感染类 ——
    ("抗菌", "细菌感染性疾病，遵医嘱使用", ["阿莫西林", "头孢", "罗红霉素", "阿奇霉素", "左氧氟沙星", "诺氟沙星", "克拉霉素", "红霉素", "环丙沙星", "莫西沙星", "希诺", "拉氧头孢", "美洛西林", "氨苄西林"]),
    ("抗真菌", "真菌感染，如脚气、皮肤癣等", ["氟康唑", "伊曲康唑", "酮康唑", "特比萘芬", "咪康唑", "克霉唑", "联苯苄唑", "硝酸益康唑", "制霉菌素"]),
    ("抗病毒", "病毒感染、流感等", ["奥司他韦", "阿昔洛韦", "更昔洛韦", "利巴韦林", "金刚烷胺"]),
    ("抗厌氧菌", "妇科、口腔等厌氧菌感染", ["甲硝唑", "替硝唑", "奥硝唑"]),

    # —— 解热镇痛 ——
    ("退热止痛", "发热退烧、头痛、关节痛、轻中度疼痛", ["布洛芬", "对乙酰氨基酚", "氨咖", "双氯芬酸", "洛索洛芬", "吲哚美辛", "塞来昔布", "美洛昔康", "尼美舒利", "阿司匹林泡腾"]),

    # —— 感冒咳嗽 ——
    ("复方感冒药", "感冒引起的发热、鼻塞、头痛等", ["氨酚黄那敏", "氨酚伪麻", "复方氨酚", "氨咖黄敏", "酚麻美敏", "氨咖那敏", "酚氨咖敏", "美扑伪麻", "氨麻"]),
    ("感冒中成药", "风寒/风热感冒、流感等", ["感冒灵", "感冒清", "感冒疏风", "感冒退热", "银翘", "维C银翘", "板蓝根", "莲花清瘟", "蓝芩", "抗病毒口服液", "小柴胡", "桑菊", "九味羌活"]),
    ("镇咳化痰", "咳嗽、痰多、咽痒", ["右美沙芬", "喷托维林", "苯丙哌林", "可待因", "氨溴索", "溴己新", "乙酰半胱氨酸", "羧甲司坦", "止咳", "化痰", "鲜竹沥", "急支糖浆", "肺宁", "复方甘草", "枇杷", "蜜炼川贝", "橘红"]),
    ("清咽利喉", "咽喉肿痛、扁桃体发炎", ["咽扁", "西瓜霜", "金嗓子", "胖大海", "六神丸", "草珊瑚", "玄麦甘桔", "蒲地蓝"]),
    ("通鼻", "鼻塞、过敏性鼻炎", ["羟甲唑啉", "赛洛唑啉", "麻黄碱滴鼻", "鼻通", "鼻炎康", "辛芳鼻炎", "鼻渊", "通窍鼻炎"]),

    # —— 过敏 ——
    ("抗过敏", "皮肤瘙痒、荨麻疹、过敏性鼻炎", ["氯雷他定", "西替利嗪", "地氯雷他定", "依巴斯汀", "扑尔敏", "苯海拉明", "酮替芬", "咪唑斯汀", "氮卓斯汀", "糠酸莫米松", "丙酸氟替卡松", "布地奈德鼻"]),

    # —— 消化系统 ——
    ("抑制胃酸", "胃酸过多、胃溃疡、反流", ["奥美拉唑", "雷贝拉唑", "泮托拉唑", "兰索拉唑", "埃索美拉唑", "艾司奥美拉唑", "法莫替丁", "雷尼替丁"]),
    ("胃黏膜保护", "胃炎、胃溃疡辅助保护", ["铝镁加", "硫糖铝", "枸橼酸铋", "复方铝酸铋", "胶体果胶铋", "瑞巴派特", "替普瑞酮", "膜固思达"]),
    ("助消化", "消化不良、食欲不振、积食", ["健胃消食", "山楂", "保和", "大山楂", "多酶", "胰酶", "复方消化酶", "乳酶生", "干酵母"]),
    ("胃肠动力", "腹胀、嗳气、消化不良", ["多潘立酮", "莫沙必利", "伊托必利", "甲氧氯普胺", "吗丁啉"]),
    ("止泻", "腹泻、肠炎", ["蒙脱石", "洛哌丁胺", "黄连素", "盐酸小檗碱", "止泻", "整肠"]),
    ("通便", "便秘", ["乳果糖", "聚乙二醇", "比沙可啶", "麻仁", "酚酞", "开塞露", "便通", "通便"]),
    ("益生菌", "肠道菌群调节、腹泻便秘辅助", ["双歧杆菌", "枯草杆菌", "酪酸梭菌", "地衣芽孢", "益生菌", "乳酸菌"]),
    ("肝胆", "肝炎、胆囊炎、护肝", ["护肝", "联苯双酯", "甘草酸", "水飞蓟", "胆宁", "消炎利胆", "茴三硫"]),
    ("痔疮", "痔疮、肛裂", ["痔疮", "马应龙", "肛泰", "化痔", "槐角"]),

    # —— 心脑血管 ——
    ("降压", "高血压", ["沙坦", "地平", "普利", "美托洛尔", "比索洛尔", "卡维地洛", "氢氯噻嗪", "吲达帕胺"]),
    ("调脂", "高血脂、动脉硬化", ["他汀", "非诺贝特", "吉非罗齐", "依折麦布"]),
    ("抗血小板", "心脑血管疾病预防", ["阿司匹林肠溶", "氯吡格雷", "替格瑞洛", "双嘧达莫"]),
    ("活血化瘀", "心脑血管中成药辅助", ["丹参", "速效救心", "通心络", "复方丹参", "麝香保心", "稳心", "脑心通", "血塞通", "银杏叶", "脉络宁", "心可舒"]),
    ("扩冠", "心绞痛、冠心病", ["硝酸甘油", "单硝酸异山梨酯", "硝酸异山梨酯"]),

    # —— 内分泌 / 慢病 ——
    ("降糖", "2 型糖尿病", ["二甲双胍", "格列", "阿卡波糖", "西格列汀", "瑞格列奈", "胰岛素"]),
    ("甲状腺", "甲减、甲亢", ["左甲状腺素", "甲巯咪唑", "丙硫氧嘧啶"]),
    ("骨健康", "骨质疏松、骨折恢复", ["阿仑膦酸", "骨化三醇", "碳酸钙D3", "氨基葡萄糖", "盐酸氨基葡萄糖", "硫酸软骨素"]),
    ("妇科调理", "月经不调、内分泌相关", ["黄体酮", "雌二醇", "戊酸雌二醇", "屈螺酮", "短效避孕"]),

    # —— 皮肤外用 ——
    ("皮肤激素", "湿疹、皮炎、过敏性皮疹", ["氢化可的松", "地塞米松乳", "糠酸莫米松乳", "丁酸氢化", "卤米松", "丙酸氟替卡松乳", "他克莫司", "吡美莫司"]),
    ("皮肤抗菌", "皮肤细菌感染、毛囊炎", ["莫匹罗星", "夫西地酸", "红霉素软膏", "百多邦"]),
    ("湿疹瘙痒", "皮肤瘙痒、湿疹辅助", ["炉甘石", "氧化锌", "黑豆馏油", "尿素乳膏", "复方樟脑"]),
    ("烫伤创伤", "轻度烫伤、擦伤", ["京万红", "湿润烧伤", "美宝", "云南白药"]),
    ("跌打损伤", "扭伤、挫伤、肌肉酸痛", ["云南白药", "红花油", "正骨水", "活络油", "麝香追风", "伤湿止痛"]),

    # —— 眼/耳/口 ——
    ("眼药", "眼部炎症、干眼、视疲劳", ["滴眼", "眼药水", "眼膏", "玻璃酸钠", "氯霉素眼", "妥布霉素眼", "更昔洛韦眼", "萘敏维"]),
    ("口腔", "口腔溃疡、咽喉炎", ["西瓜霜含", "复方氯己定含漱", "西吡氯铵", "口腔溃疡膜", "锡类散", "冰硼"]),

    # —— 妇科 ——
    ("妇科外用", "妇科炎症外用", ["洁尔阴", "妇炎洁", "妇科洗液", "苦参洗液", "硝呋太尔", "保妇康栓"]),
    ("妇科口服", "月经不调、妇科炎症", ["妇科千金", "桂枝茯苓", "乌鸡白凤", "益母草", "妇炎康"]),

    # —— 儿科 ——
    ("儿童用药", "适用于儿童的常见症状", ["小儿", "小快克", "婴幼", "儿童", "婴儿"]),

    # —— 神经 / 精神 ——
    ("助眠安神", "失眠、神经衰弱", ["褪黑素", "枣仁安神", "天王补心", "解郁安神", "右佐匹克隆", "艾司唑仑", "佐匹克隆"]),
    ("镇静抗焦虑", "焦虑、抗抑郁等", ["舍曲林", "帕罗西汀", "氟西汀", "阿普唑仑", "黛力新"]),

    # —— 营养 / 保健 ——
    ("维生素", "维生素补充", ["维生素", "维C", "多维", "复合维生素", "VC", "VB", "VE", "复合B"]),
    ("矿物质", "钙、铁、锌等矿物质补充", ["钙片", "碳酸钙", "葡萄糖酸钙", "乳酸钙", "硫酸亚铁", "葡萄糖酸锌", "硫酸锌"]),
    ("营养补剂", "营养补充", ["蛋白粉", "叶酸", "鱼油", "鱼肝油", "DHA", "辅酶Q10", "氨基酸"]),
    ("中药滋补", "中医辨证使用的滋补类", ["阿胶", "人参", "西洋参", "枸杞", "六味地黄", "桂附地黄", "金匮肾气", "归脾"]),

    # —— 中成药综合 ——
    ("祛风除湿", "风湿痹痛、关节炎", ["风湿", "祛风", "壮骨", "追风", "舒筋"]),
    ("清热解毒", "热证、咽喉肿痛、口舌生疮", ["清热", "解毒", "牛黄解毒", "黄连上清", "金银花", "蒲公英", "板蓝", "蓝芩"]),
    ("止血", "外伤出血、内出血辅助", ["云南白药", "止血", "氨甲环酸", "宫血宁"]),
    ("热敷", "宫寒、关节冷痛、肌肉酸痛", ["热敷", "暖宫", "蒸汽眼罩"]),

    # —— 器械耗材 ——
    ("器械", "医用器械、辅助用品", ["口罩", "棉签", "纱布", "绷带", "血糖", "体温", "血压计", "雾化器", "拐杖", "护腰", "护膝", "试纸", "针头"]),
    ("避孕", "避孕、计生用品", ["避孕套", "避孕膜", "短效避孕", "紧急避孕", "毓婷"]),
    ("消杀", "环境/皮肤消毒", ["消毒", "酒精", "碘伏", "84", "新洁尔灭", "苯扎"]),
    ("创可贴", "小伤口护理", ["创可贴", "邦迪", "创口贴"]),
]


# 症状词白名单：从主治简介里抽取这些词加入搜索文本，让"咳嗽""发热"等能反查相关药
SYMPTOM_KEYWORDS = [
    "咳嗽", "发热", "退热", "退烧", "头痛", "鼻塞", "鼻炎", "咽痛", "咽喉", "扁桃体",
    "腹泻", "便秘", "腹胀", "胃酸", "反流", "胃炎", "溃疡", "消化不良", "积食",
    "皮炎", "湿疹", "瘙痒", "荨麻疹", "脚气", "癣", "痔疮", "烫伤",
    "高血压", "血压", "高血脂", "心绞痛", "冠心病", "糖尿病", "甲亢", "甲减",
    "失眠", "焦虑", "抑郁", "骨折", "骨质疏松", "月经", "妇科", "避孕",
    "眼炎", "干眼", "口腔溃疡", "感冒", "流感", "过敏", "中暑", "晕车",
    "扭伤", "挫伤", "出血", "炎症", "感染", "止血",
]
# 注意：归一化在 normalize_text 定义之后再做（见 load_data 中的延迟初始化）

_SYMPTOM_KEYWORDS_NORM = None


def _get_symptoms_norm():
    """延迟初始化的症状词归一化列表。"""
    global _SYMPTOM_KEYWORDS_NORM
    if _SYMPTOM_KEYWORDS_NORM is None:
        _SYMPTOM_KEYWORDS_NORM = [normalize_text(w) for w in SYMPTOM_KEYWORDS]
    return _SYMPTOM_KEYWORDS_NORM


def _extract_symptoms(text):
    """从主治简介中抽取症状词，作为可搜索 token。"""
    t = normalize_text(text)
    return "".join(s for s in _get_symptoms_norm() if s in t)


# 当 THERAPY_RULES 全部不命中时，按 [分区, 商品分类] 给宽泛兜底
# 设计原则：兜底也要"说点有用的"，不能空着
ZONE_THERAPY_FALLBACK = {
    ("OTC", "pill"): "非处方口服药，按说明书使用",
    ("OTC", "granule"): "非处方颗粒/散剂，温水冲服",
    ("OTC", "oral_liquid"): "非处方口服液，按说明书服用",
    ("OTC", "ointment"): "非处方外用药膏，涂于患处",
    ("OTC", "liquid"): "非处方外用液体，清洁患处使用",
    ("OTC", "patch"): "非处方贴剂，贴于患处",
    ("OTC", "spray"): "非处方喷雾剂，按说明使用",
    ("OTC", "eye"): "非处方眼用制剂，按说明滴眼",
    ("RX", "pill"): "处方口服药，遵医嘱使用",
    ("RX", "granule"): "处方颗粒/散剂，遵医嘱使用",
    ("RX", "oral_liquid"): "处方口服液，遵医嘱使用",
    ("RX", "ointment"): "处方外用药膏，遵医嘱使用",
    ("RX", "liquid"): "处方外用液体，遵医嘱使用",
    ("RX", "patch"): "处方贴剂，遵医嘱使用",
    ("RX", "spray"): "处方喷雾剂，遵医嘱使用",
    ("RX", "eye"): "处方眼用制剂，遵医嘱使用",
    ("器械", "device"): "医用器械/耗材",
    ("消杀", "liquid"): "环境或皮肤消毒用",
    ("消杀", "device"): "消毒辅助用品",
    ("中柜", "pill"): "中成药口服剂，中医辨证使用",
    ("中柜", "granule"): "中成药颗粒/散剂，中医辨证使用",
    ("中柜", "oral_liquid"): "中成药口服液，中医辨证使用",
    ("侧柜", "pill"): "口服药，按说明书使用",
    ("侧柜", "granule"): "颗粒/散剂，按说明书使用",
    ("侧柜", "oral_liquid"): "口服液，按说明书使用",
    ("保健区L柜", "pill"): "保健食品/营养补充",
    ("保健区长柜", "pill"): "保健食品/营养补充",
}

ZONE_HINT_RULES = [
    ("OTC·感冒用药 / 侧柜·感冒药", ["感冒", "发烧", "退烧", "咳嗽", "咽", "鼻塞", "流涕", "氨酚"]),
    ("OTC·抗生素 / RX·抗感染", ["阿莫西林", "头孢", "阿奇", "罗红", "左氧", "诺氟", "感染", "消炎"]),
    ("OTC·外用药 / RX·外用药", ["乳膏", "软膏", "凝胶", "洗液", "滴眼", "痔"]),
    ("OTC·消化系统 / 侧柜·胃肠药", ["胃", "腹泻", "消化", "奥美", "蒙脱石", "益生菌", "便秘"]),
    ("OTC·心脑血管 / RX·心脑血管", ["血压", "降压", "心脏", "脑梗", "沙坦", "地平", "他汀"]),
    ("中柜·维生素 / 保健区", ["维生素", "钙", "叶酸", "蛋白", "保健"]),
    ("器械 / 消杀", ["口罩", "棉签", "纱布", "血糖", "体温", "消毒", "酒精", "碘伏"]),
]


# ──────────────────────────────────────────────────────────────
#  页面 & 样式
# ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="药品检索", page_icon="·", layout="centered")

# 设计语言：
#   - 极简编辑风，左对齐密排
#   - 衬线 (Noto Serif SC) 用于商品名 / 标题，强化层级
#   - 等宽 (JetBrains Mono) 用于位置编号、货号 —— 让索引"像编号"
#   - 仅一种强调色 (#c0392b 砖红)，其他全部灰阶
#   - 没有卡片边框，用水平线分隔条目
#   - 左侧两位数索引序号 + 一根细竖线，是整个界面唯一的"装饰"

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;500;700;900&family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600&display=swap');

:root {
    --ink: #0d0d0d;
    --sub: #555;
    --mute: #8a8a8a;
    --line: #1a1a1a;
    --hair: #e5e5e5;
    --bg: #fafaf7;
    --paper: #ffffff;
    --accent: #c0392b;
    --accent-soft: #f9ebe9;
    --warn: #8a6d00;
    --warn-soft: #fdf4d6;
}

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    font-feature-settings: "tnum", "cv11";
    -webkit-font-smoothing: antialiased;
    color: var(--ink);
}
.stApp { background: var(--bg); }
.block-container {
    max-width: 720px;
    padding-top: 2.2rem;
    padding-bottom: 4rem;
}
header[data-testid="stHeader"] { background: transparent; }
#MainMenu, footer { visibility: hidden; }

/* 顶部 */
.masthead {
    border-top: 2px solid var(--line);
    border-bottom: 1px solid var(--line);
    padding: 18px 0 14px;
    margin-bottom: 26px;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
}
.masthead-title {
    font-family: 'Noto Serif SC', serif;
    font-weight: 900;
    font-size: 30px;
    letter-spacing: -.02em;
    line-height: 1;
}
.masthead-meta {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: .12em;
    text-transform: uppercase;
    color: var(--mute);
}

/* 输入框 */
.stTextInput > label { display: none; }
.stTextInput > div > div {
    background: transparent !important;
}
.stTextInput > div > div > input {
    font-family: 'Noto Serif SC', serif !important;
    font-size: 22px !important;
    font-weight: 500 !important;
    padding: 10px 0 12px 0 !important;
    background: transparent !important;
    border: none !important;
    border-bottom: 1.5px solid var(--line) !important;
    border-radius: 0 !important;
    color: var(--ink) !important;
    caret-color: var(--accent);
}
.stTextInput > div > div > input::placeholder {
    color: #b8b8b8;
    font-weight: 400;
    font-style: italic;
}
.stTextInput > div > div > input:focus {
    box-shadow: none !important;
    border-bottom-color: var(--accent) !important;
}

/* 输入框下方提示 */
.input-hint {
    display: flex;
    justify-content: space-between;
    margin-top: 8px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--mute);
}

/* 快捷标签 */
.quick-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    margin: 22px 0 8px;
    padding-top: 18px;
    border-top: 1px solid var(--hair);
}
.quick-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: .12em;
    text-transform: uppercase;
    color: var(--mute);
    margin-right: 6px;
}
.quick-item {
    font-family: 'Noto Serif SC', serif;
    font-size: 14px;
    color: var(--ink);
    border-bottom: 1px solid var(--ink);
    padding-bottom: 1px;
    cursor: pointer;
}

/* 结果区头 */
.result-meta {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 22px 0 8px;
    border-bottom: 1px solid var(--line);
    margin-top: 18px;
}
.result-meta-count {
    font-family: 'Noto Serif SC', serif;
    font-size: 13px;
    color: var(--sub);
}
.result-meta-count b {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
    font-size: 15px;
    color: var(--ink);
    padding: 0 2px;
}
.result-meta-note {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--mute);
}

/* 结果条目 - 三档密度
   .compact  : >12 条，紧凑单行式，藏柜位图
   (default) : 5-12 条，标准
   .spacious : <=4 条，宽松，给每条更多呼吸 */
.row {
    display: grid;
    grid-template-columns: 38px 1fr;
    gap: 14px;
    padding: 14px 0;
    border-bottom: 1px solid var(--hair);
    position: relative;
}
.row.compact {
    grid-template-columns: 32px 1fr auto;
    gap: 12px;
    padding: 8px 0;
    align-items: center;
}
.row.compact .row-index { padding-top: 0; padding-right: 10px; }
.row.compact .row-loc {
    margin-bottom: 2px;
    font-size: 11.5px;
}
.row.compact .row-name {
    font-size: 15px;
    line-height: 1.3;
}
.row.compact .row-code {
    margin-top: 2px;
    font-size: 10.5px;
}
.row.compact .row-tags {
    margin-top: 0;
    align-self: center;
    font-size: 10px;
    white-space: nowrap;
    color: var(--mute);
}
.row.compact .row-tags > span { display: inline; }
.row.compact .locator { display: none; }

.row.spacious { padding: 22px 0; }
.row.spacious .row-name { font-size: 21px; }

.row-index {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: .05em;
    color: var(--mute);
    padding-top: 4px;
    border-right: 1px solid var(--hair);
    padding-right: 12px;
    margin-right: 0;
    line-height: 1.2;
}
.row-index-num {
    display: block;
    font-size: 11px;
    color: var(--mute);
}

.row-loc {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
    font-size: 13px;
    letter-spacing: .03em;
    color: var(--accent);
    text-transform: uppercase;
    line-height: 1.2;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.row-loc-extra {
    color: var(--mute);
    font-weight: 500;
    font-size: 11px;
}
.row-name {
    font-family: 'Noto Serif SC', serif;
    font-weight: 700;
    font-size: 18px;
    line-height: 1.32;
    color: var(--ink);
    letter-spacing: -.005em;
}
.row-code {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11.5px;
    color: var(--sub);
    margin-top: 6px;
    letter-spacing: .04em;
}
.row-code-tail {
    color: var(--ink);
    font-weight: 700;
    border-bottom: 1.5px solid var(--accent);
    padding: 0 1px;
}
.row-tags {
    margin-top: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--sub);
}
.row-tag-divider { color: var(--hair); }
.row-tag-warn { color: var(--warn); font-weight: 700; }
.row-tag-danger { color: var(--accent); font-weight: 700; }

/* 主治弱提示：编辑风斜体 + 起首装饰短线 */
.row-therapy {
    font-family: 'Noto Serif SC', serif;
    font-size: 12.5px;
    line-height: 1.5;
    color: var(--sub);
    margin-top: 8px;
    padding-top: 7px;
    border-top: 1px dashed var(--hair);
    font-style: italic;
    display: flex;
    gap: 8px;
}
.row-therapy::before {
    content: '';
    flex-shrink: 0;
    width: 14px;
    height: 1px;
    background: var(--mute);
    margin-top: 9px;
}
.row-therapy b {
    font-style: normal;
    font-weight: 700;
    color: var(--ink);
    margin-right: 6px;
    letter-spacing: -.01em;
}
.row.compact .row-therapy { display: none; }
.row.spacious .row-therapy { font-size: 13px; }

/* 剂型图标：放在序号下方，低饱和度，作为视觉锚点 */
.row-icon {
    margin-top: 10px;
    color: var(--mute);
    opacity: .55;
    display: flex;
    justify-content: flex-start;
}
.row-icon svg {
    width: 26px;
    height: 26px;
}
.row.compact .row-icon {
    margin-top: 0;
    margin-left: -2px;
}
.row.compact .row-icon svg { width: 18px; height: 18px; }
.row.spacious .row-icon svg { width: 30px; height: 30px; }

/* 柜位定位图：极简点阵 */
.locator {
    margin-top: 14px;
    padding: 12px 14px;
    background: var(--paper);
    border: 1px solid var(--hair);
}
.locator-cap {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--mute);
    margin-bottom: 9px;
    display: flex;
    justify-content: space-between;
}
.locator-cap b {
    color: var(--ink);
    font-weight: 700;
}
.cabinet-grid {
    display: inline-grid;
    grid-auto-rows: 10px;
    gap: 3px;
    line-height: 0;
}
.cell {
    display: block;
    width: 10px;
    height: 10px;
    background: #eaeaea;
    border-radius: 1px;
}
.cell.empty { background: transparent; }
.cell.hit {
    background: var(--accent);
    box-shadow: 0 0 0 2px rgba(192, 57, 43, .15);
}

/* 空状态 / 提示条 */
.empty {
    padding: 60px 0 30px;
    text-align: center;
    font-family: 'Noto Serif SC', serif;
}
.empty-mark {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: .2em;
    color: var(--accent);
    text-transform: uppercase;
    margin-bottom: 12px;
}
.empty-title {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -.01em;
}
.empty-sub {
    font-family: 'Inter', sans-serif;
    color: var(--sub);
    font-size: 13px;
    margin-top: 8px;
}

.section-head {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: .15em;
    text-transform: uppercase;
    color: var(--mute);
    padding: 26px 0 6px;
    border-bottom: 1px solid var(--hair);
    margin-bottom: 4px;
}

.zone-hints {
    margin: 14px 0;
    padding: 10px 0;
}
.zone-hint-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: .12em;
    text-transform: uppercase;
    color: var(--mute);
    margin-bottom: 8px;
}
.zone-hint-list {
    font-family: 'Noto Serif SC', serif;
    font-size: 14px;
    color: var(--ink);
    line-height: 1.6;
}
.zone-hint-list span {
    border-bottom: 1px solid var(--ink);
    margin-right: 14px;
    padding-bottom: 1px;
}

/* 底部 */
.colophon {
    margin-top: 50px;
    padding-top: 18px;
    border-top: 2px solid var(--line);
    display: flex;
    justify-content: space-between;
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--mute);
}

/* Streamlit 控件清理 */
[data-testid="stExpander"] {
    border: none !important;
    background: transparent !important;
}
[data-testid="stExpander"] summary {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px !important;
    letter-spacing: .12em !important;
    text-transform: uppercase;
    color: var(--mute) !important;
}

/* 移动端 */
@media (max-width: 520px) {
    .block-container { padding-left: 1.1rem; padding-right: 1.1rem; padding-top: 1.6rem; }
    .masthead-title { font-size: 24px; }
    .stTextInput > div > div > input { font-size: 19px !important; }
    .row { grid-template-columns: 30px 1fr; gap: 10px; }
    .row-index { padding-right: 8px; }
    .row-name { font-size: 17px; }
    .cell { width: 8px; height: 8px; }
}
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
#  归一化 & 排序键
# ──────────────────────────────────────────────────────────────
_NORM_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]")


def normalize_text(value):
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    text = text.replace("（", "(").replace("）", ")")
    return _NORM_RE.sub("", text)


def normalize_code(value):
    if value is None:
        return ""
    return re.sub(r"\D", "", str(value))


def zone_sort_value(zone):
    text = str(zone).strip()
    return (ZONE_ORDER.get(text, 99), text)


_POS_RE = re.compile(r"([A-Z]+)(\d+)(.*)")


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


def therapy_hint(name, zone="", category="box"):
    """返回 (短标签, 一句话主治)。保证每条药都有返回。"""
    text = normalize_text(name)
    for label, brief, keywords in THERAPY_RULES:
        for kw in keywords:
            if normalize_text(kw) in text:
                return (label, brief)
    # 兜底：按 (分区, 分类) 查
    zone = str(zone).strip()
    fallback = ZONE_THERAPY_FALLBACK.get((zone, category))
    if fallback:
        # 用分类标签作为短 label
        return (CATEGORY_LABELS.get(category, "药品").split("·")[-1], fallback)
    # 最终兜底
    return ("药品", "按说明书或遵医嘱使用")


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
#  数据加载（缓存）
# ──────────────────────────────────────────────────────────────
def data_fingerprint():
    if not DATA_PATH.exists():
        return 0, 0
    stat = DATA_PATH.stat()
    return stat.st_mtime_ns, stat.st_size


@st.cache_data(show_spinner=False)
def load_data(mtime_ns, size):
    _ = (mtime_ns, size)
    if not DATA_PATH.exists():
        return None, ["未找到 data.csv，请放置在 app.py 同目录"]

    df = None
    last_err = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            candidate = pd.read_csv(DATA_PATH, encoding=encoding, dtype=str).fillna("")
            # 简单乱码探测：如果商品名列里 replacement char 太多说明解码错了
            sample = "".join(candidate.get("商品名", pd.Series(dtype=str)).head(20).tolist())
            if "\ufffd" in sample:
                continue
            df = candidate
            break
        except Exception as exc:
            last_err = exc
            continue
    if df is None:
        return None, [f"读取 data.csv 失败：{last_err}"]

    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if "分区" not in df.columns:
        df["分区"] = ""

    # 预计算
    df["__name_norm"] = df["商品名"].map(normalize_text)
    df["__code_norm"] = df["货号"].map(normalize_code)
    if "适应症关键词" in df.columns:
        df["__keyword_norm"] = df["适应症关键词"].map(normalize_text)
    else:
        df["__keyword_norm"] = ""
    df["__zone_norm"] = df["分区"].astype(str).str.strip()
    df["__loc_label"] = df.apply(
        lambda r: location_label(r["分区"], r["位置"], r["段号"]), axis=1
    )
    df["__category"] = df["商品名"].map(classify_product)

    # 主治弱提示预计算
    therapy = df.apply(
        lambda r: therapy_hint(r["商品名"], r["__zone_norm"], r["__category"]), axis=1
    )
    df["__therapy_label"] = therapy.map(lambda t: t[0])
    df["__therapy_brief"] = therapy.map(lambda t: t[1])
    # 把主治 label + 从 brief 抽取的症状词 加入搜索文本
    # 这样搜"咳嗽""退烧"等症状词能召回相关药，又不会让"按说明书"等通用词污染检索
    df["__therapy_norm"] = (
        df["__therapy_label"].map(normalize_text)
        + df["__therapy_brief"].map(_extract_symptoms)
    )
    df["__search_text"] = df["__name_norm"] + df["__keyword_norm"] + df["__therapy_norm"]

    issues = validate_data(df)
    return df, issues


def validate_data(df):
    issues = []
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        issues.append(f"缺少字段：{', '.join(missing)}")
        return issues
    for label in ("商品名", "货号", "位置"):
        empty = df[label].astype(str).str.strip().eq("").sum()
        if empty:
            issues.append(f"{empty} 条缺{label}")
    multi = df.groupby("货号")["__loc_label"].nunique()
    multi_count = int((multi > 1).sum())
    if multi_count:
        issues.append(f"{multi_count} 个货号存在多个物理位置，建议核对")
    return issues


# ──────────────────────────────────────────────────────────────
#  打分 —— 关键优化：先粗筛再打分
# ──────────────────────────────────────────────────────────────
def code_match_score(query_code, code):
    """货号匹配，仅在数字数据上工作。"""
    if not query_code or not code:
        return 0
    if query_code == code:
        return 150
    if len(query_code) == 4 and code.endswith(query_code):
        return 142
    # 5-7 位：只接受后缀匹配，不再做 substring（避免误命中）
    if 4 < len(query_code) < len(code):
        if code.endswith(query_code):
            return 132
        # 接近后缀（差 1 位）
        tail = code[-len(query_code):]
        diffs = sum(1 for a, b in zip(query_code, tail) if a != b)
        if diffs == 1:
            return 96
        return 0
    # 等长全等已在最上面处理；保留 lstrip 去前导零等值
    if len(query_code) == len(code):
        if query_code.lstrip("0") == code.lstrip("0") and query_code.lstrip("0"):
            return 130
    return 0


def code_fuzzy_score(query_code, code):
    """fallback 用，比 code_match_score 宽松一档。"""
    if not query_code or not code:
        return 0
    direct = code_match_score(query_code, code)
    if direct:
        return direct
    # 长度匹配但有少量差异
    tail = code[-len(query_code):] if len(query_code) <= len(code) else code
    if len(tail) == len(query_code):
        diffs = sum(1 for a, b in zip(query_code, tail) if a != b)
        if diffs == 1:
            return 104
        if diffs == 2 and len(query_code) >= 4:
            return 82
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
    """单行打分，假设已经是候选行。"""
    scores = []

    # 货号
    if query_code and code_norm:
        cs = code_match_score(query_code, code_norm)
        if cs:
            scores.append(cs)

    # 名称完全包含
    if query_norm:
        if query_norm in name_norm:
            # 越靠前权重越高
            pos = name_norm.find(query_norm)
            bonus = max(0, 10 - pos)
            scores.append(115 + bonus)
        elif query_norm in search_text:
            scores.append(96)
        # 相似度（仅对长度 >=2 的查询）
        if len(query_norm) >= 2:
            ratio = SequenceMatcher(None, query_norm, name_norm).ratio()
            if ratio >= 0.5:
                scores.append(55 + ratio * 45)

    return max(scores) if scores else 0


def prefilter(df, query_norm, query_code):
    """向量化粗筛，返回候选行的 mask。"""
    mask = pd.Series(False, index=df.index)
    if query_norm:
        # 子串命中（向量化）
        mask |= df["__search_text"].str.contains(query_norm, regex=False, na=False)
        # 名称模糊：取首尾两个字符作为粗筛信号
        if len(query_norm) >= 2:
            head = query_norm[0]
            tail = query_norm[-1]
            mask |= (
                df["__name_norm"].str.contains(head, regex=False, na=False)
                & df["__name_norm"].str.contains(tail, regex=False, na=False)
            )
    if query_code and len(query_code) >= 4:
        # 货号后缀 / 包含
        mask |= df["__code_norm"].str.endswith(query_code, na=False)
        if len(query_code) >= 6:
            mask |= df["__code_norm"].str.contains(query_code, regex=False, na=False)
    return mask


# ──────────────────────────────────────────────────────────────
#  搜索主入口 —— 缓存
# ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, max_entries=64)
def search_cached(mtime_ns, size, query):
    """对外接口，按 (data 指纹, query) 缓存结果。"""
    df, _ = load_data(mtime_ns, size)
    if df is None:
        return pd.DataFrame()
    return _search_impl(df, query)


def _search_impl(df, query):
    query_norm = normalize_text(query)
    query_code = normalize_code(query)

    # 单字符/纯数字 <4 位 直接拒绝
    if not query_norm and not query_code:
        return pd.DataFrame()
    if query_code and query_code == query_norm and len(query_code) < 4:
        return pd.DataFrame()
    if len(query_norm) <= 1 and len(query_code) < 4:
        return pd.DataFrame()

    # 1) 货号 4 位短路
    if query_code and query_code == query_norm and len(query_code) == 4:
        hits = df[df["__code_norm"].str.endswith(query_code, na=False)].copy()
        if hits.empty:
            return hits
        hits["匹配分"] = 142.0
        return _rank(hits)

    # 2) 完整货号短路
    if len(query_code) >= 6:
        hits = df[df["__code_norm"].eq(query_code)].copy()
        if not hits.empty:
            hits["匹配分"] = 150.0
            return _rank(hits)

    # 3) 粗筛 → 打分
    mask = prefilter(df, query_norm, query_code)
    cands = df[mask]
    if cands.empty:
        return cands

    # itertuples 会把以 _ 开头的列名改写，用 zip 显式取列更稳
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


def _rank(df):
    """排序：匹配分 → 分区 → 位置 → 段号。"""
    out = df.copy()
    out["__zone_key"] = out["分区"].map(zone_sort_value)
    out["__pos_key"] = out["位置"].map(natural_position_key)
    out["__seg_key"] = out["段号"].map(segment_key)
    out = out.sort_values(
        by=["匹配分", "__zone_key", "__pos_key", "__seg_key"],
        ascending=[False, True, True, True],
    )
    return out.drop(columns=["__zone_key", "__pos_key", "__seg_key"])


def search_batch(mtime_ns, size, query):
    terms = [t.strip() for t in query.split("/") if t.strip()]
    if not terms:
        return pd.DataFrame(), []
    pieces = []
    missing = []
    for term in terms:
        r = search_cached(mtime_ns, size, term)
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
#  Fallback 推荐
# ──────────────────────────────────────────────────────────────
def fallback_suggestions(mtime_ns, size, query, limit=6):
    df, _ = load_data(mtime_ns, size)
    if df is None:
        return pd.DataFrame(), []

    query_norm = normalize_text(query)
    query_code = normalize_code(query)

    # 粗筛宽一点
    mask = pd.Series(False, index=df.index)
    if query_norm and len(query_norm) >= 2:
        head, tail = query_norm[0], query_norm[-1]
        mask |= df["__name_norm"].str.contains(head, regex=False, na=False)
        mask |= df["__name_norm"].str.contains(tail, regex=False, na=False)
    if query_code and len(query_code) >= 3:
        # 后 3 位接近
        prefix3 = query_code[:3]
        suffix3 = query_code[-3:]
        mask |= df["__code_norm"].str.endswith(suffix3, na=False)
        mask |= df["__code_norm"].str[:3].eq(prefix3)

    cands = df[mask]
    if cands.empty:
        return pd.DataFrame(), guessed_zone_hints(query)

    rows = []
    cand_records = cands.to_dict("records")
    for rec in cand_records:
        n_norm = rec["__name_norm"]
        c_norm = rec["__code_norm"]
        n_score = 0
        if query_norm:
            if query_norm in n_norm:
                n_score = 110
            else:
                ratio = SequenceMatcher(None, query_norm, n_norm).ratio()
                if ratio >= 0.42:
                    n_score = 55 + ratio * 45
        c_score = code_fuzzy_score(query_code, c_norm) if query_code else 0
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

    out = pd.DataFrame(rows).sort_values("匹配分", ascending=False).head(limit * 4)
    cards = _to_cards(out, max_cards=limit)
    return cards, guessed_zone_hints(query)


# ──────────────────────────────────────────────────────────────
#  结果整理 → 卡片
# ──────────────────────────────────────────────────────────────
def _to_cards(results, max_cards=MAX_RESULTS):
    if results.empty:
        return pd.DataFrame()

    grouped = results.groupby(["商品名", "货号"], dropna=False, sort=False)
    cards = []
    for (name, code), group in grouped:
        locations = list(dict.fromkeys(group["__loc_label"].tolist()))
        zones = list(dict.fromkeys(group["分区"].astype(str).tolist()))
        max_score = float(group["匹配分"].max())
        category = group["__category"].iloc[0] if "__category" in group.columns else classify_product(name)
        # therapy 提取：优先用预计算字段，否则现算
        if "__therapy_label" in group.columns:
            t_label = group["__therapy_label"].iloc[0]
            t_brief = group["__therapy_brief"].iloc[0]
        else:
            t_label, t_brief = therapy_hint(name, zones[0] if zones else "", category)
        cards.append({
            "商品名": name,
            "货号": code,
            "位置列表": locations,
            "首位置": locations[0] if locations else "",
            "分区列表": zones,
            "主分区": zones[0] if zones else "",
            "首行": group.iloc[0],
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
    """批量查询时按分区聚集，方便一次性取货。"""
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


# ──────────────────────────────────────────────────────────────
#  渲染
# ──────────────────────────────────────────────────────────────

# 极简单色描边图标，1.4 stroke，继承父级颜色
# 10 种分类对应不同形态，让用户在视觉扫读时能迅速辨认剂型
ICONS = {
    # 药片/胶囊：分二色的椭圆胶囊
    "pill": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="11" width="20" height="10" rx="5"/><line x1="16" y1="11" x2="16" y2="21"/></svg>',
    # 颗粒：纸袋
    "granule": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 9 L23 9 L24 26 L8 26 Z"/><path d="M9 9 L11 6 L21 6 L23 9"/><line x1="12" y1="14" x2="20" y2="14"/></svg>',
    # 口服液：药瓶
    "oral_liquid": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13 5 L19 5 L19 10 L21 13 L21 25 Q21 27 19 27 L13 27 Q11 27 11 25 L11 13 L13 10 Z"/><line x1="11" y1="16" x2="21" y2="16"/></svg>',
    # 软膏：扁锥形管
    "ointment": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M7 14 L23 11 L26 14 L23 17 L7 14 Z"/><rect x="3" y="11" width="4" height="6" rx="0.5"/><path d="M26 14 L29 12 L29 16 Z"/></svg>',
    # 液体：消毒/洗剂瓶
    "liquid": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13 5 L19 5 L19 8 L22 11 L22 25 Q22 27 20 27 L12 27 Q10 27 10 25 L10 11 L13 8 Z"/><path d="M13 18 Q16 14 19 18 Q19 22 16 22 Q13 22 13 18"/></svg>',
    # 眼药：滴管+液滴
    "eye": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="16" cy="18" rx="11" ry="6"/><circle cx="16" cy="18" r="3"/><path d="M22 6 L26 10 L22 14 Z"/></svg>',
    # 喷雾：喷头瓶
    "spray": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="11" y="13" width="10" height="14" rx="1"/><path d="M14 13 L14 9 L18 9 L18 13"/><line x1="18" y1="11" x2="24" y2="11"/><line x1="24" y1="11" x2="24" y2="14"/><circle cx="27" cy="10" r="0.6" fill="currentColor"/><circle cx="28" cy="13" r="0.6" fill="currentColor"/><circle cx="26" cy="14" r="0.6" fill="currentColor"/></svg>',
    # 贴剂：方形 + 中心点
    "patch": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="9" width="18" height="14" rx="2"/><line x1="14" y1="9" x2="14" y2="23"/><line x1="18" y1="9" x2="18" y2="23"/><circle cx="16" cy="16" r="1.5"/></svg>',
    # 器械：十字符号
    "device": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="20" height="20" rx="2"/><line x1="16" y1="11" x2="16" y2="21"/><line x1="11" y1="16" x2="21" y2="16"/></svg>',
    # 药盒：兜底
    "box": '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="9" width="18" height="17" rx="1"/><path d="M10 9 L12 5 L20 5 L22 9"/><line x1="7" y1="15" x2="25" y2="15"/></svg>',
}


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


def cabinet_grid_html(row_data, df):
    """极简点阵柜位定位。"""
    zone = str(row_data.get("分区", "")).strip()
    pos = str(row_data.get("位置", "")).strip()
    seg = str(row_data.get("段号", "")).strip()
    if not zone or not pos or not seg or "-" not in pos or not seg.isdigit():
        return ""
    cabinet, shelf_row = pos.rsplit("-", 1)
    if not shelf_row.isdigit():
        return ""

    subset = df[
        (df["__zone_norm"] == zone)
        & df["位置"].astype(str).str.startswith(f"{cabinet}-")
    ]
    if subset.empty:
        return ""
    subset = subset.copy()
    subset["__gr"] = subset["位置"].astype(str).str.rsplit("-", n=1).str[-1]
    subset = subset[subset["__gr"].str.isdigit() & subset["段号"].astype(str).str.strip().str.isdigit()]
    if subset.empty:
        return ""

    rows = sorted({int(v) for v in subset["__gr"]})
    max_col = max(int(v) for v in subset["段号"])
    if len(rows) > 18 or max_col > 24:
        return ""

    hit_row, hit_col = int(shelf_row), int(seg)
    cells = []
    for gr in rows:
        row_cols = subset[subset["__gr"].astype(int) == gr]["段号"].astype(int)
        row_max = max(row_cols) if not row_cols.empty else max_col
        for col in range(1, max_col + 1):
            active = col <= row_max
            cls = "cell"
            if active and gr == hit_row and col == hit_col:
                cls += " hit"
            elif not active:
                cls += " empty"
            cells.append(f"<span class='{cls}'></span>")

    return (
        "<div class='locator'>"
        f"<div class='locator-cap'><span>柜位坐标</span><b>{html.escape(zone)} · {html.escape(cabinet)} · 行{hit_row} 列{hit_col}</b></div>"
        f"<div class='cabinet-grid' style='grid-template-columns: repeat({max_col}, 10px);'>{''.join(cells)}</div>"
        "</div>"
    )


def render_row(idx, card, df, density="default"):
    """
    density:
        compact  - 信息密度高，单行紧凑，藏柜位图和主治，标签放右侧
        default  - 标准，含主治弱提示
        spacious - 宽松，柜位图必显，主治字号更大
    """
    name = html.escape(str(card["商品名"]))
    code_html = format_code(card["货号"])
    locations = card["位置列表"]
    category = card["分类"]
    therapy_label = card.get("主治标签", "") if hasattr(card, "get") else card["主治标签"]
    therapy_brief = card.get("主治简介", "") if hasattr(card, "get") else card["主治简介"]
    first_row = card["首行"]

    if len(locations) > 1:
        loc_main = html.escape(locations[0])
        loc_extra = f"<span class='row-loc-extra'>+{len(locations)-1}处</span>"
    else:
        loc_main = html.escape(locations[0]) if locations else ""
        loc_extra = ""

    # tags：分类、主治 label、查询项、推荐原因
    tags = [CATEGORY_LABELS.get(category, "药品")]
    if therapy_label and therapy_label not in tags:
        tags.append(therapy_label)
    if card["查询项"]:
        tags.append(f"查：{card['查询项']}")
    if card["推荐原因"]:
        tags.append(card["推荐原因"])

    # compact 模式只保留 1 个最相关 tag
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

    # 主治弱提示行（compact 模式 CSS 已经隐藏，这里也省去构造以节省字符）
    if density != "compact":
        therapy_html = (
            f"<div class='row-therapy'>"
            f"<span><b>{html.escape(therapy_label)}</b>{html.escape(therapy_brief)}</span>"
            f"</div>"
        )
    else:
        therapy_html = ""

    # 柜位图：compact 不显示；default 当单位置时显示；spacious 始终尝试显示
    show_locator = density != "compact" and len(locations) == 1
    locator = cabinet_grid_html(first_row, df) if show_locator else ""

    icon_html = f"<div class='row-icon'>{icon_svg(category)}</div>"

    if density == "compact":
        # 三列：序号+图标 | 名+位置+货号 | 标签
        return f"""
<div class="row compact">
    <div class="row-index">
        <span class="row-index-num">№{idx:02d}</span>
        {icon_html}
    </div>
    <div class="row-body">
        <div class="row-loc">{loc_main}{loc_extra}</div>
        <div class="row-name">{name}</div>
        <div class="row-code">货号 {code_html}</div>
    </div>
    <div class="row-tags">{tag_inner}</div>
</div>
"""

    klass = "row" if density == "default" else f"row {density}"
    return f"""
<div class="{klass}">
    <div class="row-index">
        <span class="row-index-num">№{idx:02d}</span>
        {icon_html}
    </div>
    <div class="row-body">
        <div class="row-loc">{loc_main}{loc_extra}</div>
        <div class="row-name">{name}</div>
        <div class="row-code">货号 {code_html}</div>
        <div class="row-tags">{tag_inner}</div>
        {therapy_html}
        {locator}
    </div>
</div>
"""


# ──────────────────────────────────────────────────────────────
#  主流程
# ──────────────────────────────────────────────────────────────
def main():
    mtime_ns, size = data_fingerprint()
    df, issues = load_data(mtime_ns, size)
    if df is None:
        st.error(issues[0] if issues else "数据加载失败")
        return

    # 顶部
    st.markdown(f"""
    <div class="masthead">
        <div class="masthead-title">药品检索</div>
        <div class="masthead-meta">{len(df)} ITEMS · {df['__loc_label'].nunique()} LOCS</div>
    </div>
    """, unsafe_allow_html=True)

    # session state for recent
    if "recent" not in st.session_state:
        st.session_state.recent = []

    # 预填查询
    prefill = st.session_state.pop("prefill", "")
    query = st.text_input(
        "搜索",
        value=prefill,
        placeholder="药名 · 品牌 · 货号后四位",
        label_visibility="collapsed",
        key="query_input",
    )

    st.markdown("""
    <div class="input-hint">
        <span>4位数字 = 货号后四位</span>
        <span>批量查询用 / 分隔</span>
    </div>
    """, unsafe_allow_html=True)

    # 数据校验提示
    if issues:
        with st.expander(f"数据校验 · {len(issues)} 项提示", expanded=False):
            for issue in issues:
                st.write(f"· {issue}")

    if not query.strip():
        # 空态：最近 + 快捷
        if st.session_state.recent:
            st.markdown("<div class='section-head'>最近</div>", unsafe_allow_html=True)
            cols = st.columns(min(len(st.session_state.recent), 4))
            for i, term in enumerate(st.session_state.recent[:4]):
                with cols[i]:
                    if st.button(term, key=f"recent_{i}", use_container_width=True):
                        st.session_state.prefill = term
                        st.rerun()
        else:
            st.markdown("""
            <div class="quick-row">
                <span class="quick-label">提示</span>
                <span class="quick-item">输入药名汉字</span>
                <span class="quick-item">或输入货号后 4 位</span>
                <span class="quick-item">或用 / 批量查</span>
            </div>
            """, unsafe_allow_html=True)

        _render_colophon(df, size)
        return

    # 记录最近
    qclean = query.strip()
    if qclean and qclean not in st.session_state.recent:
        st.session_state.recent.insert(0, qclean)
        st.session_state.recent = st.session_state.recent[:8]

    is_batch = "/" in query
    if is_batch:
        results, missing = search_batch(mtime_ns, size, query)
    else:
        results = search_cached(mtime_ns, size, query)
        missing = []

    if results.empty:
        _render_empty(query, mtime_ns, size, df)
    else:
        _render_results(results, df, is_batch, missing)

    _render_colophon(df, size)


def _render_results(results, df, is_batch, missing):
    cards = _to_cards(results, max_cards=MAX_RESULTS)
    if is_batch:
        cards = batch_sort_cards(cards).reset_index(drop=True)

    total = len(cards)

    # 信息密度按总数自适应：
    #   total <= 4         : spacious，每条信息最丰富
    #   5  <= total <= 12  : default
    #   total >= 13        : compact，单行 + 隐藏柜位图
    # 同时按密度调整初屏展示数量
    if total <= 4:
        density = "spacious"
        show_n = total
    elif total <= 12:
        density = "default"
        show_n = total
    else:
        density = "compact"
        show_n = min(20, total)  # compact 模式更密，可以一次看更多

    note_parts = []
    if total > show_n:
        note_parts.append(f"还有 {total - show_n} 条")
    if missing:
        note_parts.append(f"未找到 {len(missing)} 项")
    if not note_parts:
        note_parts.append({"compact": "紧凑视图", "spacious": "舒展视图", "default": "标准视图"}[density])
    note = " · ".join(note_parts)

    st.markdown(f"""
    <div class="result-meta">
        <div class="result-meta-count"><b>{show_n}</b> / {total} 结果</div>
        <div class="result-meta-note">{html.escape(note)}</div>
    </div>
    """, unsafe_allow_html=True)

    if missing:
        st.markdown(
            f"<div class='input-hint' style='margin-top:10px'>"
            f"<span>未找到</span><span>{html.escape(' / '.join(missing))}</span></div>",
            unsafe_allow_html=True,
        )

    rows_html = []
    for i, card in cards.head(show_n).iterrows():
        rows_html.append(render_row(i + 1, card, df, density=density))
    st.markdown("".join(rows_html), unsafe_allow_html=True)

    if total > show_n:
        if st.button(f"展开剩余 {total - show_n} 条", use_container_width=True):
            extra_html = []
            for i, card in cards.iloc[show_n:].iterrows():
                extra_html.append(render_row(i + 1, card, df, density=density))
            st.markdown("".join(extra_html), unsafe_allow_html=True)


def _render_empty(query, mtime_ns, size, df):
    query_code = normalize_code(query)
    is_code = bool(query_code) and len(query_code) >= 4 and query_code == normalize_text(query)
    title = f"货号 {html.escape(query_code)} 未命中" if is_code else f"「{html.escape(query)}」未命中"

    st.markdown(f"""
    <div class="empty">
        <div class="empty-mark">— NO MATCH —</div>
        <div class="empty-title">{title}</div>
        <div class="empty-sub">下面是按相似货号、相似药名给出的推测</div>
    </div>
    """, unsafe_allow_html=True)

    suggestions, zone_hints = fallback_suggestions(mtime_ns, size, query)

    if zone_hints:
        hints_html = "".join(f"<span>{html.escape(h)}</span>" for h in zone_hints)
        st.markdown(f"""
        <div class="zone-hints">
            <div class="zone-hint-label">可能相关分区</div>
            <div class="zone-hint-list">{hints_html}</div>
        </div>
        """, unsafe_allow_html=True)

    if not suggestions.empty:
        # fallback 默认 default 密度（数量 ≤6）
        density = "spacious" if len(suggestions) <= 3 else "default"
        st.markdown(f"""
        <div class="result-meta">
            <div class="result-meta-count"><b>{len(suggestions)}</b> 条推测</div>
            <div class="result-meta-note">FALLBACK</div>
        </div>
        """, unsafe_allow_html=True)
        rows_html = []
        for i, card in suggestions.iterrows():
            rows_html.append(render_row(i + 1, card, df, density=density))
        st.markdown("".join(rows_html), unsafe_allow_html=True)


def _render_colophon(df, size):
    st.markdown(f"""
    <div class="colophon">
        <span>药品检索 · v2</span>
        <span>{len(df)} REC · {size // 1024} KB</span>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
