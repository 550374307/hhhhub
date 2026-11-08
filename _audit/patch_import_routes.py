"""给 loomy/qoder/trae 的 routers/accounts.py 补 ImportBatchIn + /import-batch 路由。"""
import pathlib
import re

PLATFORMS = {
    "loomy": ("loomy_accounts", "从手机版备份批量导入 Loomy 账号（issue #8）。"),
    "qoder": ("qoder_accounts", "从手机版备份批量导入 Qoder 账号（issue #8）。"),
    "trae": ("trae_accounts", "从手机版备份批量导入 Trae 账号（issue #8）。"),
}

MODEL_BLOCK = '''class ImportBatchIn(BaseModel):
    """手机版备份：JSONL（每行一条）或 JSON 数组。

    形态 `{"realm","token","uid","phone","expires_at"}`，不是 WorkBuddy 的
    `auth/account` 客户端凭证 —— 通用 `/api/accounts/batch` 会拒收，
    所以走本平台自己的导入逻辑（issue #8）。
    """
    text: str = ""


'''

for pid, (mod, doc) in PLATFORMS.items():
    path = pathlib.Path("admin/platform/%s/routers/accounts.py" % pid)
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")

    if "class ImportBatchIn" in text and '"/import-batch"' in text:
        print("  skip %s (already present)" % pid)
        continue

    # 1) 插 ImportBatchIn（放在第一个 class 之前，或 router 定义之后）
    if "class ImportBatchIn" not in text:
        m = re.search(r"^class \w+\(BaseModel\):", text, re.M)
        if m:
            text = text[:m.start()] + MODEL_BLOCK + text[m.start():]
        else:
            m2 = re.search(r"^router = APIRouter\(.*\)\n", text, re.M)
            if m2:
                text = text[:m2.end()] + "\n\n" + MODEL_BLOCK.rstrip() + "\n" + text[m2.end():]
            else:
                print("  WARN %s: 找不到插入点" % pid)
                continue

    # 2) 插路由（放在第一个 @router 之前）
    if '"/import-batch"' not in text:
        route = (
            '@router.post("/import-batch")\n'
            'def import_batch(body: ImportBatchIn, _: bool = Depends(require_admin),\n'
            '                 db: Session = Depends(get_db)):\n'
            '    """%s"""\n'
            '    return %s.import_batch(db, body.text)\n\n\n' % (doc, mod)
        )
        m = re.search(r"^@router\.", text, re.M)
        if m:
            text = text[:m.start()] + route + text[m.start():]
        else:
            print("  WARN %s: 找不到 @router 插入点" % pid)
            continue

    path.write_bytes(text.replace("\n", crlf).encode("utf-8"))
    print("  patched %s" % pid)

print("done")
