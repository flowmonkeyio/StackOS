"""Main-owned public preflight/verification; no uploads, builds or source edits.

Without --execute validates local inputs only. Preflight captures every prior
file being overwritten and prepares a recovery upload. Verify requires native
successful transfer receipts and checks every staged file over public HTTPS.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from stage import HERE, ROOT, PHASES, ACCEPTED, sha, read, write, inventory

ORIGIN = 'https://stackos.flowmonkey.io'
DMG = '/StackOS/stackos-latest-mac-arm64.dmg'
UPDATE = '/StackOS/latest-mac.yml'
OWNERSHIP = '/google1e7f2e5eaf4f6048.html'
MAX_BYTES = 16 * 1024 * 1024
HEADERS = ('content-type', 'content-length', 'etag', 'last-modified')
RELEASE_BEFORE = HERE / 'receipts/stackos-2.1.18-release-20261005T212728Z-11645.json'
ROOT_BEFORE = HERE / 'receipts/stackos-2.1.18-release-20261005T212718Z-11644.json'

def route(name):
    return '/' if name == 'index.html' else '/' + name[:-10] if name.endswith('/index.html') else '/' + name

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None

class Page(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.canonicals, self.meta = [], {}
        self.feed(body)
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'link' and a.get('rel') == 'canonical':
            self.canonicals.append(a.get('href'))
        if tag == 'meta':
            self.meta.setdefault(a.get('name') or a.get('property'), []).append(a.get('content', ''))

def listing(path, remote):
    envelope = read(path)
    output = envelope['response']['output_json']
    assert envelope['action_call']['status'] == output['status'] == 'success'
    assert output['provider'] == 'ftp' and output['operation'] == 'directory.list'
    assert envelope['request']['input_json']['remote_path'].rstrip('/') == remote.rstrip('/')
    assert len(output['entries']) == output['entry_count']
    return sorted(output['entries'], key=lambda r: r['remote_path'])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight', 'verify'))
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--release-before', type=Path, default=RELEASE_BEFORE)
    parser.add_argument('--root-before', type=Path, default=ROOT_BEFORE)
    parser.add_argument('--release-after', type=Path)
    parser.add_argument('--preflight-result', type=Path)
    parser.add_argument('--upload-receipt', type=Path, action='append', default=[])
    parser.add_argument('--workers', type=int, choices=range(1, 9), default=6)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    assert out.is_relative_to(HERE) and not out.exists(), 'Use a new publication-local capture directory.'
    manifest = read(HERE / 'upload-manifest.json')
    manifest_sha = sha(HERE / 'upload-manifest.json')
    assert manifest['run_plan_id'] == 556 and manifest['execution_run_id'] == 551
    assert sha(ACCEPTED) == manifest['source_manifest_sha256']
    for row in read(ACCEPTED)['changed_files']:
        assert sha(ROOT / row['path']) == row['sha256'], 'Source drift: ' + row['path']
    assert sha(HERE / 'output-manifest.json') == manifest['output_manifest_sha256']
    assert inventory(ROOT / 'website/.output/public') == read(HERE / 'output-manifest.json'), 'Build output drift.'
    assert sha(HERE / 'upload-input.json') == manifest['upload_input_sha256']
    files = manifest['files']
    expected_stage = {r['phase'] + '/' + r['path']: (r['sha256'], r['bytes']) for r in files}
    assert {r['path']: (r['sha256'], r['bytes']) for r in inventory(HERE / 'staging')} == expected_stage
    before_entries = listing(args.release_before, '/StackOS')
    root_entries = listing(args.root_before, '/')
    assert any(r['remote_path'].rstrip('/') == '/StackOS' for r in root_entries)
    assert any(r['remote_path'] == OWNERSHIP for r in root_entries)
    requests = {}
    def add(path, method='GET', expected_sha=None, **extra):
        requests[ORIGIN + path] = {'method': method, 'expected_sha256': expected_sha, **extra}
    protected = manifest['unchanged_static_controls_preserved']
    preflight = None
    receipt_refs = []
    if args.mode == 'preflight':
        for row in files:
            if row['before_sha256']:
                add(route(row['path']), expected_sha=row['before_sha256'], recovery_path=row['path'], recovery_phase=row['phase'])
        for name in protected:
            if name != '.htaccess':
                add('/' + name, expected_sha=sha(HERE / 'frozen' / name))
        add(UPDATE)
        add(OWNERSHIP)
        add(DMG, 'HEAD')
    else:
        assert args.preflight_result and args.release_after and args.upload_receipt
        preflight = read(args.preflight_result)
        assert preflight['mode'] == 'preflight' and preflight['status'] == 'pass'
        assert preflight['upload_manifest_sha256'] == manifest_sha
        assert preflight['release_before_sha256'] == sha(args.release_before)
        assert before_entries == listing(args.release_after, '/StackOS'), 'Desktop release inventory changed.'
        transferred = set()
        for path in args.upload_receipt:
            receipt = read(path)
            result = receipt['response']['output_json']
            assert receipt['action_call']['status'] == result['status'] == 'success'
            assert result['provider'] == 'ftp' and result['operation'] == 'file.upload'
            assert not result.get('failed_count') and not result.get('failed_paths') and not result.get('skipped_count')
            assert result['completed_count'] == len(result['completed_paths'])
            transferred.update(n.lstrip('/') for n in result['completed_paths'])
            receipt_refs.append({'path': str(path), 'sha256': sha(path), 'action_call_id': receipt['action_call']['id']})
        assert transferred == {r['path'] for r in files}, 'Actual transfer set differs from frozen stage.'
        for row in files:
            add(route(row['path']), expected_sha=row['sha256'])
        for row in preflight['captures']:
            path = urllib.parse.urlsplit(row['url']).path
            if path == DMG:
                add(path, 'HEAD', expected_headers={k: row['headers'][k] for k in HEADERS})
            elif path in {UPDATE, OWNERSHIP, *('/' + p for p in protected if p != '.htaccess')}:
                assert sha(ROOT / row['body_path']) == row['sha256']
                add(path, expected_sha=row['sha256'])
    for slug, target in [('core', 'local-daemon'), ('linear', 'linear'), ('shopify', 'shopify'), ('trackbooth', 'trackbooth')]:
        add('/library/integrations/plugins/' + slug + '/', expected_status=301, location=ORIGIN + '/library/integrations/' + target + '/')
    for path in ('/getting-started', '/library'):
        add(path, expected_status=301, location=ORIGIN + path + '/')
    requests['http://stackos.flowmonkey.io/'] = {'method': 'GET', 'expected_sha256': None, 'expected_status': 301, 'location': ORIGIN + '/'}
    add('/__stackos-content-design-555-not-found__/', expected_status=404)
    if not args.execute:
        print(__import__('json').dumps({'status': 'inputs-pass', 'mode': args.mode, 'planned_requests': len(requests), 'network_requests': 0}))
        return 0
    out.mkdir(parents=True)
    def capture(item):
        url, expectation = item
        row = {'url': url, **expectation}
        data = b''
        try:
            request = urllib.request.Request(url, method=row['method'], headers={'User-Agent': 'StackOS-website-556-verification/1.0', 'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'})
            try:
                response = urllib.request.build_opener(NoRedirect()).open(request, timeout=25)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                row.update(status=response.status, final_url=response.geturl(), headers={k.lower(): v for k, v in response.headers.items() if k.lower() != 'set-cookie'})
                if row['method'] == 'GET':
                    data = response.read(MAX_BYTES + 1)
                    assert len(data) <= MAX_BYTES, 'Response exceeds16MiB bound.'
        except (urllib.error.URLError, OSError, TimeoutError, AssertionError) as error:
            row['error'] = str(error)
        row['pass'] = row.get('status') == row.get('expected_status', 200) and row.get('final_url') == url and not row.get('error')
        if row['method'] == 'GET':
            body = out / (hashlib.sha256(url.encode()).hexdigest()[:24] + '.body')
            body.write_bytes(data)
            row.update(body_path=str(body.relative_to(ROOT)), bytes=len(data), sha256=sha(body))
            row['pass'] = bool(row['pass'] and (row['expected_sha256'] is None or row['sha256'] == row['expected_sha256']))
        else:
            row['pass'] = bool(row['pass'] and all(row.get('headers', {}).get(k) for k in HEADERS))
            if row.get('expected_headers'):
                row['pass'] = bool(row['pass'] and all(row['headers'].get(k) == v for k, v in row['expected_headers'].items()))
            row['pass'] = bool(row['pass'] and row.get('headers', {}).get('content-type', '').startswith('application/x-apple-diskimage'))
        headers = row.get('headers', {})
        if row.get('location'):
            row['pass'] = bool(row['pass'] and urllib.parse.urljoin(url, headers.get('location', '')) == row['location'])
        elif url.endswith('/') and row.get('expected_status', 200) == 200:
            page = Page(data.decode('utf-8', errors='replace'))
            directives = ' '.join(page.meta.get('robots', []) + page.meta.get('googlebot', [])) + ' ' + headers.get('x-robots-tag', '')
            row['page_checks'] = {'canonical': page.canonicals == [url], 'social_url': page.meta.get('og:url') == [url],
                'indexable': not bool(re.search(r'\b(noindex|nofollow|none)\b', directives.lower())),
                'html_content_type': headers.get('content-type', '').startswith('text/html'),
                'security_headers': headers.get('x-content-type-options') == 'nosniff' and headers.get('x-frame-options') == 'DENY'
                    and headers.get('referrer-policy') == 'strict-origin-when-cross-origin' and 'max-age=' in headers.get('strict-transport-security', '')
                    and 'preload' not in headers.get('strict-transport-security', '') and bool(headers.get('permissions-policy'))}
            row['pass'] = bool(row['pass'] and all(row['page_checks'].values()))
        if url.endswith('/feed.xml') or url.endswith('/getting-started.md'):
            row['pass'] = bool(row['pass'] and 'noindex' in headers.get('x-robots-tag', ''))
        if url.endswith('/getting-started.md'):
            row['pass'] = bool(row['pass'] and headers.get('content-type', '').startswith('text/markdown') and ORIGIN + '/getting-started/' in headers.get('link', ''))
        return row
    captures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(capture, x) for x in sorted(requests.items())]):
            captures.append(future.result())
            if len(captures) % 50 == 0:
                print(__import__('json').dumps({'captured': len(captures), 'planned': len(requests)}), flush=True)
    failures = [r for r in captures if not r['pass']]
    if args.mode == 'preflight' and not failures:
        for row in captures:
            if row.get('recovery_path'):
                target = out / 'recovery' / row['recovery_phase'] / row['recovery_path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / row['body_path'], target)
        write(out / 'recovery-upload-input.json', {'items': [{'local_path': str(out / 'recovery' / p), 'remote_path': '/'} for p in PHASES if (out / 'recovery' / p).exists()], 'conflict_policy': 'overwrite', 'error_policy': 'stop', 'follow_symlinks': False})
        write(out / 'recovery-manifest.json', inventory(out / 'recovery'))
    report = {'status': 'repair' if failures else 'pass', 'mode': args.mode, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'run_plan_id': 556, 'execution_run_id': 551, 'upload_manifest_sha256': manifest_sha,
        'release_before_sha256': sha(args.release_before), 'root_before_sha256': sha(args.root_before),
        'release_after_sha256': sha(args.release_after) if args.release_after else None,
        'preflight_result_sha256': sha(args.preflight_result) if args.preflight_result else None,
        'upload_receipts': receipt_refs, 'captures': sorted(captures, key=lambda r: r['url']), 'failures': failures,
        'limits': ['One bounded public request per URL; no upload or automatic retry.', 'Recovery restores overwritten existing website bytes; new hashed assets may remain harmlessly remote.', 'Desktop release inventory/feed bytes/installer HEAD identity only; no binary download or signing claim.', '.htaccess unchanged and not staged; observed security/redirect checks verify its deployed behavior.', 'No indexing or ranking claim.']}
    write(out / 'result.json', report)
    print(__import__('json').dumps({'status': report['status'], 'requests': len(captures), 'failures': len(failures), 'result': str(out / 'result.json')}))
    return bool(failures)

if __name__ == '__main__':
    raise SystemExit(main())
