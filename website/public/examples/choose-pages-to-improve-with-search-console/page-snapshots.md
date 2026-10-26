# Four fictional page snapshots

These are complete, authored short page bodies for this exercise, captured nowhere. Product behavior and examples below are invented. The bodies are intentionally different in purpose and completeness so the decision sheet can point to an inspectable answer gap.

## P1 — Review workflow errors

URL: `https://example.com/guides/review-workflow-errors/`

### Review failed runs from one place

Open **Runs**, choose **Failed**, and select the date range you want to review. Each row shows the workflow name, the time it stopped and the last error message. Open a row to read the steps that finished before the failure.

Start with the oldest failed run and work down the list. Check its error message, correct the input if needed, then retry the run. Assign the run to a teammate if you cannot resolve it yourself.

Review this list regularly so failed work does not build up. When a retry succeeds, mark the original failure as resolved and record what changed.

## P2 — What is a task queue?

URL: `https://example.com/guides/task-queue/`

### A list of work waiting to run

A task queue holds work until a worker can process it. In an operations workflow, a task might send an update, copy a record or wait for a person to review an exception.

Our queue view shows each task's status, owner and last update. Filter by **Waiting**, **Running** or **Failed** to see where work has stopped. A failed task stays visible until someone resolves it.

A queue helps you see pending work, but its order does not tell you which task matters most to a customer. An operator still needs to decide what to handle next. You can assign a failed task to another teammate when it needs their attention.

## P3 — Find delays in approvals

URL: `https://example.com/guides/approval-delays/`

### Check what a request is waiting for

Open **Approvals** and choose **Waiting**. Each request shows when it was created and who needs to respond. Sort by age to find requests that have waited longest.

Open a request and check its current approver. A request without an assigned approver needs an owner before it can move forward. If an approver is assigned, read the request and make sure it includes the material needed for a decision.

Ask the approver what is missing before sending another reminder. If someone else must approve it, reassign the request and leave a note explaining why.

## P4 — Export an audit log

URL: `https://example.com/guides/export-audit-log/`

### Download workflow events as CSV

Open **Activity → Audit log**. Set the start and end dates, choose the workflows you need, then select **Export CSV**. You need the workspace's audit-log export permission; ask an administrator if the button is unavailable.

The file includes one row per event, with `event_time_utc`, `run_id`, `workflow_name`, `event_type` and `actor`. Timestamps use UTC. Successful runs and failed runs are both included unless you add a status filter before exporting.

To check the export, pick a failed run from the on-screen log and look for its `run_id` in the CSV. If it is missing, check the date range and status filter before repeating the export. Keep the original file so you can compare the rows if you later transform it.
