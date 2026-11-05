"""Loomy 的客户端路径与 UA 前缀（平台知识，不放总网关）。

## 为什么这层要改

旧候选表的第一条是 `D:\\Loomy\\Loomy.exe`，其余是 `C:\\Program Files` 与
`%LOCALAPPDATA%` 下的固定路径。两个问题：

1. **盘符写死** —— 用户把客户端装在 C/D/E 任何一块盘都正常，写死 `D:` 意味着
   绝大多数机器上第一条必然落空，真正命中的概率取决于运气；
2. **不支持自定义父目录** —— 用户完全可以把软件装到
   `E:\\我的软件\\AI工具\\Loomy`，旧候选表里一个都不匹配，于是「读不到客户端
   版本 → UA 降级」「扫描不到安装目录」两个症状同时出现，而且不报错。

现在交给 `admin/platform/install_paths.resolve()` 动态定位：
环境变量覆盖 > 后台保存值 > 常见 env 基目录 > **实际存在的所有盘符 + 一到三层
父文件夹** + 固定目录名 > 平台兜底模板。命中即停，结果带 TTL 缓存。

## 兼容性（重要）

`APP_EXE_CANDIDATES` / `DATA_DIR_CANDIDATES` 原本是**元组常量**，被
`platform_gateway.probe()` 直接传给 `probe.enrich()`。这里保留这两个名字，
值改成**惰性求值**的序列（`_LazyPaths`）：

  * 老调用点一行不用改（仍可迭代）；
  * 每次访问都走 `install_paths` 的缓存查找，而不是编译期定死的表。

覆盖：`LOOMY_INSTALL_DIR` / `LOOMY_USER_DATA_DIR` / `LOOMY_CONFIG_ROOT` /
`LOOMY_APP_EXE`（由 `install_paths` 按平台名拼出）。
"""
from __future__ import annotations

from admin.platform import install_paths as ip

PLATFORM = "loomy"

#: 安装目录名与 exe 名（**这两个不变**，用户只能改盘符与父文件夹）。
INSTALL_NAMES = ("Loomy",)
EXE_NAMES = ("Loomy.exe",)
DATA_NAMES = ("loomy", "Loomy")
#: 判定「这是一个真的 Loomy 安装目录」的特征文件。
INSTALL_REQUIRE = ("resources/app.asar",)

#: UA 前缀用的产品名（真实完整 UA 格式只有抓包才知道，
#: 这里给「产品名/版本」这个最小可信值）
UA_PRODUCT = "Loomy"

#: 兜底模板（全部走环境变量，不含盘符）。
APP_EXE_FALLBACK = (
    r"%LOCALAPPDATA%\Programs\Loomy\Loomy.exe",
    r"%LOCALAPPDATA%\Loomy\Loomy.exe",
    r"%PROGRAMFILES%\Loomy\Loomy.exe",
)
DATA_DIR_FALLBACK = (r"%APPDATA%\loomy", r"%LOCALAPPDATA%\Loomy")

#: 配置根父目录（**环境变量**，不是写死的 `C:/Users/Public`）。
CONFIG_ROOT_PARENT = r"%PUBLIC%\Loomy"


class _LazyPaths:
    """惰性求值的候选路径序列：每次迭代都走 `install_paths` 的缓存查找。"""

    __slots__ = ("_resolver", "_fallback")

    def __init__(self, resolver, fallback):
        self._resolver = resolver
        self._fallback = tuple(fallback)

    def __iter__(self):
        try:
            found = self._resolver()
        except Exception:  # noqa: BLE001 - 定位失败就退回模板，绝不让探测报错
            found = []
        out = []
        # 命中的排前面；兜底模板始终保留（覆盖用户手工改过的安装位置）
        for item in list(found) + list(self._fallback):
            if item and item not in out:
                out.append(item)
        return iter(out)

    def __len__(self):
        return len(list(iter(self)))

    def __getitem__(self, index):
        return list(iter(self))[index]

    def __repr__(self):
        return "LazyPaths(%d)" % len(list(iter(self)))


def install_dirs() -> list[str]:
    return ip.resolve(PLATFORM, kind="install_dir", names=INSTALL_NAMES,
                      require=INSTALL_REQUIRE)


def app_exe_candidates() -> list[str]:
    return ip.resolve(PLATFORM, kind="app_exe", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES, require=INSTALL_REQUIRE)


def data_dir_candidates(*, all_matches: bool = False) -> list[str]:
    return ip.resolve(PLATFORM, kind="user_data_dir", names=DATA_NAMES,
                      roots=(r"%APPDATA%", r"%LOCALAPPDATA%"),
                      fallback=DATA_DIR_FALLBACK, max_depth=0,
                      stop_after_first=not all_matches)


def config_root_parent():
    """配置根的**父**目录：`%PUBLIC%\\Loomy`（位置随系统环境变量）。"""
    return ip.expand(CONFIG_ROOT_PARENT)


#: 兼容旧常量名。惰性求值，老调用点无需改动。
APP_EXE_CANDIDATES = _LazyPaths(app_exe_candidates, APP_EXE_FALLBACK)
DATA_DIR_CANDIDATES = _LazyPaths(data_dir_candidates, DATA_DIR_FALLBACK)
