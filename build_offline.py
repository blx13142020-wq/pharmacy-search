"""重新生成离线单文件版：python3 build_offline.py
读取 data.csv + offline_template.html -> 药品检索_离线版.html
换新 data.csv 后跑一次即可。"""
import json, sys
sys.path.insert(0, ".")
import search as S
from pathlib import Path

df, _ = S.load_data(Path("data.csv"))
recs = [{"n": r["商品名"], "c": str(r["货号"]), "z": str(r["分区"]),
         "p": str(r["位置"]), "s": str(r["段号"]), "l": r["__loc_label"],
         "cab": r["__cabinet"], "py": r["__pinyin"], "cat": r["__category"]}
        for _, r in df.iterrows()]
tpl = Path("offline_template.html").read_text(encoding="utf-8")
out = tpl.replace("/*__DATA__*/", json.dumps(recs, ensure_ascii=False, separators=(",", ":")))
Path("药品检索_离线版.html").write_text(out, encoding="utf-8")
print(f"已生成 药品检索_离线版.html ({len(recs)} 条)")
