# Accepted runtime persistence proof: scope and limits

The recorded result covers a synthetic project, run plan, and receipt in the dedicated isolated `runtime-data` database. A separate local daemon process read the saved records through the accepted candidate API after the original fixture process had exited. The checked request adapter rejected a synthetic protected body before HTTP, and the scoped run-plan list stayed unchanged.

The harness stopped and joined its own Uvicorn thread successfully. A separate `ps` check for that PID was denied by the sandbox, so no external process-table confirmation is recorded. The run does not prove behavior of the installed daemon, a full host or delivery workflow, model execution, or any production/business operation. No claim beyond the bounded API readback and adapter rejection result is made.

`persistence-result.json` records the retained command, exit status, and sanitized observed output. Its provenance statement is explicit because the record was created after the execution from the retained tool result; the execution itself was not repeated.
