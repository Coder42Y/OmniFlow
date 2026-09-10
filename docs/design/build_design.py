#!/usr/bin/env python3
"""生成不依赖外部资源、可直接打开的设计样册；不改前端业务源码。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build():
    template = (ROOT / 'design.template.html').read_text()
    tokens = (ROOT / 'tokens.css').read_text()
    marker = '/* DESIGN_TOKENS */'
    if template.count(marker) != 1:
        raise ValueError('token占位标记必须恰好一个')
    return template.replace(marker, '\n' + tokens + '\n')


if __name__ == '__main__':
    target = ROOT / 'design.html'
    target.write_text(build())
    print('已生成 docs/design/design.html；仅文档，不启动或修改应用。')
