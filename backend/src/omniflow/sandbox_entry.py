"""在空网络空间内运行；仅 stdlib，不导入应用/数据库。父 PID 由 bwrap 管理。

本文件由启动器只读挂载。代理监听仅该隔离空间的 127.0.0.1，不是宿主公网端口。
"""

import os
import select
import signal
import socket
import subprocess
import sys
import threading


def forward(client):
    remote = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(3)
        remote.settimeout(3)
        remote.connect("/run/egress.sock")
        while True:
            ready, _, _ = select.select([client, remote], [], [], 1)
            for source in ready:
                data = source.recv(65536)
                if not data:
                    return
                (remote if source is client else client).sendall(data)
    except OSError:
        pass
    finally:
        client.close()
        remote.close()
        slots.release()


def serve(server):
    while True:
        client, _ = server.accept()
        if not slots.acquire(blocking=False):
            client.close()
            continue
        threading.Thread(target=forward, args=(client,), daemon=True).start()


if __name__ == "__main__":
    slots = threading.BoundedSemaphore(16)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(16)
    threading.Thread(target=serve, args=(server,), daemon=True).start()
    env = dict(os.environ)
    proxy = "http://127.0.0.1:" + str(server.getsockname()[1])
    env.update(
        HTTPS_PROXY=proxy, HTTP_PROXY=proxy, https_proxy=proxy, http_proxy=proxy, NO_PROXY=""
    )
    # argv 只能来自可信启动器，不接收用户/模型命令；stdin 原样交官方协议。
    child = subprocess.Popen(sys.argv[1:], env=env)

    def stop(signum, frame):
        if child.poll() is None:
            child.send_signal(signum)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    raise SystemExit(child.wait())
