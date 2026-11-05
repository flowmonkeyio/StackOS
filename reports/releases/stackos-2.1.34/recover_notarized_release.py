#!/usr/bin/env python3
"""Continue this exact signed release after Apple accepts the existing app submission.

continue: validate/staple the accepted app, package it, run the canonical smokes,
          submit the signed DMG once and retain its submission ID.
finish:   require that same DMG submission to be Accepted, then staple, refresh
          update metadata, and create the stable alias. No install or upload.
"""
import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
HOST = ROOT.parent / 'host'
DESKTOP = HOST / 'desktop'
APP = DESKTOP / 'dist/mac-arm64/StackOS.app'
DMG = DESKTOP / 'dist/stackos-2.1.34-mac-arm64.dmg'
CONFIG = DESKTOP / '.electron-builder.generated.json'
SUBMISSION = ROOT / 'recovery-dmg-submission.json'
APP_SUBMISSION = '21b7266a-37da-42d0-9c2b-3b901321dc0a'
PROFILE = 'stackos-notary'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('x') as output:
        output.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=['continue', 'finish'])
parser.add_argument('host_commit', help='Exact accepted clean host commit')
parser.add_argument('--attempt', default='1', help='Distinct receipt label for a later status-check attempt')
args = parser.parse_args()
assert args.attempt and all(c.isalnum() or c in '-_' for c in args.attempt)
receipt_path = ROOT / f'recovery-{args.phase}-{args.attempt}.json'
assert not receipt_path.exists(), 'Attempt evidence exists; choose another --attempt'
head = subprocess.check_output(['git', '-C', str(HOST), 'rev-parse', 'HEAD'], text=True).strip()
assert head == args.host_commit and len(head) == 40, 'Host commit mismatch'
assert not subprocess.check_output(['git', '-C', str(HOST), 'status', '--porcelain=v1'], text=True).strip(), 'Dirty host checkout'
assert json.loads((DESKTOP / 'package.json').read_text())['version'] == '2.1.34'
config = json.loads(CONFIG.read_bytes())
assert config['forceCodeSigning'] is True and config['dmg']['sign'] is True
assert config['mac']['notarize'] is True
assert config['publish'] == [{'provider': 'generic', 'url': 'https://stackos.flowmonkey.io/StackOS/'}]
assert json.loads((APP / 'Contents/Resources/update-config.json').read_bytes()) == {'updateUrl': 'https://stackos.flowmonkey.io/StackOS/'}
env = dict(os.environ)
for key in ('STACKOS_ALLOW_UNSIGNED_RELEASE', 'STACKOS_SKIP_NOTARIZATION', 'STACKOS_UNSIGNED_DEV', 'STACKOS_DESKTOP_BUILD_DRY_RUN'):
    env.pop(key, None)
env.update(CSC_NAME='SERGEY RURA (TSHN26FR48)', APPLE_KEYCHAIN_PROFILE=PROFILE,
           STACKOS_UPDATE_URL='https://stackos.flowmonkey.io/StackOS/',
           STACKOS_REQUIRE_SIGNING='1', STACKOS_REQUIRE_UPDATE_URL='1')
receipt = dict(phase=args.phase, status='running', host_commit=head, host_version='2.1.34',
               connector_version='0.2.1', app_submission_id=APP_SUBMISSION, config_sha256=sha(CONFIG),
               started_at=datetime.now(UTC).isoformat(), steps=[], local_activation=False)
write_json(receipt_path, receipt)


def step(name, command, check=True):
    log = ROOT / f'recovery-{args.phase}-{args.attempt}-{len(receipt["steps"]):02d}-{name}.log'
    record = dict(name=name, command=command, started_at=datetime.now(UTC).isoformat(), log=str(log))
    receipt['steps'].append(record)
    write_json(receipt_path, receipt)
    with log.open('x') as output:
        result = subprocess.run(command, cwd=DESKTOP, env=env, stdout=output, stderr=subprocess.STDOUT)
    record.update(exit_code=result.returncode, finished_at=datetime.now(UTC).isoformat(), log_sha256=sha(log))
    write_json(receipt_path, receipt)
    if check and result.returncode:
        raise RuntimeError(f'{name} failed with exit {result.returncode}; see {log}')
    return result.returncode, log


def require_accepted(name, submission_id):
    _, log = step(name, ['xcrun', 'notarytool', 'info', submission_id, '--keychain-profile', PROFILE, '--output-format', 'json'])
    data = json.loads(log.read_text())
    assert data['id'] == submission_id and data['status'] == 'Accepted', f'{submission_id} is not Accepted; preserve the existing submission and poll it'
    return data


try:
    if args.phase == 'continue':
        assert not SUBMISSION.exists(), 'A DMG submission attempt exists; use finish or inspect its saved ID, never resubmit automatically'
        assert not DMG.exists() and not DMG.with_suffix('.zip').exists(), 'Existing distributables require inspection before packaging again'
        require_accepted('app-notary-info', APP_SUBMISSION)
        step('app-signature', ['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(APP)])
        step('app-staple', ['xcrun', 'stapler', 'staple', str(APP)])
        step('app-staple-validate', ['xcrun', 'stapler', 'validate', str(APP)])
        step('app-gatekeeper', ['spctl', '--assess', '--type', 'execute', '--verbose=2', str(APP)])
        sealed_resources = sha(APP / 'Contents/_CodeSignature/CodeResources')
        receipt['app_code_resources_sha256'] = sealed_resources
        step('prepackaged', ['pnpm', 'exec', 'electron-builder', '--mac', '--arm64', '--prepackaged', str(APP), '--config', str(CONFIG), '--publish', 'never'])
        assert sha(APP / 'Contents/_CodeSignature/CodeResources') == sealed_resources, 'App signature resources changed while packaging'
        step('packaged-cli', ['node', 'scripts/verify-stackos-cli.cjs', str(APP / 'Contents/Resources/stackos/bin/stackos'), '2.1.34'])
        step('packaged-browser-cli', ['node', 'scripts/verify-stackos-browser-cli.cjs', str(APP / 'Contents/Resources/stackos/bin/stackos.browser')])
        step('dmg-signature', ['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(DMG)])
        submission = dict(status='submission_started', artifact=str(DMG), artifact_sha256=sha(DMG),
                          artifact_bytes=DMG.stat().st_size, host_commit=head,
                          started_at=datetime.now(UTC).isoformat(), continuation_receipt=str(receipt_path))
        write_json(SUBMISSION, submission)
        code, log = step('dmg-submit', ['xcrun', 'notarytool', 'submit', str(DMG), '--keychain-profile', PROFILE, '--output-format', 'json'], check=False)
        submission.update(exit_code=code, response_log=str(log), response_log_sha256=sha(log))
        try:
            response = json.loads(log.read_text())
            submission.update(submission_id=response.get('id'), apple_status=response.get('status'))
        except (ValueError, AttributeError):
            submission['status'] = 'submission_outcome_unknown'
        if submission.get('submission_id'):
            submission['status'] = 'submitted'
        write_json(SUBMISSION, submission)
        assert submission.get('submission_id'), 'DMG submission returned no ID; inspect Apple history before any further action. Do not resubmit.'
        receipt.update(status='dmg_submitted_pending_acceptance', dmg_submission_id=submission['submission_id'])
    else:
        submission = json.loads(SUBMISSION.read_bytes())
        assert submission.get('submission_id'), 'No saved DMG submission ID; inspect the submission attempt'
        assert submission['host_commit'] == head
        assert sha(DMG) == submission['artifact_sha256'], 'DMG bytes differ from the submitted artifact'
        require_accepted('dmg-notary-info', submission['submission_id'])
        step('dmg-staple', ['xcrun', 'stapler', 'staple', str(DMG)])
        step('dmg-staple-validate', ['xcrun', 'stapler', 'validate', str(DMG)])
        step('dmg-gatekeeper', ['spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=2', str(DMG)])
        step('refresh-update-metadata', ['node', 'scripts/refresh-notarized-update-metadata.cjs', str(DMG), str(DESKTOP / 'dist/latest-mac.yml')])
        step('stable-dmg-alias', ['node', '-e', 'const {createLatestDmgAliases}=require("./scripts/release-artifacts.cjs"); console.log(JSON.stringify(createLatestDmgAliases([process.argv[1]])));', str(DMG)])
        receipt.update(status='signed_notarized_packaging_complete', dmg_submission_id=submission['submission_id'], final_dmg_sha256=sha(DMG))
except Exception as error:
    receipt.update(status='stopped', error=f'{type(error).__name__}: {error}')
finally:
    receipt['finished_at'] = datetime.now(UTC).isoformat()
    write_json(receipt_path, receipt)
print(json.dumps(dict(status=receipt['status'], receipt=str(receipt_path), sha256=sha(receipt_path),
                      dmg_submission_id=receipt.get('dmg_submission_id'), error=receipt.get('error'))))
sys.exit(1 if receipt['status'] == 'stopped' else 0)
