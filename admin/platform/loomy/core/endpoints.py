"""Loomy 端点表（以**实际运行配置**为准）。

## 关键：两个 baseURL 的区别（我踩过这个坑）

    ossptest.voicecloud.cn/loomy/integration/api/v1   测试环境（模板里写的）
    loomyad.xunfei.cn/api/v1                          真实运行环境 ★

模板 `opencode-home-template/opencode.json` 里是前者，
**实际生效的是 `<configRoot>/opencode/opencode.json`**（运行时写入）。
读模板会拿到测试地址 → 所有请求返回 `100002 登录已失效`，
看起来像「凭证过期」，其实是**地址错了**。

> 教训：配置有两个位置时，读**运行时那一份**。
> 判断方法：比大小（模板 6.9 KB vs 运行时 21.3 KB）、比 mtime。

## 鉴权（三个都不能少）

    Authorization: Bearer <session>
    token: <session>                     ← 双头，只发一个会失败
    traceparent: 00-<32hex>-<16hex>-01   ← 缺了会**挂死**，不是报错

## session 来源

`<configRoot>/userData/auth-session.json`（明文 JSON，137 字节）
其中 `configRoot = C:/Users/Public/Loomy/<sha256(username)[:12]>`
"""
from __future__ import annotations

import os
import secrets

PLATFORM = "loomy"

REGIONS = {
    "cn": {
        "label": "Loomy",
        "base_url": "https://loomyad.xunfei.cn/api/v1",
        "account_base": "https://accounttest.xfinfr.com",
    },
}

REALM_IDS = {"cn": "loomy"}

# ---- 接口 ----
MODELS_PATH = "/models"
CHAT_PATH = "/chat/completions"
#: 积分余额。**注意鉴权只要 `token` 头**（不带 Authorization）。
POINTS_BALANCE_PATH = "/points/balance"
POINTS_RECORDS_PATH = "/points/records"
#: 每日签到（领取当日赠送额度）
POINTS_FIRST_LOGIN_PATH = "/points/first-login"

# ---- 配置目录（userDataPath 是算出来的，不是 %APPDATA%/<产品名>）----
#: `config-path.js`：C:/Users/Public/Loomy/<sha256(username)[:12]>/userData
CONFIG_ROOT_PARENT = "C:/Users/Public/Loomy"
#: 凭证文件名（源码 `new Store({ name: 'auth-session' })`）
CRED_FILE = "auth-session.json"
#: 运行时 provider 配置（真实 baseURL 在这里，**不是模板**）
OPENCODE_CONFIG = "opencode/opencode.json"


#: 默认 UA：Electron 内嵌 Chromium 的形态。
#:
#: ## 为什么 loomy 也要设 UA（尽管它不校验）
#:
#: Loomy 客户端是 Electron 应用（`admin/platform/skill/CASE-LOOMY.md` 的
#: 解包结论：as.ar 拆分、`config-path.js`、`electron-store`）。官方客户端
#: 走 Electron 内嵌 Chromium，**不发自定义 UA**，所以发出去的是 Chromium
#: 的默认 UA —— 而不是 `Python-urllib/3.12`。
#:
#: 用无效 token 做对照实验（`/models`，四种 UA）：业务响应码一字不差
#: （都是 200 + code=100002）。所以 **UA 不影响鉴权**。
#: 但在真实流量里，`Python-urllib/3.12` 是最典型的脚本特征；本平台同样
#: 是批量导入账号，被整体标记的代价远大于这里改一行的成本。
#:
#: 因此发一个与 Electron 客户端一致的 UA。环境变量 `ADMIN_LOOMY_UA` 可覆盖。
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Loomy/1.0.0 Chrome/128.0.6613.186 "
              "Electron/32.0.0 Safari/537.36")


def user_agent() -> str:
    """当前应使用的 UA（`ADMIN_LOOMY_UA` 可覆盖）。"""
    value = (os.getenv("ADMIN_LOOMY_UA") or "").strip()
    return value or DEFAULT_UA


def auth_headers(token: str, version: str = "1.0.0") -> dict:
    """鉴权头。

    ⚠️ `traceparent` 缺失时上游不报错、**直接挂到超时** ——
    这类「静默挂死」最难查（源码注释里有实测记录）。
    """
    return {
        "Authorization": "Bearer " + token,
        "token": token,
        "Accept": "application/json",
        "traceparent": "00-%s-%s-01" % (secrets.token_hex(16),
                                        secrets.token_hex(8)),
        "loomy-version": version,
        "User-Agent": user_agent(),
    }


def points_headers(token: str) -> dict:
    """积分接口的头。

    ⚠️ 与 chat 链路不同：**只带 `token`**，不带 `Authorization`。
    实测带 Bearer 也能通，但官方只发这一个头，保持一致最稳。

    UA 仍要带：积分接口与 chat 走同一个网关，没有理由在这里露出
    `Python-urllib` 而 chat 不露。
    """
    return {"token": token, "Accept": "application/json",
            "User-Agent": user_agent()}


def base_of(realm: str) -> str:
    """上游 base_url。

    允许用环境变量覆盖：`ADMIN_LOOMY_BASE_URL`（或按区分
    `ADMIN_LOOMY_BASE_URL_CN`）。

    为什么要这个口子：联调/回归时需要一个**可控的上游**来复现
    「工具调用参数被截断」「finish_reason 分帧」这类故障 —— 真上游
    不会按你的节奏出错。把它指向本地 mock 就能稳定复现与验证。
    生产环境不设这个变量，行为与原来完全一致。
    """
    import os

    conf = REGIONS.get(realm or "cn") or REGIONS["cn"]
    keys = []
    if realm:
        keys.append("ADMIN_LOOMY_BASE_URL_" + str(realm).upper())
    keys.append("ADMIN_LOOMY_BASE_URL")
    for key in keys:
        value = (os.getenv(key) or "").strip()
        if value:
            return value.rstrip("/")
    return str(conf["base_url"]).rstrip("/")
