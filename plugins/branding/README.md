# Branding Plugin

`branding` is the generic Level 1 authority-content plugin. It provides schemas,
workflow templates, agent presets, and the main-agent orchestration preset for
evidence-grounded content production and governed distribution.

The plugin owns only generic contracts:

- evidence-items, streams, channel charters, routing policy, content-pieces, and
  distribution-records
- workflow shapes from evidence harvest through outcome capture and audit
- role contracts for writing, routing, review, publication, and governance

Level 2 project overlays own all operator-specific values:

- sources of record
- voice profile and kill tests
- disclosure policy
- concrete channel instances, handles, charters, cadence, and publication modes
- canonical site stack and provider credentials
- standing positions and stream instances

Agents should start from `branding.brand-orchestrator`, then adapt the required
specialist preset for each workflow step. StackOS resources remain the single
source of truth; local files may mirror artifacts but should not replace
evidence, content-piece, routing, approval, or distribution records.
