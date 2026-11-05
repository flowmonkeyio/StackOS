"""Retain final publication evidence and hashes; no external actions."""
import json
from pathlib import Path
from stage import HERE, ROOT, sha, write

refs = [
    'upload-manifest.json', 'upload-input.json', 'preparation-receipt.json',
    'public-preflight/result.json', 'public-preflight/recovery-manifest.json',
    'public-preflight/recovery-upload-input.json', 'public-after/result.json',
    'public-after-retry/result.json', 'browser-verification.json',
    'sitemap-diff.json',
]
refs += [str(p.relative_to(HERE)) for p in sorted((HERE / 'receipts').glob('*.json'))]
refs += [str(p.relative_to(HERE)) for p in sorted(HERE.glob('content-556-live-*.png'))]
proof = {
    'project_id': 1, 'run_plan_id': 556, 'execution_run_id': 551,
    'design_run_plan_id': 555, 'status': 'published-and-verified',
    'operator_approval': 'Conversation: Looks good. Proceed to deployment.',
    'site_url': 'https://stackos.flowmonkey.io/',
    'candidate_sha256': '97dc66f058dcac100801455a8e78071c8f2b5a15e19467d905e5f4ce7ed3b21b',
    'upload': {'action_call_id': 11646, 'files': 460, 'bytes': 10601476, 'failed': 0, 'skipped': 0},
    'production_verification': {'unique_urls': 475, 'attempts': 477, 'final_failures': 0, 'browser': 'passed'},
    'preservation': {'desktop_entries_unchanged': 112, 'ownership_bytes_unchanged': True, 'desktop_feed_bytes_unchanged': True, 'installer_head_identity_unchanged': True, 'recovery_files': 382},
    'independent_review': {
        'reviewer': '/root/content_design_review', 'decision': 'accept', 'findings': [],
        'scope': 'Candidate freshness, staged delta, exact transfer receipts and public hashes, recovery completeness, desktop preservation and bounded retries.',
        'main_adjudication': 'Accepted all three independent reviews; main separately viewed all four production browser screenshots and checked final console.',
        'source_checks': '12 accepted file hashes,119 build-input hashes and protected baseline match.',
        'artifact_identity_limit': 'No historical whole-output manifest existed at design acceptance. Current output was independently pinned after verifying recorded hashes and timestamps.',
    },
    'sitemap': {
        'submit_action_call_id': 11648, 'list_action_call_id': 11649,
        'url': 'https://stackos.flowmonkey.io/sitemap.xml', 'submitted': True,
        'last_submitted': '2026-10-05T21:40:16.785Z', 'is_pending': True,
        'last_downloaded': '2026-10-05T19:33:51.682Z', 'errors': 0, 'warnings': 0,
        'reported_url_count': 188,
    },
    'limits': [
        'Google processing pending; no claim about recrawl, page indexing or ranking.',
        'No Hostinger cache purge performed. Ordinary public URLs and StackOS browser served the verified candidate.',
        'Two TLS verification timeouts were each retried once; original evidence retained.',
        'No desktop release, signing, install or daemon lifecycle mutation.',
    ],
    'evidence': [{'path': str((HERE / p).relative_to(ROOT)), 'sha256': sha(HERE / p), 'bytes': (HERE / p).stat().st_size} for p in refs],
}
write(HERE / 'publication-proof.json', proof)
print(json.dumps({'path': str(HERE / 'publication-proof.json'), 'sha256': sha(HERE / 'publication-proof.json'), 'bytes': (HERE / 'publication-proof.json').stat().st_size, 'evidence_files': len(refs)}))
