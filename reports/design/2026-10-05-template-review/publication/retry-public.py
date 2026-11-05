"""Retry the two recorded transport failures once; preserve original evidence."""
import copy
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request
from stage import ROOT, HERE, sha, read, write

original_path = HERE / 'public-after/result.json'
original = read(original_path)
failed = original['failures']
assert len(failed) == 2
assert {r['url'] for r in failed} == {
    'https://stackos.flowmonkey.io/__stackos-content-design-555-not-found__/',
    'https://stackos.flowmonkey.io/_nuxt/2Al9K7qh.js',
}
assert all('handshake operation timed out' in r.get('error', '') for r in failed)
out = HERE / 'public-after-retry'
out.mkdir(exist_ok=False)
retries = []
for prior in failed:
    row = {k: v for k, v in prior.items() if k in ('url', 'method', 'expected_sha256', 'expected_status')}
    request = urllib.request.Request(row['url'], headers={'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'})
    try:
        response = urllib.request.urlopen(request, timeout=25)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read(16 * 1024 * 1024 + 1)
        assert len(data) <= 16 * 1024 * 1024
        row.update(status=response.status, final_url=response.geturl(), headers={k.lower(): v for k, v in response.headers.items() if k.lower() != 'set-cookie'})
    body = out / (hashlib.sha256(row['url'].encode()).hexdigest()[:24] + '.body')
    body.write_bytes(data)
    row.update(body_path=str(body.relative_to(ROOT)), bytes=len(data), sha256=sha(body))
    expected = row['expected_sha256'] or sha(HERE / 'frozen/404.html')
    row['pass'] = row['status'] == row.get('expected_status', 200) and row['final_url'] == row['url'] and row['sha256'] == expected
    retries.append(row)
report = copy.deepcopy(original)
by_url = {r['url']: r for r in retries}
report['captures'] = [by_url.get(r['url'], r) for r in original['captures']]
report['failures'] = [r for r in report['captures'] if not r['pass']]
report['status'] = 'repair' if report['failures'] else 'pass'
report['retry_history'] = {'original_path': str(original_path.relative_to(ROOT)), 'original_sha256': sha(original_path), 'original_failures': failed, 'retry_captures': retries, 'retry_request_count': 2, 'total_request_count': 477}
write(out / 'result.json', report)
print(json.dumps({'status': report['status'], 'unique_urls': len(report['captures']), 'total_requests': 477, 'failures': report['failures']}))
raise SystemExit(bool(report['failures']))
