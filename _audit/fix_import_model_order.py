"""修正：把 ImportBatchIn 从文件后部移到第一个 @router 之前。"""
import pathlib
import re

for pid in ("loomy", "qoder", "trae"):
    path = pathlib.Path("admin/platform/%s/routers/accounts.py" % pid)
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")

    # 取出 ImportBatchIn 类块
    m = re.search(r"^class ImportBatchIn\(BaseModel\):.*?(?=^class |^@router\.|\Z)",
                  text, re.M | re.S)
    if not m:
        print("  skip %s (no ImportBatchIn)" % pid)
        continue
    block = m.group(0).rstrip() + "\n\n\n"
    text = text[:m.start()] + text[m.end():]

    # 插到第一个 @router 之前
    m2 = re.search(r"^@router\.", text, re.M)
    if not m2:
        print("  WARN %s: no @router" % pid)
        continue
    text = text[:m2.start()] + block + text[m2.start():]

    # 清理可能的连续空行
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    path.write_bytes(text.replace("\n", crlf).encode("utf-8"))
    print("  fixed %s" % pid)

print("done")
