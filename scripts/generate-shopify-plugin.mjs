#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const sourceRoot = process.argv[2];
const outputPath = process.argv[3] || "plugins/shopify/plugin.yaml";

if (!sourceRoot) {
  console.error("usage: node scripts/generate-shopify-plugin.mjs <cob-shopify-tools-root> [output]");
  process.exit(2);
}

const domains = ["analytics", "customers", "inventory", "orders", "products"];

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir)) {
    const full = path.join(dir, entry);
    const stat = fs.statSync(full);
    if (stat.isDirectory()) {
      out.push(...walk(full));
    } else if (entry.endsWith(".tool.ts")) {
      out.push(full);
    }
  }
  return out;
}

function ascii(value) {
  return String(value || "")
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"')
    .replace(/[\u2013\u2014]/g, "-")
    .replace(/\s+/g, " ")
    .trim();
}

function dq(value) {
  return JSON.stringify(ascii(value));
}

function title(value) {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function yamlArray(values) {
  return `[${values.map(dq).join(", ")}]`;
}

function propSchema(field, snippet) {
  let schema = { type: "string" };
  const enumMatch = snippet.match(/z\.enum\(\[([^\]]+)\]/);
  if (enumMatch) {
    schema = {
      type: "string",
      enum: [...enumMatch[1].matchAll(/["']([^"']+)["']/g)].map((match) => match[1]),
    };
  } else if (/z\s*\.\s*array\(\s*z\s*\.\s*string/.test(snippet)) {
    schema = { type: "array", items: { type: "string" } };
  } else if (/z\s*\.\s*array\(\s*z\s*\.\s*object/.test(snippet)) {
    schema = { type: "array", items: { type: "object", additionalProperties: true } };
  } else if (/z\s*\.\s*coerce\s*\.\s*number|z\s*\.\s*number/.test(snippet)) {
    schema = {
      type:
        field === "limit" ||
        field === "quantity" ||
        field === "threshold" ||
        field === "days_of_stock_threshold"
          ? "integer"
          : "number",
    };
  } else if (/z\s*\.\s*object/.test(snippet)) {
    schema = { type: "object", additionalProperties: true };
  }

  if (field.endsWith("date")) {
    schema.type = "string";
    schema.pattern = "^\\d{4}-\\d{2}-\\d{2}$";
  }

  const desc = snippet.match(/\.describe\((?:["'])([^"']+)/);
  if (desc) {
    schema.description = ascii(desc[1]);
  }

  if (field === "limit") {
    const min = snippet.match(/\.min\((\d+)/);
    const max = snippet.match(/\.max\((\d+)/);
    if (min) schema.minimum = Number(min[1]);
    if (max) schema.maximum = Number(max[1]);
  }

  return schema;
}

function extractInput(source) {
  const marker = "input: {";
  const start = source.indexOf(marker);
  if (start < 0) {
    return { properties: {}, required: [] };
  }

  let index = start + marker.length;
  let depth = 1;
  for (; index < source.length; index += 1) {
    const char = source[index];
    if (char === "{") depth += 1;
    if (char === "}") {
      depth -= 1;
      if (depth === 0) break;
    }
  }

  const block = source.slice(start + marker.length, index);
  const matches = [...block.matchAll(/^\t\t([a-zA-Z_][\w]*)\s*:/gm)];
  const properties = {};
  const required = [];

  matches.forEach((match, idx) => {
    const name = match[1];
    const from = match.index;
    const to = idx + 1 < matches.length ? matches[idx + 1].index : block.length;
    const snippet = block.slice(from, to);
    properties[name] = propSchema(name, snippet);
    if (!/\.optional\(\)|\.default\(/.test(snippet)) {
      required.push(name);
    }
  });

  return { properties, required };
}

function descriptionFrom(source) {
  const singleLine = source.match(/description:\s*(["'`])([\s\S]*?)\1/);
  if (singleLine) {
    return singleLine[2];
  }
  const multiLine = source.match(/description:\s*([\s\S]*?),\n\s*scopes:/);
  if (!multiLine) {
    return "";
  }
  return multiLine[1].replace(/["'`]/g, "");
}

const tools = walk(sourceRoot)
  .map((filePath) => {
    const source = fs.readFileSync(filePath, "utf8");
    const rel = path.relative(sourceRoot, filePath);
    const pick = (regex) => source.match(regex)?.[1];
    const name = pick(/name:\s*["']([^"']+)/);
    const domain = pick(/domain:\s*["']([^"']+)/);
    const scopes =
      pick(/scopes:\s*\[([^\]]*)\]/)
        ?.match(/["']([^"']+)["']/g)
        ?.map((value) => value.slice(1, -1)) || [];
    const graphql = [...source.matchAll(/from\s+["'](\.\/[\w-]+\.graphql)["']/g)].map((match) =>
      match[1].slice(2),
    );
    let mode = "graphql";
    if (domain === "analytics" && graphql.length === 0) mode = "shopifyql";
    if (name === "manage_product_tags") mode = "tag_mutations";
    if (name === "inventory_risk_report") mode = "inventory_risk";

    const tool = {
      rel,
      name,
      domain,
      description: ascii(descriptionFrom(source)),
      scopes,
      graphql,
      input: extractInput(source),
      mode,
    };
    if (tool.name === "create_collection") {
      delete tool.input.properties.collection_type;
    }
    if (tool.name === "shopifyql_query") {
      return null;
    }
    applyStackOSOverrides(tool);
    return tool;
  })
  .filter(Boolean)
  .sort((left, right) => domains.indexOf(left.domain) - domains.indexOf(right.domain) || left.name.localeCompare(right.name));

function addEnum(schema, values) {
  if (schema) schema.enum = values;
}

function addInteger(schema, description) {
  if (schema) {
    schema.type = "integer";
    schema.description = description;
  }
}

function applyStackOSOverrides(tool) {
  if (tool.name === "conversion_funnel") {
    tool.description =
      "Conversion funnel metrics: sessions, orders, customers, and ShopifyQL conversion_rate (values in shop currency)";
  }
  if (tool.name === "customer_lifetime_value") {
    tool.description =
      "Customer lifetime value report from the ShopifyQL customers dataset showing customer_name, total_amount_spent, and total_orders. Note: groups by customer_name, which may not be unique across customers with the same name.";
  }
  if (tool.name === "inventory_risk_report") {
    tool.scopes = ["read_inventory", "read_products", "read_orders"];
  }
  if (tool.name === "refund_rate_summary") {
    tool.description =
      "Sales reversal summary for a date range using documented Shopify sales metrics (orders, sales_reversals, gross_sales, net_sales, discounts; values in shop currency). Does not compute a physical-return rate.";
  }
  if (tool.name === "sales_comparison") {
    addEnum(tool.input.properties.compare_to, ["previous_period", "previous_year"]);
    addEnum(tool.input.properties.group_by, ["day", "week", "month"]);
  }
  if (tool.name === "get_customer_lifetime_value") {
    tool.description =
      "Get customer amountSpent, numberOfOrders, and first/last visible order dates. True first/last order history can require Shopify read_all_orders access beyond the default order window.";
    tool.scopes = ["read_customers", "read_orders"];
  }
  if (tool.name === "get_customer_orders") {
    tool.description =
      "List visible orders for a specific customer. Historical orders beyond Shopify's default order window can require read_all_orders access.";
    tool.scopes = ["read_customers", "read_orders"];
  }
  if (tool.name === "adjust_inventory") {
    addInteger(
      tool.input.properties.delta,
      "Quantity change - positive to add, negative to subtract",
    );
    tool.input.properties.change_from_quantity = {
      type: "integer",
      description:
        "Optional compare-and-swap expected current quantity. Omit to send explicit null and skip the CAS check.",
    };
  }
  if (tool.name === "set_inventory_level") {
    tool.input.properties.change_from_quantity = {
      type: "integer",
      description:
        "Optional compare-and-swap expected current quantity. Omit to send explicit null and skip the CAS check.",
    };
  }
  if (tool.name === "get_order" || tool.name === "get_order_by_name") {
    tool.scopes = ["read_orders", "read_customers"];
  }
  if (tool.name === "get_order") {
    tool.description =
      "Get a single order by ID with customer, addresses, notes, tags, fulfillments, and the first 50 line items plus pagination metadata.";
  }
  if (tool.name === "get_order_by_name") {
    tool.description =
      'Get an order by its exact Shopify order name (for example, #1001 or a custom prefixed name). Uses Shopify order search by name.';
  }
  if (tool.name === "get_order_fulfillment_status") {
    tool.scopes = ["read_orders"];
  }
  if (tool.name === "list_orders" || tool.name === "search_orders") {
    tool.scopes = ["read_orders", "read_customers"];
  }
  if (tool.name === "mark_order_paid") {
    tool.description =
      "Mark an order as paid using the orderMarkAsPaid mutation. Shopify also requires the acting staff user to have the mark_orders_as_paid permission.";
  }
  if (tool.name === "update_order_tags") {
    tool.description =
      "Set or replace tags on an order using orderUpdate; supplied tags overwrite the order's tag list.";
  }
  if (tool.name === "get_product") {
    tool.description =
      "Get a single product by its Shopify GID. Returns product detail with first 10 media and first 100 variants plus pagination metadata.";
  }
  if (tool.name === "get_product_by_handle") {
    tool.description =
      "Get a product by its URL handle (slug). Returns product detail with first 10 media and first 100 variants plus pagination metadata.";
  }
  if (tool.name === "list_product_variants") {
    tool.description =
      "List variants for a product with cursor pagination. Returns variant details including price, SKU, inventory, and selected options.";
    if (tool.input.properties.limit) tool.input.properties.limit.maximum = 250;
  }
  if (
    ["create_product", "list_products", "update_product", "update_product_status"].includes(
      tool.name,
    )
  ) {
    addEnum(tool.input.properties.status, ["ACTIVE", "DRAFT", "ARCHIVED", "UNLISTED"]);
  }
  if (tool.name === "update_product_status") {
    tool.description = "Change a product's status to ACTIVE, DRAFT, ARCHIVED, or UNLISTED.";
  }
}

let yaml = `slug: shopify
name: Shopify
version: 0.1.0
description: Shopify Admin GraphQL provider with a curated, static action catalog for commerce agents.
display_order: 46
source: builtin
ui:
  nav:
    section: Shopify
capabilities:
`;

for (const domain of domains) {
  const description =
    domain === "analytics"
      ? "Shopify analytics and reporting operations backed by ShopifyQL or Admin GraphQL."
      : `Shopify ${domain} Admin GraphQL operations.`;
  yaml += `  - key: ${domain}
    name: ${title(domain)}
    description: ${description}
    kind: integration
`;
}

yaml += `providers:
  - key: shopify
    name: Shopify Admin API
    description: Static-token Shopify Admin GraphQL provider. Tokens stay daemon-held; agents can only run curated action catalog operations.
    auth_type: api-token
    auth_methods:
      - key: admin-api-token
        label: Shopify Admin API access token
        auth_type: api-token
        payload_format: raw
        payload_field: admin_api_access_token
        fields:
          - key: admin_api_access_token
            label: Admin API access token
            type: secret
            secret: true
            required: true
          - key: store_domain
            label: Store domain
            type: text
            secret: false
            required: true
            placeholder: example.myshopify.com
            description: The store myshopify.com domain used in the Admin GraphQL endpoint.
          - key: api_version
            label: Admin API version
            type: text
            secret: false
            required: false
            placeholder: 2026-07
            description: Defaults to the official 2026-07 stable Admin API version.
    config:
      default_api_version: 2026-07
      endpoint_template: https://{store_domain}/admin/api/{api_version}/graphql.json
      connection_category: Commerce
      setup_note: Store an existing Shopify Admin API access token and the store myshopify.com domain. StackOS does not create tokens or run OAuth for this provider.
      setup:
        credential_label: Shopify Admin API access token
        setup_note: Use an existing token from a Shopify custom app with only the scopes needed for the enabled actions.
        homepage_url: https://shopify.dev/docs/api/admin-graphql
        console_url: https://admin.shopify.com/
        api_key_url: https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens
        docs_url: https://shopify.dev/docs/api/admin-graphql/latest
        support_url: https://help.shopify.com/
        fallback_url: https://shopify.dev/docs/api/admin-graphql/latest
        fallback_reason: Static-token setup requires an operator-supplied Admin API access token; OAuth/token acquisition is deliberately out of scope.
        verified_at: "2026-07-08"
        url_confidence:
          homepage_url: verified
          console_url: directional
          api_key_url: verified
          docs_url: verified
          support_url: verified
          fallback_url: verified
resources:
  - key: admin-graphql-action
    name: Shopify Admin GraphQL Action
    description: Static metadata for one curated Shopify action generated from the cob-shopify-mcp catalog and executed through the local StackOS Shopify connector.
    schema:
      type: object
      additionalProperties: true
      required:
        - action_name
        - domain
        - scopes
      properties:
        action_name:
          type: string
        domain:
          type: string
        scopes:
          type: array
          items:
            type: string
        graphql_file:
          type: string
        mode:
          type: string
actions:
`;

for (const tool of tools) {
  const risk = tool.scopes.some((scope) => scope.startsWith("write_")) ? "write" : "read";
  yaml += `  - key: ${tool.name}
    name: ${dq(title(tool.name))}
    description: ${dq(tool.description)}
    provider: shopify
    capability: ${tool.domain}
    risk_level: ${risk}
    input_schema:
      type: object
      additionalProperties: false
`;
  if (tool.input.required.length) {
    yaml += "      required:\n";
    for (const field of tool.input.required) {
      yaml += `        - ${field}\n`;
    }
  }
  yaml += "      properties:\n";
  for (const [field, schema] of Object.entries(tool.input.properties)) {
    yaml += `        ${field}:\n          type: ${schema.type}\n`;
    if (schema.description) yaml += `          description: ${dq(schema.description)}\n`;
    if (schema.pattern) yaml += `          pattern: ${dq(schema.pattern)}\n`;
    if (schema.minimum !== undefined) yaml += `          minimum: ${schema.minimum}\n`;
    if (schema.maximum !== undefined) yaml += `          maximum: ${schema.maximum}\n`;
    if (schema.enum) {
      yaml += "          enum:\n";
      for (const value of schema.enum) {
        yaml += `            - ${dq(value)}\n`;
      }
    }
    if (schema.type === "array") {
      yaml += `          items:\n            type: ${schema.items?.type || "object"}\n`;
      if (schema.items?.additionalProperties) yaml += "            additionalProperties: true\n";
    }
    if (schema.type === "object" && schema.additionalProperties) {
      yaml += "          additionalProperties: true\n";
    }
  }
  yaml += `    output_schema:
      type: object
      additionalProperties: true
    config:
      schema_version: stackos.action.v1
      connector: shopify
      operation: admin.graphql
      requires_credential: true
      shopify:
        action_name: ${tool.name}
        domain: ${tool.domain}
        mode: ${tool.mode}
        scopes: ${yamlArray(tool.scopes)}
        source: cob-shopify-mcp
        source_path: ${dq(`src/shopify/tools/${tool.rel}`)}
`;
  if (tool.mode === "shopifyql") {
    yaml += "        graphql_file: graphql/shopifyql.graphql\n";
  } else if (tool.graphql[0]) {
    yaml += `        graphql_file: ${dq(`graphql/${tool.domain}/${tool.graphql[0]}`)}\n`;
  }
}

yaml += `config:
  catalog_source:
    name: cob-shopify-mcp
    license: MIT
    extracted_at: "2026-07-08"
    tool_count: ${tools.length}
  official_docs:
    admin_graphql: https://shopify.dev/docs/api/admin-graphql/latest
    access_scopes: https://shopify.dev/docs/api/usage/access-scopes
    versioning: https://shopify.dev/docs/api/usage/versioning
    limits: https://shopify.dev/docs/api/usage/limits
`;

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, yaml, "utf8");
console.log(`wrote ${outputPath} actions=${tools.length}`);
