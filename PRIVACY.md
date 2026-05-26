# Privacy

StackOS is local-first. The daemon binds to loopback, stores its
SQLite database under the local XDG data directory, and does not include
telemetry or phone-home behavior.

## Local Data

The daemon stores project configuration, task/ticket state, resource records,
artifacts, communication history that providers deliver to configured ingress
routes, run logs, provider metadata, and encrypted credentials in
`~/.local/share/stackos/stackos.db` by default. The
per-machine encryption seed and bearer token live under
`~/.local/state/stackos/` with mode `0600`.

Backups and restores copy local files only. Moving an install to another
machine requires the database and matching `seed.bin`; without that seed,
encrypted integration credentials cannot be decrypted.

## Outbound Calls

StackOS only contacts external services when you configure and run the
corresponding provider connection, action, communication route, job, or run
plan. Depending on enabled plugins and configured credentials, those calls may
include:

- Communications providers such as Telegram Bot API, Slack Web API, SMTP
  servers, and IMAP servers.
- Publishing providers such as WordPress and Ghost.
- GTM and RevOps providers such as CRM, enrichment, outbound, workspace, and
  pipeline tools declared by the enabled GTM plugin.
- Media-buying providers such as Meta Ads, Google Ads, Taboola, Outbrain, or
  project-local media tools when those action contracts are configured.
- SEO and research providers such as DataForSEO, Ahrefs, Google Search
  Console, Reddit, Firecrawl, Jina Reader, sitemap targets, and configured
  HTTP endpoints.
- Utility providers such as OpenAI Images for generated image assets.

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
