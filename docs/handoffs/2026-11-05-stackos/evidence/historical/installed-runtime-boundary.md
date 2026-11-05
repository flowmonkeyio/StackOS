# Installed reviewer runtime boundary canary

Ran after installation of StackOS 2.1.44, build `20261103T210228Z`. The installed verification record reports doctor exit 0 and six source hash checks passed. Installed `reviewer_isolation.py` SHA-256: `a6771c9d643ac047f27971b25caab4496dbb2f6a5c6398ce54ecf3084e39c6ad`.

This was a no-model direct `--observe` repeat using the actual installed helper at `/Applications/StackOS.app/Contents/Resources/stackos/.venv/lib/python3.12/site-packages/stackos/_assets/plugins/engineering/scripts/reviewer_isolation.py` and installed bundled Python. A new private host and all canaries were created under `/var/folders/rv/7zpzsz7d3yg0fmxxjrfcqkfh0000gn/T/stackos-installed-runtime-canary-ifb4w4l2`. They contain only synthetic strings and `view/src/sample.py` (`installed_answer = 55`). No real report, user state, credential, or daemon was accessed. Plan55/run50 was not touched.

## Filesystem and write probe

Exact helper command (exit 0):

```text
/Applications/StackOS.app/Contents/Resources/stackos/.venv/bin/python /Applications/StackOS.app/Contents/Resources/stackos/.venv/lib/python3.12/site-packages/stackos/_assets/plugins/engineering/scripts/reviewer_isolation.py --observe /usr/local/bin/codex /var/folders/rv/7zpzsz7d3yg0fmxxjrfcqkfh0000gn/T/stackos-installed-runtime-canary-ifb4w4l2/view 45 /var/folders/rv/7zpzsz7d3yg0fmxxjrfcqkfh0000gn/T/stackos-installed-runtime-canary-ifb4w4l2/private-host < /var/folders/rv/7zpzsz7d3yg0fmxxjrfcqkfh0000gn/T/stackos-installed-runtime-canary-ifb4w4l2/observe-input.json
```

The exact raw `argv` and Python assertion script are in [installed-runtime-observe-input.json](installed-runtime-observe-input.json). Assertions required the view artifact read to succeed; reads of the synthetic report, user-state, and credential canaries to each raise `PermissionError`; and a write under the view to raise `PermissionError`.

Raw helper output:

```text
Could not find platform dependent libraries <exec_prefix>
{"type": "observation.completed", "exit_code": 0, "stdout": "VIEW_ARTIFACT=READ_OK\nOUTSIDE_READS_BLOCKED=3\nOUTSIDE_READS_LEAKED=0\nOUTCOME_WRITE_BLOCKED=True\n", "stderr": "", "refusal_state": "no_refusal_reported", "confinement": {"mode": "read_only", "verified": true, "network": false, "roots": ["/private/var/folders/rv/7zpzsz7d3yg0fmxxjrfcqkfh0000gn/T/stackos-installed-runtime-canary-ifb4w4l2/view"], "profile": "stackos_outcome_review", "host_contract": "codex-cli 0.164.0", "feature_names_sha256": "9525ece8bc739aeb1626ce8a6a0ee6c227cee3219b4dd437fb0fe462180d2f55"}}
```

The synthetic outcome-write target remained absent after the run.

## Loopback network probe

A disposable listener bound only to `127.0.0.1:51010`. Baseline command and raw output:

```text
/usr/bin/python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",51010),timeout=2); s.sendall(b"baseline"); print("BASELINE_CONNECT=OK"); print("BASELINE_REPLY="+s.recv(128).decode()); s.close()'
exit 0
BASELINE_CONNECT=OK
BASELINE_REPLY=baseline-ok
```

The actual installed helper was invoked again with the same profile and private host. Its exact Python network probe is preserved in [installed-runtime-network-input.json](installed-runtime-network-input.json); it attempted only the synthetic loopback listener. Exact helper command (exit 0):

```text
/Applications/StackOS.app/Contents/Resources/stackos/.venv/bin/python /Applications/StackOS.app/Contents/Resources/stackos/.venv/lib/python3.12/site-packages/stackos/_assets/plugins/engineering/scripts/reviewer_isolation.py --observe /usr/local/bin/codex /var/folders/rv/7zpzsz7d3yg0fmxxjrfcqkfh0000gn/T/stackos-installed-runtime-canary-ifb4w4l2/view 45 /var/folders/rv/7zpzsz7d3yg0fmxxjrfcqkfh0000gn/T/stackos-installed-runtime-canary-ifb4w4l2/private-host < /var/folders/rv/7zpzsz7d3yg0fmxxjrfcqkfh0000gn/T/stackos-installed-runtime-canary-ifb4w4l2/observe-network-input.json
```

Raw helper output:

```text
Could not find platform dependent libraries <exec_prefix>
{"type": "observation.completed", "exit_code": 0, "stdout": "NETWORK_LOOPBACK_BLOCKED=True\nNETWORK_ERROR=1\n", "stderr": "", "refusal_state": "no_refusal_reported", "confinement": {"mode": "read_only", "verified": true, "network": false, "roots": ["/private/var/folders/rv/7zpzsz7d3yg0fmxxjrfcqkfh0000gn/T/stackos-installed-runtime-canary-ifb4w4l2/view"], "profile": "stackos_outcome_review", "host_contract": "codex-cli 0.164.0", "feature_names_sha256": "9525ece8bc739aeb1626ce8a6a0ee6c227cee3219b4dd437fb0fe462180d2f55"}}
```

The listener recorded `ACCEPT_1` for the baseline and then `LISTENER_END=timeout`; no second connection arrived. No external address or StackOS daemon was contacted.

Both observe invocations printed the bundled-Python startup warning `Could not find platform dependent libraries <exec_prefix>`, but each command completed successfully with exit 0 and all assertions passed.
