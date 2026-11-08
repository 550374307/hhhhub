"""把上游三家的 import_batch（及其辅助函数）移植进合并结果。

背景：上游 loomy/qoder/trae 的 services/accounts.py 都有 import_batch，
解决 issue #8「非 WorkBuddy 平台批量导入全部失败」。w3 漏了这三个。
"""
import pathlib
import subprocess
import sys


def upstream_lines(rel):
    out = subprocess.run(["git", "show", "upstream/main2:" + rel],
                         capture_output=True)
    if out.returncode != 0:
        raise SystemExit("git show failed: " + rel)
    return out.stdout.decode("utf-8").splitlines()


def extract_fn(lines, name):
    """取一个顶层 def 的完整实现（到下一个顶层 def/class/常量为止）。"""
    start = None
    for i, l in enumerate(lines):
        if l.startswith("def " + name + "("):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if l.startswith("def ") or l.startswith("class ") or (
                l and not l[0].isspace() and not l.startswith("#")
                and not l.startswith(")") and not l.startswith("]")):
            end = i
            break
    return [l for l in lines[start:end]]


def has_def(local_lines, name):
    return any(l.startswith("def " + name + "(") for l in local_lines)


def append_block(path, block, label):
    p = pathlib.Path(path)
    raw = p.read_bytes().decode("utf-8")
    crlf = "\r\n" if "\r\n" in raw else "\n"
    text = raw.rstrip()
    addition = ("\n\n\n# ---------------------------------------------------------------------------\n"
                "# " + label + "\n"
                "# ---------------------------------------------------------------------------\n"
                + "\n".join(block).rstrip())
    p.write_bytes((text + addition + "\n").replace("\r\n", "\n").encode("utf-8"))
    print("  appended %s -> %s" % (label, path))


# ---- loomy: session_expires_at + import_batch ----
L = "admin/platform/loomy/services/accounts.py"
up = upstream_lines(L)
cur = pathlib.Path(L).read_text(encoding="utf-8").splitlines()
for fn, label in (("session_expires_at", "session_expires_at（import_batch 依赖）"),
                  ("import_batch", "import_batch（批量导入，issue #8）")):
    if has_def(cur, fn):
        print("  skip %s (already present)" % fn)
        continue
    blk = extract_fn(up, fn)
    if blk:
        append_block(L, blk, label)
        cur = pathlib.Path(L).read_text(encoding="utf-8").splitlines()

# ---- qoder: _prefetch_profiles + import_batch ----
Q = "admin/platform/qoder/services/accounts.py"
upq = upstream_lines(Q)
curq = pathlib.Path(Q).read_text(encoding="utf-8").splitlines()
for fn, label in (("_prefetch_profiles", "_prefetch_profiles（import_batch 依赖）"),
                  ("import_batch", "import_batch（批量导入，issue #8）")):
    if has_def(curq, fn):
        print("  skip %s (already present)" % fn)
        continue
    blk = extract_fn(upq, fn)
    if blk:
        append_block(Q, blk, label)
        curq = pathlib.Path(Q).read_text(encoding="utf-8").splitlines()

# ---- trae: import_batch ----
T = "admin/platform/trae/services/accounts.py"
upt = upstream_lines(T)
curt = pathlib.Path(T).read_text(encoding="utf-8").splitlines()
if not has_def(curt, "import_batch"):
    blk = extract_fn(upt, "import_batch")
    if blk:
        append_block(T, blk, "import_batch（批量导入，issue #8）")
else:
    print("  skip import_batch (trae)")

print("done")
