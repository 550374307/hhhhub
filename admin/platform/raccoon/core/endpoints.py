"""小浣熊（Raccoon）端点表。

## 上游形态

小浣熊是**桌面客户端 + Web 网关**型服务，没有对外开放的 OAuth：

    LLM 网关   POST {llmBase}/chat/completions
    llmBase    https://xiaohuanxiong.com/api/web/llm/v2
    鉴权        Authorization: Bearer <JWT>   （HS256，iss=xiaohuanxiong.com/iam）
    目录        GET  {llmBase}/model_catalog
    积分        GET  {origin}/api/web/points/v1/balance
    权益        GET  {origin}/api/web/auth/v1/entitlement_info
    续期        POST {origin}/api/web/auth/v1/refresh   body {"refresh_token": ...}

## 实测记录（2026-11-03）

三条只读接口用真实凭证全部返回 `code: 0`：

    /model_catalog        → 9 个模型（raccoon-* / sn-*）
    /points/v1/balance    → available_points / daily_points / reward_points …
    /auth/v1/entitlement_info → office.pro_enable / active_plan …

## 为什么没有「本机扫描」

小浣熊客户端把登录态存在 Electron 的 LevelDB 里（不是明文 JSON），
且本机当前**并未安装**该客户端。凭证只能由用户从别处导入（本项目走
agent2api 导出的 `agent2api-account` JSON）。所以 `supports_local_scan=False`，
`scan()` 返回 `{"supported": False}` —— 明确说不支持，而不是假装能扫。

## 为什么「额度」口径要小心

`/points/v1/balance` 里：
    available_points        可用总额（含每日额度）
    daily_points            当日额度（**每日重置，会过期**）
    reward_points           奖励积分
    monthly_points          月度额度
所以 `balance_remain = available_points`，
`credits_expiring_soon = daily_points`（对齐 Loomy 的「当日额度」口径）。

## ⚠️ 积分接口对本批账号返回 403（实测 50/50）

    2026-11-03 全量核对：
      GET /api/web/llm/v2/model_catalog  -> 200 × 32、401 × 18
      GET /api/web/points/v1/balance     -> 403 × 50（全部）

「目录 200 而积分 403」证明 **token 有效**，积分接口有独立的访问门槛
（子账号权限 / 风控 / 灰度）。因此：
  * 凭证有效性**只由目录接口**判定；
  * 积分读不到时沿用上次快照，**不把可用的号标 disabled，也不写 0**。
把 403 当 auth 的后果是整批好号被永久禁用 —— 早期实现踩过。
"""
from __future__ import annotations

import os

PLATFORM = "raccoon"

#: 区域：小浣熊目前只有国内站
REGIONS = {
    "cn": {
        "label": "小浣熊",
        "origin": "https://xiaohuanxiong.com",
        "llm_base": "https://xiaohuanxiong.com/api/web/llm/v2",
    },
}

REALM_IDS = {"cn": "raccoon"}

# ---- 路径 ----
CHAT_PATH = "/chat/completions"
MODELS_PATH = "/model_catalog"
POINTS_BALANCE_PATH = "/api/web/points/v1/balance"
POINTS_BILLS_PATH = "/api/web/points/v1/bills"
ENTITLEMENT_PATH = "/api/web/auth/v1/entitlement_info"
AUTH_REFRESH_PATH = "/api/web/auth/v1/refresh"

#: 与服务端保持一致的 UA。上游对 UA 异常会返回风控页而非 JSON。
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def origin_of(realm: str) -> str:
    """站点 origin（env 可覆盖：`ADMIN_RACCOON_ORIGIN`）。"""
    value = (os.getenv("ADMIN_RACCOON_ORIGIN") or "").strip()
    if value:
        return value.rstrip("/")
    conf = REGIONS.get(realm or "cn") or REGIONS["cn"]
    return str(conf["origin"]).rstrip("/")


def llm_base_of(realm: str) -> str:
    """LLM 网关 base（env 可覆盖：`ADMIN_RACCOON_LLM_BASE_URL`）。

    保留这个口子是为了联调：指向本地 mock 才能稳定复现
    「流式分帧」「429 重试」这类故障，真上游不会按你的节奏出错。
    """
    import os as _os

    value = (_os.getenv("ADMIN_RACCOON_LLM_BASE_URL") or "").strip()
    if value:
        return value.rstrip("/")
    conf = REGIONS.get(realm or "cn") or REGIONS["cn"]
    return str(conf["llm_base"]).rstrip("/")


def auth_headers(token: str) -> dict:
    """LLM / 业务接口的鉴权头。"""
    return {
        "Authorization": "Bearer " + str(token or ""),
        "Accept": "application/json",
        "User-Agent": DEFAULT_UA,
        "Origin": "https://xiaohuanxiong.com",
        "Referer": "https://xiaohuanxiong.com/",
    }
