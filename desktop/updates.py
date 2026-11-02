from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from packaging.version import InvalidVersion, Version

from desktop.common import METADATA, RELEASE, REPOSITORY, RELEASES_URL, VERSION, atomic_json
from desktop.runtime import download

#: 本 fork 的修复版后缀，形如 `1.0.2-fix3`。
#:
#: ## 为什么必须自己解析
#:
#: `-fixN` 不是合法 PEP 440，`Version("1.0.2-fix3")` 会抛 `InvalidVersion`，
#: 于是老实现 `newer()` 直接返回 False —— 意味着本 fork 发的所有修复版
#: **永远不可能被识别为更新**。用户点「检查更新」只会看到上游的版本，
#: 一升级就把带修复的 EXE 覆盖成上游原版，功能静默丢失。
import re as _re

_FIX_RE = _re.compile(r"^(\d+(?:\.\d+)*)-fix([1-9]\d*)$", _re.IGNORECASE)


def parse_version_tag(tag: str):
    """把 tag 解析成可比较的 `(base_tuple, fix_number)`。

    * `v1.0.2`      → ((1, 0, 2), 0)
    * `v1.0.2-fix3` → ((1, 0, 2), 3)

    返回 None 表示无法识别（调用方按「不是更新」处理）。
    """
    text = str(tag or "").lstrip("vV").strip()
    if not text:
        return None
    match = _FIX_RE.match(text)
    if match:
        base = tuple(int(x) for x in match.group(1).split("."))
        return base, int(match.group(2))
    try:
        version = Version(text)
    except InvalidVersion:
        return None
    # 预发布版（rc/beta）不参与更新比较，沿用原策略
    if version.is_prerelease:
        return None
    return tuple(version.release), 0


def newer(tag: str, current=RELEASE) -> bool:
    """`tag` 是否比 `current` 更新。

    比较顺序：先比基础版本号，基础相同时再比 `-fixN` 的序号。
    这样 `1.0.2-fix2` > `1.0.2-fix1` > `1.0.2`，而 `1.0.3` > `1.0.2-fix9`。
    """
    candidate = parse_version_tag(tag)
    if candidate is None:
        return False
    base, fix = candidate
    mine = parse_version_tag(current)
    if mine is None:
        # 当前版本号不可解析（极可能是开发态），保守认为没有更新
        return False
    my_base, my_fix = mine
    # 补齐长度，避免 (1,0) 与 (1,0,2) 直接比大小出错
    width = max(len(base), len(my_base))
    base += (0,) * (width - len(base))
    my_base += (0,) * (width - len(my_base))
    if base != my_base:
        return base > my_base
    return fix > my_fix


def release_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or
            not parsed.path.startswith(f"/{REPOSITORY}/releases/")):
        raise ValueError("更新地址必须来自本项目 GitHub Releases")
    return url


class UpdateService:
    def __init__(self, base: Path, platform: str | None = None):
        self.base = base
        self.platform = platform or ("android" if METADATA["asset"].endswith(".apk") else "windows")
        if self.platform not in ("windows", "android"):
            raise ValueError("Unsupported update platform")
        suffix = "android-arm64.apk" if self.platform == "android" else "windows-x64.exe"
        self.asset_pattern = "miraihub-{version}-" + suffix
        self.cache = base / f"releases-cache-{self.platform}.json"
        self.lock = threading.Lock()
        self.releases = []
        self.etag = ""
        self.error = ""
        self.checked = False
        try:
            cached = json.loads(self.cache.read_text(encoding="utf-8"))
            self.releases = cached.get("releases", [])
            self.etag = cached.get("etag", "")
        except (OSError, ValueError):
            pass

    def snapshot(self) -> dict:
        # ⚠️ 排序键不能用 `Version(r["tag"])`：`-fixN` 不是合法 PEP 440，
        # 遇到修复版 tag 会抛 InvalidVersion，整个快照直接崩掉。
        candidates = [r for r in self.releases if newer(r["tag"]) and self.matches_asset(r)]
        latest = max(candidates, key=lambda r: self._sort_key(r["tag"]), default=None)
        return {"version": RELEASE, "platform": self.platform, "releases": self.releases, "latest": latest,
                "available": latest is not None, "error": self.error,
                "checked": self.checked, "releases_url": RELEASES_URL}

    @staticmethod
    def _sort_key(tag: str):
        """可比较的排序键（含 -fixN）。解析不了的排最前。"""
        parsed = parse_version_tag(tag)
        if parsed is None:
            return ((-1,), -1)
        base, fix = parsed
        return (base, fix)

    def matches_asset(self, release: dict) -> bool:
        """该 release 是否带有可下载的本平台产物。

        注意这里**不再比对文件名**：资产名由构建时的 version.json 决定，
        与 tag 可能不同（修复版 tag `v1.0.2-fix1` 的产物叫
        `miraihub-1.0.2-windows-x64.exe`）。文件名匹配已在 `check()` 里
        完成并落在 `asset` 字段上，这里只需确认「确实拿到了资产 + 有校验」。
        """
        asset = release.get("asset") or {}
        if not asset.get("name"):
            return False
        return bool(release.get("installable"))

    def check(self) -> dict:
        with self.lock:
            try:
                # Paginated history is bounded to 100 releases, newest first; no startup blocking.
                headers = {"User-Agent": "MiraiHub-Desktop", "Accept": "application/vnd.github+json"}
                if self.etag:
                    headers["If-None-Match"] = self.etag
                request = urllib.request.Request(
                    f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100", headers=headers)
                with urllib.request.urlopen(request, timeout=15) as response:
                    rows = json.load(response)
                    self.etag = response.headers.get("ETag", "")
                releases = []
                for row in rows:
                    if row.get("draft") or row.get("prerelease"):
                        continue
                    tag = str(row.get("tag_name", ""))
                    assets = {a["name"]: a for a in row.get("assets", [])}
                    # ⚠️ 资产名里的版本号取自 **desktop/version.json**（构建时），
                    # 不是 tag。修复版 tag 是 `v1.0.2-fix1` 而产物仍叫
                    # `miraihub-1.0.2-windows-x64.exe`。
                    # 早期这里用 tag 拼文件名，于是修复版永远匹配不到资产、
                    # 被判成「不可安装」，更新提示直接消失 —— 而且不报错。
                    # 现在按解析出的基础版本号拼，并回退到「按模式匹配」。
                    parsed = parse_version_tag(tag)
                    base_text = ".".join(str(x) for x in parsed[0]) if parsed else tag.lstrip("vV")
                    filename = self.asset_pattern.format(version=base_text)
                    asset = assets.get(filename)
                    if asset is None:
                        # 兜底：同 tag 下唯一一个 .exe 也算（容忍命名漂移）
                        exes = [n for n in assets
                                if n.lower().endswith(".exe") and not n.lower().endswith(".sha256.exe")]
                        if len(exes) == 1:
                            filename = exes[0]
                            asset = assets[filename]
                    checksum = assets.get(filename + ".sha256") if filename else None
                    digest = (asset or {}).get("digest", "") or ""
                    releases.append({"id": row["id"], "tag": tag, "name": row.get("name") or tag,
                                     "body": (row.get("body") or "暂无更新说明")[:100000],
                                     "date": row.get("published_at", ""),
                                     "url": release_url(row["html_url"]), "asset": asset,
                                     "checksum": checksum,
                                     "installable": bool(asset and (checksum or digest.startswith("sha256:")))})
                self.releases = releases
                atomic_json(self.cache, {"etag": self.etag, "releases": releases})
                self.error = ""
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    self.error = ""
                else:
                    self.error = f"检查更新失败（HTTP {exc.code}），稍后可重试"
            except Exception as exc:
                self.error = f"检查更新失败：{exc}"
            self.checked = True
            return self.snapshot()

    def stage(self, release_id: int, progress=None) -> Path:
        # A caller passes a release ID, never a URL or executable path.
        release = next((r for r in self.releases if r["id"] == int(release_id)), None)
        if (self.platform != "windows" or not release or not newer(release["tag"])
                or not self.matches_asset(release) or not release["installable"]):
            raise ValueError("该版本没有可校验的 Windows 更新包")
        asset = release["asset"]
        expected = (asset.get("digest") or "").removeprefix("sha256:")
        if release["checksum"]:
            request = urllib.request.Request(release_url(release["checksum"]["browser_download_url"]),
                                             headers={"User-Agent": "MiraiHub-Desktop"})
            with urllib.request.urlopen(request, timeout=20) as response:
                expected = response.read(4096).decode("utf-8").strip().split()[0]
        if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
            raise ValueError("更新包 SHA256 无效")
        directory = self.base / "updates"
        directory.mkdir(exist_ok=True)
        target = directory / f"{int(release_id)}.exe"
        partial = target.with_suffix(".part")
        try:
            actual = download(release_url(asset["browser_download_url"]), partial, progress)
            if actual.lower() != expected.lower():
                raise ValueError("更新文件校验失败，已保留当前版本")
            with partial.open("rb") as file:
                if file.read(2) != b"MZ":
                    raise ValueError("更新文件不是 Windows EXE")
            partial.replace(target)
            return target
        finally:
            partial.unlink(missing_ok=True)
