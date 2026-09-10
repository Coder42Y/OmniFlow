"""精确 HTTPS 目的地、有界响应、禁止重定向/代理、DNS 地址固定的传输。

exchange/resolver 可注入；本阶段测试不联网。真实 TLS 交换器不默认挂接到适配器。
"""

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class HTTPRequest:
    method: str
    url: str
    address: str
    headers: dict
    body: bytes | None
    timeout: float


@dataclass
class HTTPResponse:
    status: int
    headers: dict
    chunks: object
    close: object = lambda: None


def resolve(host):
    return tuple({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def validate_url(url, hosts):
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        raise ValueError("结果地址无效")
    if any(ord(c) < 33 or ord(c) > 126 for c in url) or "\\" in url:
        raise ValueError("结果地址无效")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
        or parsed.netloc not in (parsed.hostname, f"{parsed.hostname}:443")
    ):
        raise ValueError("结果地址不在许可范围")
    return parsed


class HTTPRateLimited(Exception):
    """只携带等待提示；不保留 URL、授权头和响应正文。"""

    def __init__(self, retry_after=None):
        super().__init__("提供方限流")
        self.retry_after = retry_after


def _response_headers(status, read_headers):
    """保留已知的 429；头部有歧义时拒绝使用等待提示，而不是丢弃暂停事实。"""
    try:
        headers = {}
        for key, value in read_headers():
            key = key.lower()
            if key in headers and key in (
                "content-length",
                "content-type",
                "content-encoding",
                "location",
                "retry-after",
            ):
                raise ValueError("响应头不明确")
            headers[key] = value
    except Exception:
        if status == 429:
            raise HTTPRateLimited() from None
        raise
    if status == 429:
        raise HTTPRateLimited(headers.get("retry-after"))
    return headers


def _close_resources(*resources, preserve_error=False):
    """全部尝试关闭；已有主异常时仅压住次要关闭错误，否则抛出首个关闭错误。"""
    first_error = None
    for resource in resources:
        if resource is None:
            continue
        try:
            resource.close()
        except BaseException as exc:
            # 包括取消/退出，也先尝试关闭其余资源；不能因第一个 close 失败泄漏连接。
            if first_error is None:
                first_error = exc
    if first_error is not None and not preserve_error:
        raise first_error


def public_address(value):
    address = ipaddress.ip_address(value)
    return not (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or (
            address.version == 6
            and (
                address in ipaddress.ip_network("64:ff9b::/96")
                or address.sixtofour is not None
                or address.teredo is not None
            )
        )
    )


class SafeHTTP:
    def __init__(self, *, exchange, resolver=resolve):
        self.exchange, self.resolver = exchange, resolver

    def request(self, method, url, *, hosts, headers=None, body=None, max_bytes, timeout=60):
        parsed = validate_url(url, hosts)
        addresses = self.resolver(parsed.hostname)
        if not addresses or not all(public_address(value) for value in addresses):
            raise ValueError("目标地址不是公共地址")
        # exchange 必须连接 address 并使用原 hostname 校验证书，不再自行解析 DNS。
        response = self.exchange(
            HTTPRequest(method, url, addresses[0], headers or {}, body, timeout)
        )
        try:
            normalized = _response_headers(response.status, lambda: response.headers.items())
            if 300 <= response.status < 400:
                raise ValueError("拒绝供应商重定向")
            if normalized.get("content-encoding", "identity") != "identity":
                raise ValueError("拒绝压缩响应以避免容量绕过")
            size = normalized.get("content-length")
            if size is not None and (not str(size).isdigit() or int(size) > max_bytes):
                raise ValueError("响应容量超过限制")
            data = bytearray()
            deadline = time.monotonic() + timeout
            for chunk in response.chunks:
                if not isinstance(chunk, bytes) or time.monotonic() >= deadline:
                    raise ValueError("响应无效或超时")
                if len(data) + len(chunk) > max_bytes:
                    raise ValueError("响应容量超过限制")
                data.extend(chunk)
            if size is not None and len(data) != int(size):
                raise ValueError("响应不完整")
        except BaseException:
            _close_resources(response, preserve_error=True)
            raise
        else:
            _close_resources(response)
            return response.status, normalized, bytes(data)


class PinnedHTTPSExchange:
    """无隐式重试、无代理、无授权继承；仅使用调用方显式给定的请求头。"""

    def __call__(self, request):
        parsed = urlsplit(request.url)
        deadline = time.monotonic() + request.timeout
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=min(request.timeout, 10))
        response = None
        try:
            raw = socket.create_connection((request.address, 443), timeout=min(request.timeout, 10))
            try:
                connection.sock = ssl.create_default_context().wrap_socket(
                    raw, server_hostname=parsed.hostname
                )
            except BaseException:
                _close_resources(raw, preserve_error=True)
                raise
            connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            connection.request(request.method, target, body=request.body, headers=request.headers)
            response = connection.getresponse()
            headers = _response_headers(response.status, response.getheaders)

            def chunks():
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    if connection.sock is not None:
                        connection.sock.settimeout(remaining)
                    chunk = response.read1(65536)
                    if not chunk:
                        return
                    yield chunk

            def close():
                _close_resources(response, connection)

            return HTTPResponse(response.status, headers, chunks(), close)
        except BaseException:
            _close_resources(response, connection, preserve_error=True)
            raise
