#!/usr/bin/env python3
"""规范静态验证；不代替实际浏览器与视觉模型审查。"""
from pathlib import Path
from html.parser import HTMLParser
import re
import unittest
from build_design import build

ROOT = Path(__file__).resolve().parent


def luminance(color):
    values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in values]
    return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.external = []
        self.refs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        for key in ('src', 'href'):
            value = attrs.get(key, '')
            if value.startswith(('http:', 'https:', '//')):
                self.external.append(value)
            if value.startswith('#'):
                self.refs.append(value[1:])


class DesignTests(unittest.TestCase):
    def test_generated_file_matches_tokens_and_template(self):
        self.assertEqual((ROOT / 'design.html').read_text(), build())

    def test_self_contained_and_local_references_valid(self):
        document = Document()
        document.feed(build())
        self.assertEqual(len(document.ids), len(set(document.ids)))
        self.assertFalse(document.external)
        self.assertTrue(set(document.refs) <= set(document.ids))
        self.assertNotIn('fetch(', build())

    def test_text_and_control_contrast(self):
        tokens = dict(re.findall(r'(--of-[\w-]+):\s*(#[0-9a-f]{6});', (ROOT / 'tokens.css').read_text()))
        for fg, bg, target in [('--of-primary', None, 4.5), ('--of-muted', '--of-bg', 4.5),
                               ('--of-text', '--of-bg', 4.5), ('--of-control-border', '--of-bg', 3),
                               ('--of-focus', '--of-bg', 3), ('--of-rail-active-color', '--of-bg', 3),
                               ('--of-success', '--of-success-bg', 4.5),
                               ('--of-danger', '--of-danger-bg', 4.5), ('--of-warning', '--of-warning-bg', 4.5)]:
            with self.subTest(fg=fg, bg=bg):
                self.assertGreaterEqual(contrast(tokens[fg], tokens[bg] if bg else '#ffffff'), target)

    def test_approved_rail_geometry_is_preserved(self):
        css = (ROOT / 'tokens.css').read_text()
        for line in ['--of-rail-idle: 6px;', '--of-rail-step: 10px;', '--of-rail-peak: 28px;',
                     '--of-rail-thickness: 2px;']:
            self.assertIn(line, css)
        self.assertIn('prefers-reduced-motion:reduce', build())
        self.assertIn('aria-orientation="vertical"', build())


if __name__ == '__main__':
    unittest.main()
