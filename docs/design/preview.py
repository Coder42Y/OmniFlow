#!/usr/bin/env python3
"""限时、只读的设计样册预览；只提供一个HTML，不暴露目录或业务接口。"""
import argparse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

DOCUMENT = Path(__file__).with_name('design.html')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def serve(self, head=False):
        if urlsplit(self.path).path not in ('/', '/design.html'):
            self.send_error(404)
            return
        content = DOCUMENT.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        if not head:
            self.wfile.write(content)

    def do_GET(self):
        self.serve()

    def do_HEAD(self):
        self.serve(True)

    def do_POST(self):
        self.send_response(405)
        self.send_header('Allow', 'GET, HEAD')
        self.send_header('Content-Length', '0')
        self.end_headers()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--seconds', type=int, default=3600)
    parser.add_argument('--info', required=True, type=Path)
    args = parser.parse_args()
    ip = ipaddress.ip_address(args.bind)
    allowed = ip.is_loopback or (ip.version == 4 and any(ip in ipaddress.ip_network(net) for net in
               ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']))
    if not allowed or not 1 <= args.seconds <= 3600:
        parser.error('只允许回环或RFC1918内网地址，预览最多1小时')
    os.umask(0o077)
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.timeout = 1
    end = time.monotonic() + args.seconds
    args.info.parent.mkdir(parents=True, exist_ok=True)
    args.info.write_text(json.dumps({'host': args.bind, 'port': server.server_port,
        'url': f'http://{args.bind}:{server.server_port}/design.html', 'pid': os.getpid(),
        'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=args.seconds)).isoformat(),
        'scope': '单个静态设计文档，无目录列表、无业务接口'}, ensure_ascii=False, indent=2))
    print(f'设计样册：http://{args.bind}:{server.server_port}/design.html；最多1小时，仅只读。', flush=True)
    try:
        while time.monotonic() < end:
            server.handle_request()
    finally:
        server.server_close()
