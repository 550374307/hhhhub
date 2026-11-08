import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

root = pathlib.Path("admin/console")

API_RE = re.compile(r'["\'`](/api/[A-Za-z0-9_\-/]+)')
TPL_RE = re.compile(r'["\'`](/api/[^"\'`$\s]*)')

fe = set()
for f in list(root.glob("*.js")) + [root / "index.html"]:
    if not f.is_file():
        continue
    txt = f.read_text(encoding="utf-8", errors="replace")
    for m in API_RE.finditer(txt):
        fe.add(m.group(1))
    for m in TPL_RE.finditer(txt):
        val = m.group(1)
        if val.startswith("/api/"):
            fe.add(val)

print("  前端引用的 api 路径数:", len(fe))

import admin.server as s  # noqa: E402

back = set()
for r in s.app.routes:
    p = getattr(r, "path", "")
    if p.startswith("/api/"):
        back.add(p)


def norm(p):
    return re.sub(r"\{[^}]+\}", "X", p.rstrip("/"))


backn = {norm(p) for p in back}
missing = []
for p in sorted(fe):
    n = norm(p)
    if n in backn:
        continue
    if any(bn.startswith(n.rstrip("/") + "/") or n.startswith(bn.rstrip("/") + "/")
           for bn in backn):
        continue
    missing.append(p)

print("  未命中后端路由的:", len(missing))
for m in missing[:15]:
    print("    ", m)
