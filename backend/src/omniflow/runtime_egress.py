"""CLI 网络出口：隔离空间仅能通过专用 Unix socket 进行精确 HTTPS CONNECT。

不解密 TLS，不记录数据/目的地，不代理任意端口或 HTTP URL。DNS 全地址校验并固定
连接 IP，拒绝宿主/私网。此服务不绑定 TCP，CLI 无宿主网络命名空间。
"""

import select
import socket
import threading
import time
from contextlib import suppress

from .safe_http import public_address, resolve


def relay(left, right, stopping, seconds=600):
    deadline = time.monotonic() + seconds
    while not stopping.is_set() and time.monotonic() < deadline:
        ready, _, _ = select.select([left, right], [], [], 0.2)
        for source in ready:
            data = source.recv(65536)
            if not data:
                return
            (right if source is left else left).sendall(data)


class EgressBroker:
    def __init__(self, path, hosts):
        self.path, self.hosts = path, frozenset(hosts)
        self.stopping = threading.Event()
        self.slots = threading.BoundedSemaphore(16)
        self.connections = set()
        self.lock = threading.Lock()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.server.bind(str(path))
            path.chmod(0o600)
            self.server.listen(16)
            self.server.settimeout(0.2)
            self.thread = threading.Thread(target=self.serve, daemon=True)
            self.thread.start()
        except BaseException:
            self.server.close()
            raise

    def serve(self):
        while not self.stopping.is_set():
            try:
                client, _ = self.server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self.slots.acquire(blocking=False):
                client.close()
                continue
            with self.lock:
                self.connections.add(client)
            threading.Thread(target=self.handle, args=(client,), daemon=True).start()

    def handle(self, client):
        remote = None
        try:
            client.settimeout(3)
            header = b""
            deadline = time.monotonic() + 3
            while b"\r\n\r\n" not in header:
                chunk = client.recv(1)
                if not chunk or len(header) >= 4096 or time.monotonic() >= deadline:
                    raise ValueError
                header += chunk
            first = header.decode("ascii").split("\r\n")[0]
            method, authority, version = first.split(" ")
            host, port = authority.split(":")
            if (
                method != "CONNECT"
                or version != "HTTP/1.1"
                or port != "443"
                or host not in self.hosts
            ):
                raise ValueError
            addresses = resolve(host)
            if (
                self.stopping.is_set()
                or not addresses
                or not all(public_address(a) for a in addresses)
            ):
                raise ValueError
            remote = socket.create_connection((addresses[0], 443), timeout=3)
            with self.lock:
                self.connections.add(remote)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            relay(client, remote, self.stopping)
        except Exception:
            pass  # 不把 URL、TLS 数据或原始错误写日志。
        finally:
            with self.lock:
                for stream in (client, remote):
                    if stream is not None:
                        self.connections.discard(stream)
                        stream.close()
            self.slots.release()

    def close(self):
        self.stopping.set()
        self.server.close()
        self.thread.join(2)
        with self.lock:
            for stream in list(self.connections):
                with suppress(OSError):
                    stream.shutdown(socket.SHUT_RDWR)
        self.path.unlink(missing_ok=True)
