"""Offline validation of relative links, original diagram assets and lab structure."""
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ('Architecture and purpose', 'Prerequisites', 'Troubleshooting and root causes',
            'Security considerations', 'Cleanup', 'Interview questions with answers')


def validate():
    documents = list(ROOT.glob('*.md'))
    documents.extend(p for folder in ('docs', 'deploy', 'observability', 'tests')
                     for p in (ROOT / folder).rglob('*.md'))
    links = 0
    for path in documents:
        content = path.read_text()
        # Fenced examples may contain placeholder paths; only prose links are checked.
        prose = re.sub(r'```.*?```', '', content, flags=re.S)
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', prose):
            if target.startswith(('https:', 'http:', 'mailto:', '#')):
                continue
            location = target.split('#')[0]
            if not (path.parent / location).exists():
                raise ValueError(f'broken relative link in {path.relative_to(ROOT)}: {location}')
            links += 1
    diagrams = ROOT / 'docs/diagrams'
    assets = sorted(diagrams.glob('*.svg'))
    if len(assets) != 8:
        raise ValueError('expected eight original SVG diagrams')
    for asset in assets:
        element = ET.fromstring(asset.read_text())
        if not element.findall('.//{http://www.w3.org/2000/svg}text'):
            raise ValueError('diagram must retain editable text')
        data = asset.with_suffix('.png').read_bytes()
        if data[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', data[16:24]) != (1280, 900):
            raise ValueError('invalid PNG or unexpected diagram size')
    labs = sorted((ROOT / 'docs/labs').glob('*.md'))
    if len(labs) != 14:
        raise ValueError('expected fourteen learner labs')
    for lab in labs:
        content = lab.read_text()
        for title in SECTIONS:
            if f'## {title}' not in content:
                raise ValueError(f'missing lab section {title}: {lab.name}')
        if '```' not in content or '## Expected output' not in content:
            raise ValueError(f'missing commands/expected output: {lab.name}')
    return links, len(assets), len(labs)


if __name__ == '__main__':
    links, diagrams, labs = validate()
    print(f'Offline documentation validation passed: {links} relative links, {diagrams} diagram pairs, {labs} labs.')
