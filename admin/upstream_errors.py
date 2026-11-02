"""Keep upstream evidence separate from gateway decisions and client UI text."""
from __future__ import annotations

import json
from pathlib import Path
import re


_MESSAGES = json.loads((Path(__file__).with_name('data') / 'upstream_error_messages.json').read_text(encoding='utf-8'))

#: 需要打码的凭证形态。顺序有意义：先处理长的、带前缀的形态。
_REDACT_RULES = (
    # Authorization 头：必须**整行值**抹掉，不能只抹第一个词。
    # 反例（实测踩过）：`Authorization: Basic dXNlcjpwYXNz` 若只替换
    # `\S+`，抹掉的是 `Basic`，后面的 base64 凭据反而露出来了。
    # 这里吃掉紧跟的 scheme 词 + 其后所有非空白/引号内容。
    (re.compile(r'(?i)(authorization\s*[:=]\s*)'
                r'(?:(?:bearer|basic|digest|token)\s+)?'
                r'[^\s",}\'&]+'),
     r'\1<redacted>'),
    # 独立的 scheme + 长串（没有 Authorization 前缀时）
    (re.compile(r'(?i)\b(bearer|basic|digest)\s+[A-Za-z0-9._~+/=\-]{8,}'), r'\1 <redacted>'),
    # JSON / query / kv 三种形态的敏感字段
    (re.compile(r'(?i)\b(access_?token|refresh_?token|id_?token|auth_?token|'
                r'token|session_?id|session|cookie|set-cookie|api_?key|apikey|'
                r'secret|password|passwd|pwd|client_secret|code_verifier|'
                r'auth_?code)"?\s*[:=]\s*"?[^",}\s&]*'),
     r'\1=<redacted>'),
    # 裸 JWT（三段式，可能不在任何字段名后面）
    (re.compile(r'eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]*'), '<jwt>'),
    # 常见的长随机串前缀（sk- / wb- / ghp_ …）
    (re.compile(r'\b(sk|wb|ghp|gho|ghs|xox[baprs])[-_][A-Za-z0-9_\-]{16,}'), r'\1-<redacted>'),
)


def redact(text: str, limit: int = 0) -> str:
    """抹掉文本里可能承载凭证的片段，并可截断。

    ## 用途

    上游的错误体常常**回显请求头**（`Authorization: Bearer <token>`），
    或者带上 `access_token=...`。这些文本进日志没问题，但要回到响应体、
    或写进之后可能被别人看到的数据库字段，就必须先脱敏。

    各平台的 `UpstreamError.detail` 约定是「只进日志」，但实际有若干地方
    把它塞进了 `to_dict()` 交给前端（loomy/qoder/trae/raccoon 都有）。
    所以脱敏放在转换出口，而不是指望每个调用点都记得处理。
    """
    if not text:
        return ''
    out = str(text)
    for pattern, repl in _REDACT_RULES:
        out = pattern.sub(repl, out)
    return out[:limit] if limit else out



class UpstreamError(RuntimeError):
    def __init__(self, context: str, status: int, body: str):
        self.http_status = status
        self.raw_body = body
        self.upstream = error_info(body, status)
        super().__init__(f'{context}: HTTP {status} / {body}')


def error_info(body: str, status: int = 0) -> dict:
    raw = body or ''
    text = raw
    for prefix in ('[estimated] ', '[upstream] '):
        text = text.removeprefix(prefix)
    candidates = [text] + [line[5:].strip() for line in text.splitlines() if line.startswith('data:')]
    # Backend exceptions prefix the actual JSON response with method/path/HTTP.
    if not text.lstrip().startswith(('{', '[')) and not text.startswith(('data:', 'event:')):
        start = text.find('{')
        if start >= 0:
            candidates.append(text[start:])

    def visit(obj, depth=0):
        if not isinstance(obj, dict) or depth > 5 or 'choices' in obj:
            return None
        code = obj.get('code')
        if code is not None and not isinstance(code, bool) and str(code) not in ('', '0', '200'):
            return obj
        for key in ('error', 'data'):
            found = visit(obj.get(key), depth + 1)
            if found:
                return found
        if any(isinstance(obj.get(k), str) for k in ('msg', 'message', 'detail', 'details')):
            return obj
        return None

    obj = None
    for candidate in candidates:
        try:
            obj = visit(json.loads(candidate))
        except (ValueError, TypeError):
            continue
        if obj:
            break
    code = str(obj['code']) if obj and obj.get('code') is not None else None
    if code is None and text.lstrip().startswith('{') and '"choices"' not in text:
        # Legacy cooldown reasons were limited to 255 characters and may end
        # inside a JSON string. Recover only their explicit envelope code.
        codes = re.findall(r'"code"\s*:\s*"?(-?\d+)"?', text)
        code = next((c for c in codes if c not in ('0', '200')), None)
    message = next((obj[k] for k in ('msg', 'message', 'detail', 'details')
                    if isinstance(obj.get(k), str)), None) if obj else None
    display = obj.get('displayMsg') if obj else None
    client = _MESSAGES.get(code or str(status or http_status(raw)), {})
    return {'http_status': status or http_status(raw) or None, 'code': code,
            'message': message, 'display_message': display, 'raw': raw,
            'client_message': client.get('message'), 'client_description': client.get('description')}


def http_status(message: str) -> int:
    # Never mistake a port, retry duration or a business code for HTTP status.
    match = re.search(r'\bHTTP(?:/\d(?:\.\d)?)?\s+(\d{3})\b', message or '', re.I)
    return int(match[1]) if match else 0


def stored_info(value: str, fallback: str = '') -> dict:
    try:
        result = json.loads(value or '')
        if isinstance(result, dict):
            return result
    except (ValueError, TypeError):
        pass
    return error_info(fallback)