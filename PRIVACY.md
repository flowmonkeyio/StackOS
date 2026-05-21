# Privacy

content-stack is local-first. The daemon binds to loopback, stores its
SQLite database under the local XDG data directory, and does not include
telemetry or phone-home behavior.

## Local Data

The daemon stores project configuration, content, prompts, run logs,
integration metadata, and encrypted credentials in
`~/.local/share/content-stack/content-stack.db` by default. The
per-machine encryption seed and bearer token live under
`~/.local/state/content-stack/` with mode `0600`.

Backups and restores copy local files only. Moving an install to another
machine requires the database and matching `seed.bin`; without that seed,
encrypted integration credentials cannot be decrypted.

## Outbound Calls

content-stack only contacts external services when you configure and run
the corresponding integration, action, job, or run plan. Those calls may
include:

- DataForSEO for keyword and SERP data.
- Firecrawl and Jina for crawl/read operations.
- Google Search Console and Google OAuth endpoints for Search Console
  data and token refresh.
- OpenAI Images for generated image assets.
- Reddit or Ahrefs when those integrations are configured.

Live vendor calls are operator-triggered through configured credentials,
scheduled jobs, or granted run-plan actions. The daemon does not send local project
data to any vendor outside those explicit integration paths.

## Browser UI

The Vue UI is served by the same localhost daemon. REST and MCP requests
require the per-install bearer token except for narrow bootstrap and
OAuth callback routes documented in `docs/security.md`.

## Logs

Run and step logs are local database rows. Daemon logs are local files
under the state directory. Request/response audit rows are sanitized so
secret tokens are not intentionally written to logs.

## Removing Data

`make uninstall` removes installed skills, MCP entries, and
the optional launchd plist, but intentionally preserves the database,
seed, and auth token. Delete the XDG data/state directories manually only
when you are sure you no longer need the content or encrypted credentials.
