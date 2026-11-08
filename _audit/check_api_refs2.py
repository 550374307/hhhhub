"""更严格的前端→后端路由交叉验证。

上一版误报太多，因为把 query string、模板占位符、通配符都当成了路径。
这里先清洗再比对。
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

root = pathlib.Path("admin/console")

API_RE = re.compile(r'["\'`](/api/[A-Za-z0-9_\-/<>.$*]+)')

fe = set()
for f in list(root.glob("*.js")) + [root / "index.html"]:
    if not f.is_file():
        continue
    txt = f.read_text(encoding="utf-8", errors="replace")
    for m in API_RE.finditer(txt):
        raw = m.group(1)
        # 去 query string
        raw = raw.split("?")[0].rstrip("/")
        if not raw.startswith("/api/"):
            continue
        fe.add(raw)

import admin.server as s  # noqa: E402

back = set()
for r in s.app.routes:
    p = getattr(r, "path", "")
    if p.startswith("/api/"):
        back.add(p.rstrip("/"))


def norm(p):
    # 模板占位符 <x> / ${x} / {x} 统一成 X
    p = re.sub(r"<[^>]+>", "X", p)
    p = re.sub(r"\{[^}]+\}", "X", p)
    p = re.sub(r"\$\{[^}]+\}", "X", p)
    return re.sub(r"/+", "/", p)


backn = {norm(p) for p in back}
back_prefix = [norm(p).rstrip("/") for p in back]

missing = []
for p in sorted(fe):
    n = norm(p)
    if n in backn:
        continue
    # 前缀包含关系（前端拼 id）
    if any(b == n or b.startswith(n + "/") or n.startswith(b + "/")
           for b in back_prefix):
        continue
    # 前端用变量拼平台名（如 /api/<storage>/device/poll）
    if any(w in p for w in ("<", "${", "{")):
        continue
    missing.append(p)

print("  清洗后前端引用:", len(fe))
print("  后端 /api 路由:", len(back))
print("  真正的未命中:", len(missing))
for m in missing:
    print("    ", m)
