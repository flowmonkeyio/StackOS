# SEO Plugin

`plugins/seo/plugin.yaml` is the StackOS catalog boundary for SEO work.

This package owns the SEO domain shape:

- SEO capabilities, providers, actions, resources, and nav live in the plugin
  manifest.
- SEO workflow templates live under `plugins/seo/workflows`.
- Action entries bind to daemon-side tools through `config.tool_ref` or
  `config.tool_refs`; the manifest is declarative metadata only.
- Secrets never belong here. Provider credentials are resolved by daemon-side
  auth providers/connectors.
