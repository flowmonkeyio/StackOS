# SEO Plugin Agent Notes

The SEO plugin is the first-party StackOS domain package for SEO work.

- Own SEO catalog metadata here: capabilities, providers, actions, resources,
  UI nav, and workflow templates.
- Treat every `config.tool_ref` or `config.tool_refs` entry as a local action
  binding. The manifest describes the binding; execution remains daemon-side
  and grant-checked.
- Keep utility providers such as image generation, web scraping, Jina, and
  Reddit under the `utils` plugin unless the provider is SEO-specific.
- Secrets never belong in plugin files. Provider auth metadata is declarative;
  credentials are resolved by daemon-side auth providers/connectors.
