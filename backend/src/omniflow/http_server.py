"""本地 Uvicorn 启动；先通知 SSE，再进入等待连接退出的优雅关闭阶段。"""

import uvicorn

from .app import create_app


class LocalServer(uvicorn.Server):
    def handle_exit(self, sig, frame):
        self.config.app.state.stream_shutdown.set()
        super().handle_exit(sig, frame)


def serve_http(settings, port):
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=5,
    )
    LocalServer(config).run()
