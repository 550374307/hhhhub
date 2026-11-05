"""Qoder 的客户端路径与 UA 前缀（平台知识，不放总网关）。

候选表不再写死盘符：`D:\\Qoder` / `C:\\Program Files` 这类固定位置在多数
机器上必然落空，而用户把客户端装到 `E:\\我的软件\\AI工具\\Qoder` 时
旧候选表一个都不匹配 —— 表现是「读不到客户端版本（UA 降级）」+
「扫描不到安装目录」，且不报错。

现在交给 `install_paths.resolve()`：环境变量覆盖 > 后台保存值 >
常见 env 基目录 > 实际存在的所有盘符 + 一到三层父文件夹 + 固定目录名 >
平台兜底模板。命中即停，带 TTL 缓存。

覆盖：`QODER_INSTALL_DIR` / `QODER_USER_DATA_DIR` / `QODER_CONFIG_ROOT` /
`QODER_APP_EXE`。
"""
from __future__ import annotations

from admin.platform import install_paths as ip

PLATFORM = "qoder"

INSTALL_NAMES = ("Qoder",)
EXE_NAMES = ("Qoder.exe",)
DATA_NAMES = ("com.qoder.app.stable", "com.qodercn.app.stable",
              ".qoder", ".qoder-cn")

#: UA 前缀用的产品名（真实完整 UA 格式只有抓包才知道，
#: 这里给「产品名/版本」这个最小可信值）
UA_PRODUCT = "Qoder"

#: 兜底模板（全部走环境变量，不含盘符）。
APP_EXE_FALLBACK = (
    r"%LOCALAPPDATA%\Programs\Qoder\Qoder.exe",
    r"%PROGRAMFILES%\Qoder\Qoder.exe",
    r"%PROGRAMFILES(X86)%\Qoder\Qoder.exe",
)
DATA_DIR_FALLBACK = (
    r"%APPDATA%\com.qoder.app.stable",
    r"%APPDATA%\com.qodercn.app.stable",
    r"~\.qoder",
    r"~\.qoder-cn",
)

CONFIG_ROOT_PARENT = r"%APPDATA%\Qoder"


def install_dirs() -> list[str]:
    return ip.resolve(PLATFORM, kind="install_dir", names=INSTALL_NAMES)


def app_exe_candidates() -> list[str]:
    return ip.resolve(PLATFORM, kind="app_exe", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES)


def data_dir_candidates(*, all_matches: bool = False) -> list[str]:
    return ip.resolve(PLATFORM, kind="user_data_dir", names=DATA_NAMES,
                      roots=(r"%APPDATA%", "%USERPROFILE%"),
                      fallback=DATA_DIR_FALLBACK, max_depth=0,
                      stop_after_first=not all_matches)


def config_root_parent():
    return ip.expand(CONFIG_ROOT_PARENT)


#: 兼容旧常量名。惰性求值，老调用点无需改动。
APP_EXE_CANDIDATES = ip.LazyPaths(app_exe_candidates, APP_EXE_FALLBACK)
DATA_DIR_CANDIDATES = ip.LazyPaths(data_dir_candidates, DATA_DIR_FALLBACK)
