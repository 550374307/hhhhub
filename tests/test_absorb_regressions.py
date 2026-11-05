# -*- coding: utf-8 -*-
"""本轮吸收改动的回归测试（纯 stdlib + 已装依赖，不需要数据库/网络）。

覆盖：
  1. 计费指纹整行删除（11128）
  2. 邀请码不再从 query 取
  3. 平台内模型收窄 upstream_of_in_platform
  4. 思考档位解析（三种客户端方言）
  5. pool 活跃度乘性回温 + 冷号限并发
  6. 协议桥三协议转换（工具调用完整性）
  7. env_guard 生产隔离判定
  8. 惰性路径序列的兼容语义

跑法（**必须在仓库根**）：

    python -I tests/test_absorb_regressions.py

注意 `tests/` 在 .gitignore 里（第 76/162 行），新增测试文件需要 `git add -f`。
"""
import json
import pathlib
import sys
import traceback
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Windows CI（GitHub Actions runner）的 stdout 是 cp1252，中文用例名会在
# print 时抛 UnicodeEncodeError 把测试判成失败 —— 这不是测试逻辑问题，
# 但会让 CI 永远红。统一把 stdout 改成 UTF-8，且 modify 失败时退回
# backslashreplace（宁可显示成 \uXXXX，也不要因为编码炸掉）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:  # noqa: BLE001 - 老版本 / 非标准流
    pass

_PASS = []
_FAIL = []


def check(name):
    """装饰器：跑一个用例并记录结果。"""
    def wrap(fn):
        try:
            fn()
        except Exception:  # noqa: BLE001 - 测试收集器必须吞掉一切
            _FAIL.append((name, traceback.format_exc()))
        else:
            _PASS.append(name)
        return fn
    return wrap


# --------------------------------------------------------------------------
# 1. 计费指纹
# --------------------------------------------------------------------------
@check("fingerprint: 整行删除（含 cch=）")
def _():
    from upstream_compat import _sanitize_non_repository_text as san

    s = "x-anthropic-billing-header: cc_version=1.2; cc_entrypoint=cli; cch=abc123;"
    out = san(s)
    assert "cch=" not in out, out
    assert "anthropic-billing" not in out.lower(), out


@check("fingerprint: 行内残留清掉但保留正文")
def _():
    from upstream_compat import _sanitize_non_repository_text as san

    out = san("cmd x-anthropic-billing-header: v=1; cch=deadbeef; tail")
    assert "cch=" not in out, out
    assert "cmd" in out and "tail" in out, out


@check("fingerprint: 结构化删除 + 空 system 消息整条摘掉")
def _():
    from upstream_compat import strip_harness_billing_header

    obj = {"messages": [
        {"role": "system", "content": "x-anthropic-billing-header: v=1; cch=aa;"},
        {"role": "user", "content": "hi"},
    ]}
    removed = strip_harness_billing_header(obj)
    assert removed == 1, removed
    assert len(obj["messages"]) == 1, obj
    assert obj["messages"][0]["role"] == "user", obj


# --------------------------------------------------------------------------
# 2. 邀请码
# --------------------------------------------------------------------------
@check("join: _extract_invite_code 不读 query_params")
def _():
    import inspect

    from admin.routers import login as login_router

    src = inspect.getsource(login_router._extract_invite_code)
    # 只看**实际代码**：docstring 里写着「不读 query_params」是在解释原因，
    # 直接全文搜会误判成"还在读"。
    assert "request.query_params" not in src, src
    assert ".query_params.get" not in src, src
    # 行为验证：query 里带邀请码必须取不到
    class Req:
        query_params = {"invite_code": "MH-FROM-QUERY"}
        headers = {}

    class Body:
        invite_code = ""

    assert login_router._extract_invite_code(Body(), Req()) == "", \
        "query 参数仍然被当作邀请码来源"
    # 头里带则必须取到
    class ReqHdr:
        query_params = {}
        headers = {"x-join-invite": "MH-FROM-HEADER"}

    assert login_router._extract_invite_code(None, ReqHdr()) == "MH-FROM-HEADER"


@check("join: 有效期上限为 7 天")
def _():
    from admin import join_invite

    assert join_invite._MAX_VALID_DAYS == 7, join_invite._MAX_VALID_DAYS


# --------------------------------------------------------------------------
# 3. 平台内模型收窄
# --------------------------------------------------------------------------
@check("models: upstream_of_in_platform 按平台收窄")
def _():
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
    except Exception:
        return  # 没装 sqlalchemy 时跳过（不应发生：这是运行依赖）
    from admin.models import Base, ModelConfig
    from admin.platform import models_router as mr

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    try:
        db.add_all([
            ModelConfig(model_id="glm-5.3-flash", platform="workbuddy", enabled=1),
            ModelConfig(model_id="GLM-5.3-Flash", platform="loomy", enabled=1),
        ])
        db.commit()
        assert mr.upstream_of_in_platform("glm-5.3-flash", "loomy", db) == "GLM-5.3-Flash"
        assert mr.upstream_of_in_platform("glm-5.3-flash", "workbuddy", db) == "glm-5.3-flash"
        assert mr.upstream_of_in_platform("glm-5.3-flash", "nope", db) == ""
    finally:
        db.close()


# --------------------------------------------------------------------------
# 4. 思考档位
# --------------------------------------------------------------------------
@check("effort: 三种客户端方言")
def _():
    from admin.platform.shared import reasoning_effort_of as f

    assert f({"reasoning_effort": "high"}) == "high"
    assert f({"reasoningEffort": "HIGH"}) == "high"
    assert f({"reasoning": {"effort": "medium"}}) == "medium"
    assert f({"thinking": {"type": "enabled", "budget_tokens": 8192}}) == "budget:8192"
    assert f({"thinking": {"type": "disabled"}}) == "off"
    assert f({"reasoning_effort": "DROP TABLE"}) == ""
    assert f(None) == ""


@check("effort: 从 scope 取 body，坏输入不抛")
def _():
    from admin.platform.shared import reasoning_effort_of_request

    class Req:
        def __init__(self, body):
            self.scope = {"_wb_body": body}

    assert reasoning_effort_of_request(Req(json.dumps({"reasoning_effort": "low"}).encode())) == "low"
    assert reasoning_effort_of_request(Req(b"not json")) == ""
    assert reasoning_effort_of_request(Req(None)) == ""

    class Empty:
        scope = {}

    assert reasoning_effort_of_request(Empty()) == ""


# --------------------------------------------------------------------------
# 5. pool 活跃度
# --------------------------------------------------------------------------
@check("pool: 活跃号权重 > 沉寂号（方向已反转）")
def _():
    from admin import pool

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    active = pool._weight(100, 0, 100, now, now)
    stale = pool._weight(100, 0, 100, now - timedelta(hours=400), now)
    assert active > stale, (active, stale)


@check("pool: activity_factor 边界")
def _():
    from admin import pool

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert pool.activity_factor(now, now) == 1.0
    assert pool.activity_factor(now - timedelta(hours=12), now) == 1.0
    lo = pool.activity_factor(now - timedelta(hours=400), now)
    assert abs(lo - pool.IDLE_MIN_FACTOR) < 1e-9, lo


@check("pool: 冷号限并发到 1，热号不干预")
def _():
    from admin import pool

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    class Acc:
        uid = "u"
        max_in_flight = None

        def __init__(self, hours):
            self.last_used_at = now - timedelta(hours=hours)
            self.created_at = None

    rt = pool.PoolRuntime()
    rt.max_in_flight = 3
    assert rt.limit_for(Acc(1)) == 3
    assert rt.limit_for(Acc(200)) == pool.COLD_WARMUP_INFLIGHT


# --------------------------------------------------------------------------
# 6. 协议桥
# --------------------------------------------------------------------------
@check("bridge: anthropic -> openai 工具三件套")
def _():
    from admin.platform import protocol_bridge as b

    body = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant",
             "content": [{"type": "tool_use", "id": "t1", "name": "calc",
                          "input": {"x": 1}}]},
            {"role": "user",
             "content": [{"type": "tool_result", "tool_use_id": "t1",
                          "content": "42"}]},
        ],
        "tools": [{"name": "calc", "input_schema": {"type": "object"}}],
    }
    out = b.anthropic_to_openai(body)
    assert any(m.get("tool_calls") for m in out["messages"]), out
    assert any(m.get("tool_call_id") for m in out["messages"]), out
    assert out.get("tools"), out
    assert any(m["role"] == "tool" for m in out["messages"]), out


@check("bridge: responses -> openai 工具")
def _():
    from admin.platform import protocol_bridge as b

    body = {
        "model": "m",
        "input": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "name": "f",
                   "parameters": {"type": "object"}}],
    }
    out = b.responses_to_openai(body)
    assert out.get("messages"), out


# --------------------------------------------------------------------------
# 7. env_guard
# --------------------------------------------------------------------------
@check("env_guard: dev 下不拦任何路径")
def _():
    from admin import env_guard

    if env_guard.IS_PROD:
        return  # 跑在 prod 环境时该断言不适用
    assert env_guard.is_prod_blocked("/api/app-source/locate") is False


@check("env_guard: 路径判定函数存在且高危前缀齐全")
def _():
    from admin import env_guard

    assert "/api/app-source" in env_guard.PROD_BLOCKED_PREFIXES
    assert "/api/maintenance/restore" in env_guard.PROD_BLOCKED_PREFIXES
    assert "/api/maintenance/restart" in env_guard.PROD_BLOCKED_PREFIXES
    assert callable(env_guard.assert_boot_policy)
    assert callable(env_guard.fs_ops_allowed)
    assert callable(env_guard.local_runtime)


# --------------------------------------------------------------------------
# 8. 惰性路径
# --------------------------------------------------------------------------
@check("paths: LazyPaths 可迭代/可 len/可下标，且解析失败退模板")
def _():
    from admin.platform.install_paths import LazyPaths

    def boom():
        raise RuntimeError("resolver down")

    p = LazyPaths(boom, ("%X%\\a.exe",))
    assert list(p) == ["%X%\\a.exe"], list(p)
    assert len(p) == 1
    assert p[0] == "%X%\\a.exe"


@check("paths: 四平台常量仍是可迭代序列")
def _():
    for pid in ("loomy", "qoder", "trae", "raccoon"):
        mod = __import__("admin.platform.%s.core.paths" % pid, fromlist=["x"])
        exe = list(mod.APP_EXE_CANDIDATES)
        data = list(mod.DATA_DIR_CANDIDATES)
        assert exe, pid
        assert data, pid
        assert len(exe) == len(set(exe)), "dup in %s: %s" % (pid, exe)


# --------------------------------------------------------------------------
# 9. raccoon 扫码授权（含 200901 保护）
# --------------------------------------------------------------------------
@check("raccoon: 200901 归 confirm，不归 auth")
def _():
    from admin.platform.raccoon.core import client as c

    assert c._classify(200901, 400, "iam_confirm_required") == "confirm"
    assert c._classify(200901, 400, "") == "confirm"
    # 关键：不能进 auth —— 那会让 sync_quota 把健康号 disabled
    assert 200901 not in c._AUTH_CODES
    assert 200901 in c._CONFIRM_CODES
    # 相邻分类不受影响
    assert c._classify(401, 401, "") == "auth"
    assert c._classify(402, 402, "") == "quota"


@check("raccoon: qr_svg 生成合法 SVG")
def _():
    from admin.platform.raccoon.services import webauth as w

    svg = w.qr_svg("https://example.com/x?code=1")
    assert svg.lstrip().startswith("<svg"), svg[:40]
    assert len(svg) > 500, len(svg)


@check("raccoon: 扫码会话可用才入库，200901 必须拒绝")
def _():
    """两条路径都要验：可用 -> 入库；200901 -> 不入库且标记 confirm_required。

    这是本轮最危险的一处：直接入库会把**能用的凭证覆盖成不能用的**，
    而用户只会看到「授权成功了却什么都用不了」。
    """
    import base64
    import time as _t

    def b64(o):
        return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")

    def jwt(sub):
        return "%s.%s.%s" % (
            b64({"alg": "HS256", "typ": "JWT"}),
            b64({"iss": "https://xiaohuanxiong.com/iam", "sub": sub,
                 "exp": int(_t.time()) + 86400}), "sig")

    class Resp:
        def __init__(self, payload):
            self._r = json.dumps(payload).encode()

        def read(self):
            return self._r

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import admin.platform.raccoon.services.accounts as raccoon_accounts
    import admin.platform.raccoon.services.webauth as w
    from admin.platform.raccoon.core import client as raccoon_client

    real_urlopen = w.urllib.request.urlopen
    real_fetch = raccoon_client.fetch_models
    real_import = raccoon_accounts.import_record

    class FakeDB:
        def commit(self):
            pass

    def scenario(mode):
        token = jwt("pu-test-" + mode)
        imported = {}

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "/auth/mp/qrcode/login" in url:
                return Resp({"code": "OK",
                             "data": {"status": "success", "access_token": token}})
            return Resp({"code": "OK",
                         "data": {"qrcode_code": "C-" + mode,
                                  "expires_in": 300, "poll_interval": 2}})

        def fake_fetch(tok, realm="cn", account_id=None):
            if mode == "confirm":
                raise raccoon_client.UpstreamError(
                    status=400, message="iam_confirm_required",
                    kind="confirm", code=200901)
            return [{"id": "m1"}]

        def fake_import(db, record):
            imported.update(record)
            return (object(), True)

        w.urllib.request.urlopen = fake_urlopen
        raccoon_client.fetch_models = fake_fetch
        raccoon_accounts.import_record = fake_import
        try:
            begin = w.begin()
            out = w.poll(FakeDB(), begin["auth_id"])
            return out, imported
        finally:
            w.urllib.request.urlopen = real_urlopen
            raccoon_client.fetch_models = real_fetch
            raccoon_accounts.import_record = real_import

    ok_out, ok_imported = scenario("ok")
    assert ok_out.get("status") == "done", ok_out
    assert ok_imported.get("token"), "可用会话应入库"

    bad_out, bad_imported = scenario("confirm")
    assert bad_out.get("status") == "failed", bad_out
    assert bad_out.get("confirm_required") is True, bad_out
    assert not bad_imported, "200901 的会话绝不能入库"


# --------------------------------------------------------------------------
# 10. codearts 平台（本轮新接入）
# --------------------------------------------------------------------------
@check("codearts: 平台已注册且六个平台全部装载")
def _():
    from admin.platform import loader, registry

    res = loader.load_platforms()
    loaded = res.get("loaded") if isinstance(res, dict) else None
    if loaded is not None:
        assert "admin.platform.codearts" in loaded, loaded
    spec = registry.get("codearts")
    assert spec is not None, "codearts 未注册"
    assert spec.storage() == "codearts"


@check("codearts: 签名器确定性 + SDK-HMAC-SHA256 形态")
def _():
    from admin.platform.codearts.core import signer

    kwargs = dict(headers={"Content-Type": "application/json"},
                  body='{"a":1}', access_key="AK", secret_key="SK",
                  now=1700000000.0)
    h1 = signer.sign("POST", "https://example.com/v1/chat", **kwargs)
    h2 = signer.sign("POST", "https://example.com/v1/chat", **kwargs)
    assert h1 == h2, "同样输入必须得到同样签名"
    auth = h1.get("Authorization", "")
    assert auth.startswith("SDK-HMAC-SHA256 "), auth[:40]
    assert "Access=AK" in auth, auth
    assert h1.get("X-Sdk-Date"), h1


@check("codearts: AccountOps 契约方法齐全")
def _():
    from admin.platform.codearts.platform_gateway import CodeArtsAccountOps

    need = {"list", "scan", "import_local", "import_record", "import_batch",
            "refresh", "health", "export", "credits", "usage", "inject"}
    have = {n for n in dir(CodeArtsAccountOps) if not n.startswith("_")}
    missing = need - have
    assert not missing, "缺方法: %s" % missing


@check("codearts: scan() 带 supported 字段（客户端没装也不误报）")
def _():
    from admin.platform.codearts.platform_gateway import CodeArtsAccountOps

    r = CodeArtsAccountOps("cn").scan()
    assert r.get("supported") is True, r
    # 本机没装客户端时必须是「扫到了 0 个」而不是「不支持」
    assert isinstance(r.get("items"), list), r


@check("shared: 分页/过滤/排序/聚合四个 helper 可用")
def _():
    from admin.platform import shared

    class Req:
        query_params = {"page": "2", "page_size": "10", "search": "x",
                        "sort": "name_asc"}

    p = shared.list_page_params(Req())
    assert p["paginated"] is True and p["page"] == 2 and p["page_size"] == 10
    assert p["search"] == "x" and p["sort"] == "name_asc"

    class NoPage:
        query_params = {}

    n = shared.list_page_params(NoPage())
    assert n["paginated"] is False, "无 page 必须保持旧的全量行为"


@check("shared: kick_background_refresh 单飞 + 限频")
def _():
    from admin.platform import shared

    assert shared.kick_background_refresh("t-empty", [], lambda s, a: None) is False
    first = shared.kick_background_refresh("t-single", [999999], lambda s, a: None)
    assert first is True, "首次应接受"
    second = shared.kick_background_refresh("t-single", [999999], lambda s, a: None)
    assert second is False, "限频内必须拒绝"


@check("paths: install_paths.save 在库不可用时降级而非抛 500")
def _():
    """数据库不可用是一种正常运行状态（首次启动、库重启），
    改一个路径不该因此变成「服务器错误」。"""
    from admin.platform import install_paths as ip

    r = ip.save("codearts", {"install_dir": r"C:\test\codearts"})
    assert isinstance(r, dict), r
    assert "install_dir" in r, r
    # 本测试环境通常没有真实 MySQL，所以 persisted 应为 False 且带 warning；
    # 若真连上了库，persisted=True 也算通过 —— 两种都必须是「不抛异常」。
    assert "persisted" in r, r
    if not r["persisted"]:
        assert r.get("warning"), "未持久化时必须给用户可见的提示"
    c = ip.clear("codearts")
    assert isinstance(c, dict) and "persisted" in c, c


@check("client_paths: 路由覆盖全部平台（含 codearts / raccoon）")
def _():
    from admin.routers import client_paths as cp

    r = cp.list_all(_admin=True)
    plats = r.get("platforms") or []
    assert "codearts" in plats, plats
    assert "raccoon" in plats, plats
    assert (r.get("keys") or []), r


# --------------------------------------------------------------------------
def _say(text):
    """打印一行。编码不支持时退化成 ASCII 安全形式，绝不让编码问题影响判定。"""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def main():
    total = len(_PASS) + len(_FAIL)
    for item in _PASS:
        _say("PASS %s" % item)
    for name, tb in _FAIL:
        _say("FAIL %s" % name)
        _say(tb)
    _say("== %d/%d passed ==" % (len(_PASS), total))
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
