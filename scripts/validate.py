#!/usr/bin/env python3
"""Validate published sources and run existing offline checks; never access a robot."""
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
CAL = ROOT / 'tools/calibrator'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(*args, cwd=ROOT, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True, **kwargs)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.links.extend(v for k, v in attrs if k in ('src', 'href') and v)


def main():
    require(sys.version_info >= (3, 10), 'Python 3.10+ required')
    require(shutil.which('node'), 'Node.js required for syntax validation')
    manifest = json.loads((CAL / 'static/source-manifest.json').read_text())
    require(len(manifest['files']) == 11, 'Unexpected upstream reference file set')
    for relative, digest in manifest['files'].items():
        file = CAL / 'static/source' / relative
        require(hashlib.sha256(file.read_bytes()).hexdigest() == digest,
                f'Upstream reference hash mismatch: {relative}')
    snapshot = json.loads((CAL / 'source-snapshot.json').read_text())
    for relative, digest in snapshot['files'].items():
        require(hashlib.sha256((CAL / relative).read_bytes()).hexdigest() == digest,
                f'Published runtime differs from source snapshot: {relative}')
    print('PASS: original calibration runtime and 11 upstream reference hashes', flush=True)

    for file in ROOT.rglob('*.py'):
        if '.git' not in file.parts:
            ast.parse(file.read_text(), filename=str(file))
    for file in (CAL / 'static').rglob('*.js'):
        content = file.read_text()
        subprocess.run(['node', '--check', '--input-type=module'],
                       input=content, text=True, check=True, capture_output=True)
        for relative in re.findall(r'''from\s*['"](\.[^'"]+)['"]''', content):
            require((file.parent / relative).is_file(), f'Missing import in {file}: {relative}')
    for html in (CAL / 'static').rglob('*.html'):
        parser = Links()
        parser.feed(html.read_text())
        for link in parser.links:
            url = urlsplit(link)
            if url.scheme or url.netloc or not url.path or url.path.startswith('/api/'):
                continue
            require((html.parent / unquote(url.path)).exists(), f'Missing HTML asset: {link}')
    require((CAL / 'static/source/assets/duck-visual.bin').stat().st_size > 1_000_000,
            'Display mesh is missing or a placeholder')
    for md in ROOT.rglob('*.md'):
        if '.git' in md.parts:
            continue
        for target in re.findall(r'\]\(([^\s)]+)\)', md.read_text()):
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            require((md.parent / unquote(url.path)).exists(), f'Broken Markdown link in {md}: {target}')
    print('PASS: Python/JavaScript syntax, calibration assets and documentation links', flush=True)
    run(sys.executable, '-m', 'unittest', 'discover', '-s', 'tools/calibrator', '-v')
    run(sys.executable, 'scripts/validate.py', cwd=ROOT / 'tools/servo-guide')
    print('PASS: all MicroDuck Maker offline validation', flush=True)


if __name__ == '__main__':
    main()
