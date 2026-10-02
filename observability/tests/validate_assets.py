"""Asset and privacy checks; native validators remain required before rollout."""
from pathlib import Path
import json
import re
import yaml

root = Path(__file__).resolve().parents[1]
for path in root.rglob('*.yml'):
    yaml.safe_load(path.read_text())
dashboard = json.loads((root / 'grafana/cloudops-dashboard.json').read_text())
assert len({p['id'] for p in dashboard['panels']}) == len(dashboard['panels'])
source = (root.parent / 'app/monitoring/metrics.py').read_text()
known = set(re.findall(r'(?:Counter|Histogram)\("([^"]+)"', source))
known.add('cloudops_http_request_duration_seconds_bucket')
queries = ' '.join(p['targets'][0]['expr'] for p in dashboard['panels'])
queries += (root / 'prometheus/cloudops-rules.yml').read_text()
assert set(re.findall(r'cloudops_[a-z_]+', queries)) <= known
config = (root / 'alloy/config.alloy').read_text()
pattern = json.loads(re.search(r'expression = (".*")', config).group(1))
base = '2026-10-02 10:00:00Z INFO request_id=123e4567-e89b-12d3-a456-426614174000 host=app method=GET path=/ready status=200 duration_ms=1.2 user=private@example.test'
match = re.match(pattern, base)
assert match
clean = ' '.join(f'{k}={v}' for k, v in match.groupdict().items())
assert 'private@' not in clean and 'user=' not in clean and 'host=' not in clean
for line in [base.replace('/ready', '/users/private@example.test'), base.replace('123e4567-e89b-12d3-a456-426614174000', 'private-user-name'), base + ' Authorization=secret', 'DB password=secret', 'Cookie: session=secret', 'Traceback: user=private@example.test']:
    assert re.match(pattern, line) is None
print('YAML, JSON, metric references and privacy fixtures passed')
