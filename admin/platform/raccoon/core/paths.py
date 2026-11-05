"""小浣熊客户端路径（只用于「装没装」的探测）。

本平台**不依赖**客户端：凭证优先从本机 `auth.json` 扫描，扫不到就导入。
这里保留路径表，是为了「客户端参数」页能回答「你的小浣熊装在哪」，
而不是一片空白。

## 与旧版的差别

旧表是一组 `%LOCALAPPDATA%` 下的固定路径，用户装到 D/E 盘或自定义父目录
（`E:\\我的软件\\AI工具\\小浣熊`）时全部落空。现在交给
`install_paths.resolve()` 动态定位：环境变量覆盖 > 后台保存值 >
常见 env 基目录 > 实际存在的所有盘符 + 一到三层父文件夹 + 目录名 >
兜底模板。

**注意语义差别**：旧的是已经 `expandvars` 过的**绝对路径列表**，
这里保留这个语义 —— 兜底模板在迭代时才展开，调用方拿到的仍是绝对路径。

覆盖：`RACCOON_INSTALL_DIR` / `RACCOON_USER_DATA_DIR` /
`RACCOON_CONFIG_ROOT` / `RACCOON_APP_EXE`。
"""
from __future__ import annotations

import os

from admin.platform import install_paths as ip

PLATFORM = "raccoon"

#: Electron 安装目录名 / 主程序名（用户只能改盘符与父文件夹）。
INSTALL_NAMES = ("raccoon", "Raccoon", "xiaohuanxiong", "小浣熊")
EXE_NAMES = ("Raccoon.exe", "小浣熊.exe")
DATA_NAMES = ("raccoon", "xiaohuanxiong", "小浣熊")

#: UA 里的产品名（探测用）
UA_PRODUCT = "Raccoon"

#: 兜底模板：先留模板，取值时再 expandvars（保持「绝对路径」语义）。
_APP_EXE_FALLBACK = (
    r"%LOCALAPPDATA%\Programs\raccoon\Raccoon.exe",
    r"%LOCALAPPDATA%\Programs\xiaohuanxiong\小浣熊.exe",
    r"%LOCALAPPDATA%\raccoon\Raccoon.exe",
    r"%PROGRAMFILES%\Raccoon\Raccoon.exe",
    r"%PROGRAMFILES(X86)%\Raccoon\Raccoon.exe",
)
_DATA_DIR_FALLBACK = (
    r"%APPDATA%\raccoon",
    r"%APPDATA%\xiaohuanxiong",
    r"%APPDATA%\小浣熊",
    r"%LOCALAPPDATA%\raccoon\User Data",
)


def _expanded(values):
    return [p for p in (os.path.expandvars(str(v)) for v in values) if p]


def install_dirs() -> list[str]:
    return ip.resolve(PLATFORM, kind="install_dir", names=INSTALL_NAMES)


def app_exe_candidates() -> list[str]:
    return ip.resolve(PLATFORM, kind="app_exe", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES)


def data_dir_candidates(*, all_matches: bool = False) -> list[str]:
    return ip.resolve(PLATFORM, kind="user_data_dir", names=DATA_NAMES,
                      roots=(r"%APPDATA%", r"%LOCALAPPDATA%"),
                      max_depth=0, stop_after_first=not all_matches)


def _exe_resolver():
    return install_dirs() + app_exe_candidates()


class _ExpandedLazyPaths(ip.LazyPaths):
    """在 `LazyPaths` 基础上把兜底模板展开成绝对路径（保持旧语义）。"""

    def __iter__(self):
        return iter(_expanded(list(super().__iter__())))


APP_EXE_CANDIDATES = _ExpandedLazyPaths(_exe_resolver, _APP_EXE_FALLBACK)
DATA_DIR_CANDIDATES = _ExpandedLazyPaths(data_dir_candidates, _DATA_DIR_FALLBACK)
