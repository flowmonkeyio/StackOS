# content-stack plugin compatibility notes

This plugin is the current installed agent surface for the historical
content-stack/SEO Stack product. Keep it working while StackOS is introduced.

Target direction:

- A future `stackos-agent` plugin should expose the generic StackOS bridge.
- This plugin may remain as a compatibility alias during migration.
- Do not expand this plugin with new domain-specific direct tools.
- Prefer generic plugin/catalog/capability/provider discovery, auth refs,
  workflow templates, run plans, and granted action execution as those surfaces
  land.

Compatibility rules:

- Preserve existing `content-stack mcp-bridge` behavior unless a signed-off task
  explicitly migrates it.
- Keep SEO/procedure tools available for current users until D12/D13 migrate
  them into SEO plugin/workflow-template compatibility.
- Never expose secrets to agents. Credential setup/status should use daemon-side
  auth flows, opaque refs, redacted status, and sanitized logs.
- Operational/vendor calls must remain hidden/gated through toolbox/grant
  mechanics, not added as broad direct tools.
