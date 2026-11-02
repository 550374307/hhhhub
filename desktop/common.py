from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METADATA = json.loads((ROOT / "desktop/version.json").read_text(encoding="utf-8"))
#: 基础版本号，三段式（`1.0.2`）。构建脚本用它算 versionCode / filevers，
#: 所以**必须**保持纯数字三段。
VERSION = METADATA["version"]
#: 对外展示的版本号，可带 fork 修复后缀（`1.0.2-fix3`）。
#:
#: ## 为什么要有这一项
#:
#: 之前只把修复号写在 git tag 上（`v1.0.2-fix1`），而 `version` 一直跟随上游
#: `1.0.2` —— 结果**安装包内的文件版本、应用内显示的版本、更新比较的当前版本
#: 全是 1.0.2**，用户装了带新功能的修复版，版本号却纹丝不动，无法区分
#: 「装没装上新版」，自动更新也会认为「已是最新」。
#:
#: 现在拆成两个：
#:   * `version`  仅供构建期数值换算（versionCode / VS_VERSIONINFO 的 filevers）
#:    * `release`  真正的"我是什么版本"，UI、更新比较、tag 校验都用它
#: 缺省时回落到 `version`，兼容老配置。
RELEASE = str(METADATA.get("release") or VERSION)
REPOSITORY = METADATA["repository"]
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"


def data_dir() -> Path:
    path = Path(os.environ.get("WORKBUDDY_DESKTOP_DATA") or
                str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WorkBuddy2API"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
