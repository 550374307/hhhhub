"""Build an arm64 APK with an explicitly staged backend; no local credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "android/app/build/generated/python"

#: 「这次跑的是哪个文件」——只用于给锚点日志加作用域前缀。
_SCOPE = ["html"]
#: 每次改写尝试的记录：(scope, label, 命中次数)。`0` 表示 miss。
_ANCHOR_LOG: list[tuple[str, str, int]] = []


def _log_hit(label: str, count: int) -> None:
    _ANCHOR_LOG.append((_SCOPE[0], label, count))


def check_runtime() -> None:
    # Run the staged app against Android's pinned dependency versions. Gradle
    # compilation alone cannot catch import-time FastAPI/Pydantic errors.
    environment = ROOT / 'build/android-runtime-check'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '-r', str(ROOT / 'android/requirements.txt'), '--index-url',
                    'https://pypi.tuna.tsinghua.edu.cn/simple'], check=True)
    subprocess.run([str(python), str(ROOT / 'scripts/check_runtime.py'), '--android',
                    '--source-dir', str(STAGING)], cwd=ROOT, check=True)


class MobileHtmlError(RuntimeError):
    """移动端改写没有命中**结构性命中**（导航 / 面板级锚点）。

    为什么这类必须报错而不是静默跳过：历史上这里是一条
    `html.replace('<link ... /static/desktop.css />', ...)` —— 后来控制台把样式
    改挂到 `/admin/assets/desktop.css?v=…`，这条替换就再也没命中过，而
    `str.replace` 找不到目标时**不抛异常**。结果 mobile.css 从来没被打进
    APK，设置页（只在移动端存在的那一版）整屏没有样式。构建时发现不了，
    只有装到手机上才看得见。

    ## 2026-11 改造：锚点分两档

    控制台已把内联脚本全部搬到 `admin/console/console-app.js`
    （index.html 855KB → 192KB）。原先 12 条打在「要搬走的内联 JS」上的
    锚点在新结构里**必然**全 miss；参照上游 `b1f3e79`，它们的语义挪到了
    `mobile_js()`，作用在真正的脚本文件上。在此基础上再区分：

      * **必需锚点**（`_sub_once` / `_replace_once`）：导航、面板、
        `</head>` / `</body>` 注入点。这些是移动端能不能用起来的前提，
        miss 说明模板结构真的变了，必须停下来改脚本。
      * **可选锚点**（`_sub_maybe` / `_replace_maybe`）：文案、图标、
        某个具体控件。控制台改版时这些会自然漂移，跳过不影响出包，
        只在构建日志里留一行 `[mobile] 跳过未命中：<label>`。
    """


def _replace_once(html: str, old: str, new: str, label: str) -> str:
    count = html.count(old)
    _log_hit(label, count)
    if not count:
        raise MobileHtmlError(
            f"移动端改写未命中：{label}（期望片段 {old!r}）。"
            "控制台模板改过结构后，请同步更新 scripts/build_android.py。")
    return html.replace(old, new, 1)


def _sub_once(html: str, pattern: str, repl, label: str, flags=re.S) -> str:
    result, count = re.subn(pattern, repl, html, flags=flags)
    _log_hit(label, count)
    if not count:
        raise MobileHtmlError(
            f"移动端改写未命中：{label}（期望正则 {pattern!r}）。"
            "控制台模板改过结构后，请同步更新 scripts/build_android.py。")
    return result


def _sub_maybe(text: str, pattern: str, repl, label: str, flags=re.S) -> str:
    """模板漂移时允许跳过，但必须在构建日志里留下痕迹。"""
    result, count = re.subn(pattern, repl, text, flags=flags)
    _log_hit(label, count)
    if not count:
        print(f"[mobile] 跳过未命中：{label}")
    return result


def _replace_maybe(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    _log_hit(label, count)
    if not count:
        print(f"[mobile] 跳过未命中：{label}")
        return text
    return text.replace(old, new, 1)


def mobile_html(html: str) -> str:
    # --- 移除桌面专属面板 -------------------------------------------------
    #
    # 导航已经改成「分组按钮」：`接入` 这个按钮同时承载 client / sync / alerts
    # 三个面板，而移动端只保留 sync（作为「设置」）、要砍掉 client 与 settings。
    # 所以这里不能直接删掉整个 `接入` 按钮（那会把设置入口一起删掉，手机上将
    # 完全没有设置页），而是先把它**改写成独立的「设置」项**，再交给后面的
    # 「移除 client 导航项」逻辑处理真正的桌面专属项。
    has_grouped_nav = 'data-group-tabs="client,sync' in html
    if has_grouped_nav:
        html = _sub_once(
            html, r'<button\b[^>]*data-tab="client"[^>]*data-group-tabs="[^"]*"[^>]*>',
            '<button class="side-item" data-tab="sync">',
            '「接入」分组 → 移动端「设置」项')
        # 文案：把按钮上的「接入」换成「设置」，与移动端设置页一致。
        # 可选：按钮文案改过一轮，miss 只是文案不同，不影响导航可用性。
        html = _sub_maybe(
            html, r'(data-tab="sync">.*?)</button>',
            lambda m: re.sub(r'(</svg>)\s*接入\s*$', r'\1 设置', m.group(1)) + '</button>',
            '「接入」文案 → 「设置」')
    else:
        html = _sub_once(html, r'<button\b[^>]*data-tab="client".*?</button>',
                         '', '移除「客户端参数」导航项')
        html = _replace_once(html, '</svg> 同步设置</button>', '</svg> 设置</button>',
                             '同步设置 → 设置（导航文案）')
    html = _sub_once(html, r'<section id="panel-client".*?</section>',
                     '', '移除「客户端参数」面板')
    html = _sub_once(html, r'<section id="panel-settings".*?</section>',
                     '', '移除「管理员设置」面板')
    settings = (ROOT / "android/web/settings.html").read_text(encoding="utf-8")
    html = _sub_once(html, r'<section id="panel-sync".*?</section>',
                     lambda _: settings, '用移动端设置页替换「同步设置」')

    # --- 桌面专属脚本已外置到 console-app.js，改写见 mobile_js() ----------
    #
    # 2026-11-04 控制台拆分后，原先这里的 12 条锚点（线上同步设置 /
    # injectAcc / 扫描本机账号 / 客户端兼容参数 / TABS / 面板加载）全部
    # 打在一个已经搬走的 `<script>` 上，一条都命中不了。它们的语义
    # （删掉桌面专属逻辑）挪到了 `mobile_js()`，作用在真正的
    # `admin/console/console-app.js` 上。
    html = _sub_once(html, r'<button\b[^>]*data-tab="settings".*?</button>',
                     '', '移除「设置」导航项（由移动端设置页接管）')

    # 「扫描本机」按钮：移动端 mobile_js() 删掉了整个 scanLocal 实现，
    # 按钮继续用 onclick="scanLocal(...)" 会 ReferenceError。
    #
    # 改派给 WBPlatform.action('scan')：platformScanLocal 的 workbuddy
    # 分支有 `typeof scanLocal === "function"` 守卫，移动端会得到
    # 「移动端不支持扫描本机」的提示，而不是脚本报错。
    # 上游这行是 `scanLocal(this)`，更早是 `scanLocal()`；两种都试。
    for old in ('onclick="scanLocal(this)"', 'onclick="scanLocal()"'):
        if old in html:
            _log_hit('「扫描本机」按钮 → 平台动作入口', 1)
            html = html.replace(old, "onclick=\"WBPlatform.action('scan')\"")
            break
    else:
        _log_hit('「扫描本机」按钮 → 平台动作入口', 0)
        print("[mobile] 跳过未命中：「扫描本机」按钮 → 平台动作入口")

    # --- 文案 -------------------------------------------------------------
    # 模板可能改版，未命中只记录不中断。
    html = _replace_maybe(html, '登录新账号，或从本机与备份中恢复已有账号。',
                          '手机号登录或浏览器授权，账号保存在这台手机。', '账号页文案')

    # --- 挂上移动端样式与脚本 ---------------------------------------------
    #
    # 用 `</head>` 做锚点而不是匹配某条 `<link>`：后者会随控制台改版漂移
    # （这次就是 `/static/desktop.css` → `/admin/assets/desktop.css?v=…`）。
    # mobile.css 必须排在全部桌面样式之后，才能覆盖 `.mobile-mode` 下的规则。
    html = _replace_once(
        html, '</head>',
        '<link rel="stylesheet" href="/static/mobile.css" />\n</head>',
        '注入 mobile.css')
    html = _replace_once(
        html, '</body>',
        '<script src="/static/mobile.js"></script>'
        '<script src="/static/mobile_settings.js"></script>\n</body>',
        '注入 mobile.js / mobile_settings.js')
    return html


def mobile_js(js: str) -> str:
    """控制台脚本已外置：桌面专属逻辑在这里移除（原来误写在 index.html 上）。

    2026-11-04：控制台把内联脚本全部搬到 admin/console/console-app.js，
    旧改写规则一条都命中不了，APK 打包直接失败。这里按同样的语义作用在
    真正的脚本文件上；未命中的规则只记录，不阻断出包。

    这些规则都是「删掉某个桌面专属实现或补一条移动端提示」——删不掉只是
    包里多带几十 KB 代码，功能上不会崩，所以全部走 `_sub_maybe`。
    """
    js = _sub_maybe(js, r'/\* ==================== 线上同步设置.*?'
                        r'(?=/\* ==================== 客户端兼容参数)',
                    'function loadSyncConfig() {}\n\n', '替换「线上同步设置」脚本')
    js = _sub_maybe(js, r'^.*\[`injectAcc\(.*\n', '', '移除「注入本机」菜单项', flags=re.M)
    js = _sub_maybe(js, r'async function injectAcc\(.*?(?=/\* 猫猫旅行)',
                    '', '移除 injectAcc 实现')
    js = _sub_maybe(js, r'/\* 扫描本机账号.*?(?=async function refreshAll\()',
                    '', '移除扫描本机账号实现')
    js = _sub_maybe(js, r'/\* ==================== 客户端兼容参数.*?'
                        r'(?=/\* ==================== 设置)',
                    '', '移除客户端参数脚本')
    # TABS 去掉 client / settings：
    # **必须锚定在 `const TABS = [...]` 里面**。上游用的是裸 `,"client"`，
    # 那会连带命中别的数组（本仓 `keyOrder` 等），删掉的是数据而不是页签。
    js = _sub_maybe(js, r'(const TABS = \[[^\]]*?),"client"', r'\1',
                    'TABS 去掉 client')
    js = _sub_maybe(js, r'(const TABS = \[[^\]]*?),"settings"', r'\1',
                    'TABS 去掉 settings')
    js = _sub_maybe(js, r'^.*if \(name === "client"\).*\n', '',
                    '去掉 client 面板加载', flags=re.M)
    js = _sub_maybe(js, r'^.*if \(name === "settings"\).*\n', '',
                    '去掉 settings 面板加载', flags=re.M)
    # 凭证提示：`openUpload()` / reauth 弹窗都搬进 console-app.js 了，
    # 所以这条移动端提示要打在脚本上（上游留在 index.html 上，静默失效）。
    #
    # 语义与内联版一致：**在控件前面插一段说明**，告诉用户 Windows 新版加密
    # 凭证在这台手机上用不了，得先在电脑上导出明文备份。
    js = _replace_maybe(js, '新版加密、旧版明文及 .info.bak-时间戳备份',
                        '旧版明文及 .info.bak-时间戳备份', '导入说明文案')
    credential_note = ('<p class="text-xs text-slate-500 mb-2">新版 Windows 加密凭证请先在'
                       '电脑账号管理页导出“系统明文备份”后再导入，或使用手机号授权登录。</p>')
    for marker in ('<input id="upFile"', '<textarea id="reauthJson"'):
        js = _replace_maybe(js, marker, credential_note + marker,
                            f'凭证提示（{marker}）')
    return js


def _run_pipeline(record: bool = True) -> tuple[dict[str, str], list[str]]:
    """在内存里跑一遍改写，返回 {文件名: 改写后文本} 与出错信息列表。

    只读源文件，不写 staging、不建 venv、不碰 gradle。
    """
    if record:
        _ANCHOR_LOG.clear()
    failures: list[str] = []
    outputs: dict[str, str] = {}
    for key, rel, fn in (
        ("html", "admin/console/index.html", mobile_html),
        ("js", "admin/console/console-app.js", mobile_js),
    ):
        path = ROOT / rel
        if not path.is_file():
            failures.append(f"源文件不存在：{rel}")
            continue
        _SCOPE[0] = key
        try:
            outputs[rel] = fn(path.read_text(encoding="utf-8"))
        except MobileHtmlError as exc:
            failures.append(str(exc))
    _SCOPE[0] = "html"
    return outputs, failures


def _node_syntax_ok(source: str) -> tuple[bool, str]:
    """用 node 校验改写后的脚本仍是合法 JS（不可用则返回 True + 说明）。"""
    node = shutil.which("node")
    if not node:
        return True, "未找到 node，跳过 JS 语法校验"
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "console-app.mobile.js"
        target.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [node, "-e",
             "new Function(require('fs').readFileSync(process.argv[1],'utf8'))",
             str(target)],
            capture_output=True, text=True)
    if proc.returncode == 0:
        return True, "node 语法校验通过"
    return False, (proc.stderr or proc.stdout).strip().splitlines()[-1] if (
        proc.stderr or proc.stdout).strip() else "node 语法校验失败"


def check_anchors() -> int:
    """Dry-run：跑一遍内存改写，报告每条锚点命中情况并做后置断言。

    为什么需要它：所有可选规则都降级成 `_sub_maybe` 之后，「**全部** miss」
    也只会刷一屏日志而构建照常成功 —— 那正是历史上「APK 里少了 mobile.css」
    的翻版。这个 dry-run 把「锚点是否还活着」变成一条可 CI 化的检查。
    """
    outputs, failures = _run_pipeline()

    for scope, label, count in _ANCHOR_LOG:
        mark = f"HIT  x{count}" if count else "MISS     "
        required = _required_label(label)
        tag = "必需" if required else "可选"
        print(f"[anchors] {mark}  [{scope}/{tag}] {label}")

    optional_miss = [label for _, label, count in _ANCHOR_LOG
                     if not count and not _required_label(label)]

    if failures:
        print("\n[anchors] 必需锚点未命中 —— APK 会缺少移动端结构，拒绝出包：",
              file=sys.stderr)
        for message in failures:
            print(f"  - {message}", file=sys.stderr)
        return 1

    # ---- 后置断言：改写结果里必须真的有移动端资源、且桌面面板确实没了 ----
    html = outputs.get("admin/console/index.html", "")
    js = outputs.get("admin/console/console-app.js", "")
    problems: list[str] = []
    for needle, why in (
        ('href="/static/mobile.css"', "mobile.css 未注入"),
        ('src="/static/mobile.js"', "mobile.js 未注入"),
        ('src="/static/mobile_settings.js"', "mobile_settings.js 未注入"),
    ):
        if needle not in html:
            problems.append(why)
    for bad, why in (
        ('id="panel-client"', "「客户端参数」面板没被移除"),
        ('id="panel-settings"', "「管理员设置」面板没被移除"),
        ('data-tab="settings"', "「设置」导航项没被移除"),
        ('onclick="scanLocal(', "「扫描本机」按钮仍指向被删函数"),
    ):
        if bad in html:
            problems.append(why)
    if "mobile_settings" not in html:
        problems.append("移动端设置页没写进去")
    # 脚本侧：被删的实现不能再出现；被保留的核心函数必须还在
    for bad in ('async function injectAcc(', '/* 扫描本机账号'):
        if bad in js:
            problems.append(f"桌面专属实现仍在脚本里：{bad}")
    for keep in ('function renderOverview(', 'function ic(', 'function openProblemAccounts(',
                 'function platformImportPaste(', 'function showAllOfficialModels(',
                 'function geRealmDupCount('):
        if keep not in js:
            problems.append(f"核心函数被误删：{keep}")
    # 语法：改写完必须仍是合法 JS
    ok, detail = _node_syntax_ok(js)
    print(f"[anchors] JS 改写后语法：{detail}")
    if not ok:
        problems.append("改写后的 console-app.js 语法不合法")

    if problems:
        print("\n[anchors] 后置断言失败：", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1

    if optional_miss:
        print(f"\n[anchors] 可选锚点未命中 {len(optional_miss)} 条（只警告）："
              + "、".join(optional_miss))
    print("\n[anchors] 必需锚点全部命中，后置断言通过 —— OK")
    return 0


def _required_label(label: str) -> bool:
    """必需锚点清单：与 mobile_html 里的 `_sub_once` / `_replace_once` 一一对应。

    只用于**报告分类**（必需 vs 可选）；真实的中断行为由调用点决定 ——
    必需项 miss 时 `_sub_once` 会抛 MobileHtmlError，dry-run 直接失败。
    """
    return label in {
        '「接入」分组 → 移动端「设置」项',
        '移除「客户端参数」导航项',
        '同步设置 → 设置（导航文案）',
        '移除「客户端参数」面板',
        '移除「管理员设置」面板',
        '用移动端设置页替换「同步设置」',
        '移除「设置」导航项（由移动端设置页接管）',
        '注入 mobile.css',
        '注入 mobile.js / mobile_settings.js',
    }


def prepare() -> None:
    # Do not allow a symlink or changed working directory to redirect recursive cleanup.
    staging = STAGING.resolve()
    allowed = (ROOT / "android/app/build").resolve()
    if not staging.is_relative_to(allowed) or staging != allowed / "generated/python":
        raise RuntimeError("Unexpected Android staging path")
    if staging.exists():
        # 构建机的安全删除钩子会拦截批量 rmtree；移开旧暂存目录即可，
        # 不删任何文件，也不影响新目录创建。
        staging.rename(staging.parent / ("python_prev_" + secrets.token_hex(4)))
    staging.mkdir(parents=True)
    # ⚠️ 只拷 `*.py` 是不够的：平台目录里还有**运行时数据文件**。
    # 实测漏了 `admin/platform/trae/data/{catalog_cn,catalog_intl}.json`
    # （约 20 KB）—— 后果不是崩溃，而是**静默退化**：
    #   catalog.reload() 返回 0 个别名（正常 115 个），
    #   entry('deepseek-v4-flash') 直接 None，
    #   于是 Trae 的模型名解析全丢，大小写不符的请求一律 4023。
    # 而且构建和 check_runtime 都不报错（加载器用 except Exception 兜住了），
    # 属于「源码能跑、只有 APK 坏」的典型。
    #
    # 下面的 rglob("data") 循环已经把 `admin/platform/*/data` 一并覆盖，
    # 所以后面不再重复 copytree（重复会撞 FileExistsError）。
    for path in (ROOT / "admin").rglob("*.py"):
        relative = path.relative_to(ROOT)
        if path.name in ("app_source.py",):
            continue
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    # 非 .py 的运行时资源（JSON 数据、模板等）：按平台目录整体补齐。
    # 只挑 `data/` 子树，避免把 __pycache__ / 测试产物带进 APK。
    # ⚠️ 跳过顶层的 `admin/data` —— 它下面会由 copytree 单独处理，
    # 这里再拷一次会让那次 copytree 撞 FileExistsError（实测踩过）。
    top_level_data = (ROOT / "admin" / "data").resolve()
    copied_data = []
    for data_dir in (ROOT / "admin").rglob("data"):
        if not data_dir.is_dir() or "__pycache__" in data_dir.parts:
            continue
        if data_dir.resolve() == top_level_data:
            continue
        for path in data_dir.rglob("*"):
            if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied_data.append(relative.as_posix())
    if copied_data:
        print(f"Bundled {len(copied_data)} runtime data file(s) into APK staging")
    for name in ("upstream_compat.py", "anthropic_adapter.py", "responses_adapter.py",
                 "responses_projection.py", "desensitize.py", "repository_instructions.py", "wb_install.py", "converter.py", "model_metadata.py"):
        shutil.copy2(ROOT / name, staging / name)
    (staging / "desktop/assets").mkdir(parents=True)
    for name in ("__init__.py", "common.py", "runtime.py", "updates.py"):
        shutil.copy2(ROOT / "desktop" / name, staging / "desktop" / name)
    shutil.copy2(ROOT / "desktop/assets/app.svg", staging / "desktop/assets/app.svg")
    metadata = json.loads((ROOT / "desktop/version.json").read_text(encoding="utf-8"))
    metadata["asset"] = "miraihub-{version}-android-arm64.apk"
    (staging / "desktop/version.json").write_text(json.dumps(metadata), encoding="utf-8")
    shutil.copytree(ROOT / "admin/static", staging / "admin/static")
    shutil.copytree(ROOT / "admin/console", staging / "admin/console")
    shutil.copytree(ROOT / "admin/data", staging / "admin/data")
    # 先改脚本、再改页面：两处都作用在 staging 副本上，互不干扰。
    app_js = staging / "admin/console/console-app.js"
    if app_js.is_file():
        _SCOPE[0] = "js"
        app_js.write_text(mobile_js(app_js.read_text(encoding="utf-8")), encoding="utf-8")
        print("Mobile console script rewritten (desktop-only sections removed)")
    else:
        # 内联版控制台（无 console-app.js）不该再出现，但别让出包崩掉。
        print("[mobile] 未找到 admin/console/console-app.js —— 跳过脚本改写")
    _SCOPE[0] = "html"
    index = staging / "admin/console/index.html"
    index.write_text(mobile_html(index.read_text(encoding="utf-8")), encoding="utf-8")
    for name in ("mobile.js", "mobile.css", "mobile_settings.js"):
        shutil.copy2(ROOT / "android/web" / name, staging / "admin/static" / name)
    for name in ("android_runtime.py", "mobile_http.py"):
        shutil.copy2(ROOT / "android/python" / name, staging / name)
    shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")
    print("Prepared mobile backend and UI (no desktop parameter page or scan tools)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--check-anchors", action="store_true",
                        help="Dry-run: 静态校验移动端改写锚点是否命中后退出（不建 venv / 不跑 gradle）")
    parser.add_argument("--with-debug", action="store_true", help="Also build the isolated emulator debug APK")
    parser.add_argument("--gradle", help="Gradle 8.11.1 executable")
    parser.add_argument("--jdk", help="JDK 17 directory")
    parser.add_argument("--sdk", help="Android SDK directory")
    args = parser.parse_args()
    if args.check_anchors:
        raise SystemExit(check_anchors())
    prepare()
    if args.prepare_only:
        return
    check_runtime()
    env = dict(os.environ, WB_ANDROID_BUILD_PYTHON=sys.executable)
    if args.jdk:
        env["JAVA_HOME"] = str(Path(args.jdk).resolve())
    if args.sdk:
        env["ANDROID_HOME"] = str(Path(args.sdk).resolve())
    if env.get("JAVA_HOME"):
        env["PATH"] = str(Path(env["JAVA_HOME"]) / "bin") + os.pathsep + env.get("PATH", "")
    gradle = args.gradle or shutil.which("gradle")
    if not gradle:
        raise SystemExit("Set --gradle to Gradle 8.11.1 and provide JDK 17 / Android SDK")
    # Keep one private signing identity for all upgrades. Never upload these files.
    #
    # 位置为什么在仓库根、而不是 `build/`：`build_desktop.py` 每次打包都会
    # 清 `build/`，签名身份放在里面等于每发一版就换一把新钥匙 ——
    # APK 覆盖升级会直接 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`，用户只能卸载。
    # 老位置保留一次性迁移，升级过的人不会因为换了路径又丢一次身份。
    signing = Path(os.environ.get("WB_ANDROID_SIGNING_DIR") or (ROOT / "android-signing"))
    legacy_signing = ROOT / "build/android-signing"
    if not signing.exists() and legacy_signing.is_dir():
        shutil.copytree(legacy_signing, signing)
        print(f"Migrated Android signing identity: {legacy_signing} -> {signing}")
    signing.mkdir(parents=True, exist_ok=True)
    credentials = signing / "signing.json"
    keystore = signing / "release.jks"
    if credentials.is_file():
        config = json.loads(credentials.read_text(encoding="utf-8"))
    else:
        config = {"password": secrets.token_urlsafe(40)}
        credentials.write_text(json.dumps(config), encoding="utf-8")
    env.update(WB_ANDROID_KEYSTORE=str(keystore), WB_ANDROID_STORE_PASSWORD=config["password"],
               WB_ANDROID_KEY_PASSWORD=config["password"])
    if not keystore.is_file():
        keytool = str(Path(env["JAVA_HOME"]) / "bin/keytool") if env.get("JAVA_HOME") else "keytool"
        subprocess.run([keytool, "-genkeypair", "-keystore", str(keystore), "-storetype", "JKS",
                        "-storepass:env", "WB_ANDROID_STORE_PASSWORD", "-keypass:env", "WB_ANDROID_KEY_PASSWORD",
                        "-alias", "workbuddy2api", "-keyalg", "RSA", "-keysize", "3072",
                        "-validity", "10000", "-dname", "CN=MiraiHub Android"], env=env, check=True)
    tasks = ["assembleRelease", "lintRelease"]
    if args.with_debug:
        tasks.append("assembleDebug")
    subprocess.run([gradle, "--no-daemon", *tasks], cwd=ROOT / "android", env=env, check=True)
    meta = json.loads((ROOT / "desktop/version.json").read_text(encoding="utf-8"))
    # 产物名用 release（可带 -fixN），与桌面端保持同一口径
    release = str(meta.get("release") or meta["version"])
    output = ROOT / "dist/android"
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"miraihub-{release}-android-arm64.apk"
    shutil.copy2(ROOT / "android/app/build/outputs/apk/release/app-release.apk", target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".apk.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(f"Built {target.name} ({target.stat().st_size / 1048576:.1f} MB)")


if __name__ == "__main__":
    main()
