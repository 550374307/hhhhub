"""流式响应的资源清理助手。

## 为什么需要它（真实事故）

ASGI **spec 2.3** 下，客户端断开时 uvicorn 会 cancel 任务，而
starlette 0.46.2 的 `stream_response` 源码里**既没有 `aclose()` 也没有
`finally`** —— 于是同步生成器里写的 `finally` 只能等**垃圾回收**才跑到。

后果：客户端提前断开 → 号池租约不归还 → `MAX_IN_FLIGHT` 被吃满 →
该账号 `full=True` 永久退出号池，**只有重启进程才恢复**。

实测（raccoon 修复前）：3 次「断在响应开始前」即吃满 3/3，
等 5 秒 + 强制 `gc.collect()` 仍然是 3。

## 正确做法

    cleanup = make_cleanup(lambda: (shared.release(account), ...))
    return StreamingResponse(
        gen(), media_type="text/event-stream",
        background=BackgroundTask(cleanup))   # ← 断连也会执行

生成器的 `finally` 里也调一次 `cleanup()`，两条路径都覆盖，
`make_cleanup` 内部用一次性开关保证幂等（重复调用无副作用）。
"""
from __future__ import annotations

import logging
from typing import Callable

_logger = logging.getLogger(__name__)


def make_cleanup(action: Callable[[], None],
                 *, on_error: str = "流式清理失败") -> Callable[[], None]:
    """把清理动作包成**幂等**函数，供生成器 finally 与 BackgroundTask 共用。

    参数
    ----
    action    : 真正的清理逻辑（release 租约、记账、关 DB 等）。
    on_error  : 动作抛异常时写日志用的前缀。

    返回
    ----
    cleanup() 可被多次调用，只有第一次真正执行 action。
    异常被吞掉并记日志 —— 清理失败绝不能掩盖/替换掉真正的业务异常
    （例如上游 400 被清理阶段的错误覆盖，用户看到的是无关报错）。
    """
    done = {"flag": False}

    def cleanup() -> None:
        if done["flag"]:
            return
        done["flag"] = True
        try:
            action()
        except Exception:  # noqa: BLE001 - 清理失败只记日志
            _logger.exception(on_error)

    # 供测试/排障查询是否已执行
    cleanup.done = done  # type: ignore[attr-defined]
    return cleanup
