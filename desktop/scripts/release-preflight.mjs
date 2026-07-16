import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pkg = JSON.parse(fs.readFileSync(path.join(desktopDir, "package.json"), "utf8"));
const updateUrl = process.env.STACKOS_UPDATE_URL || "";
const requireUpdateUrl = process.env.STACKOS_REQUIRE_UPDATE_URL === "1";

function hasEnv(name) {
  return Boolean(process.env[name] && process.env[name].trim() !== "");
}

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: desktopDir,
    encoding: "utf8",
    shell: false,
    ...options
  });
}

function commandExists(command) {
  const result = run("/usr/bin/env", ["sh", "-c", `command -v ${command} >/dev/null 2>&1`]);
  return result.status === 0;
}

function status(ok) {
  return ok ? "ok" : "missing";
}

function isLocalhost(hostname) {
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname);
}

function parseUpdateUrl() {
  if (!updateUrl) {
    return null;
  }
  try {
    return new URL(updateUrl);
  } catch (_error) {
    return "invalid";
  }
}

function updateUrlLooksLikeFile(parsed) {
  const basename = path.posix.basename(parsed.pathname || "").toLowerCase();
  return (
    /^latest(?:-[a-z0-9]+)?\.ya?ml$/.test(basename) ||
    /\.(?:dmg|zip|blockmap)$/.test(basename)
  );
}

function developerIdIdentities() {
  const result = run("security", ["find-identity", "-v", "-p", "codesigning"]);
  if (result.status !== 0) {
    return [];
  }
  const identities = [];
  for (const line of result.stdout.split(/\r?\n/)) {
    const match = line.match(/"Developer ID Application: ([^"]+)"/);
    if (match) {
      identities.push(match[1]);
    }
  }
  return identities;
}

function configuredTargetArches() {
  const targets = pkg.build?.mac?.target || [];
  const arches = new Set();
  for (const target of targets) {
    if (typeof target === "string") {
      if (target === "dmg" || target === "zip") {
        arches.add(process.arch === "x64" ? "x64" : "arm64");
      }
      continue;
    }
    for (const arch of target.arch || []) {
      arches.add(arch);
    }
  }
  return [...arches].sort();
}

function signingStatus() {
  const identities = developerIdIdentities();
  const cscName = hasEnv("CSC_NAME");
  const cscLink = hasEnv("CSC_LINK");
  const cscPassword = hasEnv("CSC_KEY_PASSWORD");
  const autoDiscovery = process.env.STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY === "1";
  const issues = [];
  if (cscPassword && !cscLink) {
    issues.push("CSC_KEY_PASSWORD is set but CSC_LINK is unset");
  }
  if (cscName && /^Developer ID Application:/i.test(process.env.CSC_NAME.trim())) {
    issues.push('CSC_NAME should omit the "Developer ID Application:" prefix');
  }
  if (autoDiscovery && !cscName && !cscLink && identities.length !== 1) {
    issues.push(
      `auto-discovery requires exactly one visible Developer ID Application identity; found ${identities.length}`
    );
  }
  const configured = cscName || cscLink || autoDiscovery;
  return {
    configured,
    explicit: cscName || cscLink,
    cscName,
    cscLink,
    autoDiscovery,
    identities,
    issues
  };
}

function notarizationStatus() {
  const apiNames = ["APPLE_API_KEY", "APPLE_API_KEY_ID", "APPLE_API_ISSUER"];
  const appleIdNames = ["APPLE_ID", "APPLE_APP_SPECIFIC_PASSWORD", "APPLE_TEAM_ID"];
  const apiSet = apiNames.filter(hasEnv);
  const appleIdSet = appleIdNames.filter(hasEnv);
  const keychainProfile = hasEnv("APPLE_KEYCHAIN_PROFILE");
  const methods = [];
  const issues = [];
  if (apiSet.length > 0 && apiSet.length < apiNames.length) {
    issues.push(`App Store Connect API key method is partial; set ${apiNames.join(", ")}`);
  }
  if (apiSet.length === apiNames.length) {
    methods.push("api-key");
  }
  if (appleIdSet.length > 0 && appleIdSet.length < appleIdNames.length) {
    issues.push(`Apple ID method is partial; set ${appleIdNames.join(", ")}`);
  }
  if (appleIdSet.length === appleIdNames.length) {
    methods.push("apple-id");
  }
  if (keychainProfile) {
    methods.push("keychain-profile");
  }
  if (methods.length > 1) {
    issues.push(`multiple notarization methods configured: ${methods.join(", ")}`);
  }
  return { configured: methods.length === 1, methods, issues };
}

function validateNotaryCredentials(method) {
  if (process.env.STACKOS_PREFLIGHT_VALIDATE_NOTARY !== "1" || !method) {
    return null;
  }
  const args = ["notarytool", "history", "--output-format", "json"];
  if (method === "api-key") {
    args.push(
      "--key",
      process.env.APPLE_API_KEY,
      "--key-id",
      process.env.APPLE_API_KEY_ID,
      "--issuer",
      process.env.APPLE_API_ISSUER
    );
  } else if (method === "apple-id") {
    args.push(
      "--apple-id",
      process.env.APPLE_ID,
      "--password",
      process.env.APPLE_APP_SPECIFIC_PASSWORD,
      "--team-id",
      process.env.APPLE_TEAM_ID
    );
  } else if (method === "keychain-profile") {
    if (hasEnv("APPLE_KEYCHAIN")) {
      args.push("--keychain", process.env.APPLE_KEYCHAIN);
    }
    args.push("--keychain-profile", process.env.APPLE_KEYCHAIN_PROFILE);
  }
  const result = run("xcrun", args, { timeout: 60000 });
  return {
    ok: result.status === 0,
    status: result.status,
    detail: result.status === 0 ? "notarytool history succeeded" : "notarytool history failed"
  };
}

function printList(label, values) {
  if (values.length === 0) {
    console.log(`  ${label}: none`);
    return;
  }
  console.log(`  ${label}:`);
  for (const value of values) {
    console.log(`    - ${value}`);
  }
}

const requiredTools = ["bash", "rsync", "uv", "file", "otool", "codesign", "security", "xcrun"];
const signing = signingStatus();
const notarization = notarizationStatus();
const parsedUpdateUrl = parseUpdateUrl();
const releaseUpdateUrl =
  parsedUpdateUrl && parsedUpdateUrl !== "invalid"
    ? parsedUpdateUrl.protocol === "https:" && !isLocalhost(parsedUpdateUrl.hostname)
    : false;
const activeNotaryMethod = notarization.methods.length === 1 ? notarization.methods[0] : null;
const notaryValidation = validateNotaryCredentials(activeNotaryMethod);
const arches = configuredTargetArches();
const missingTools = requiredTools.filter((tool) => !commandExists(tool));
const issues = [
  ...missingTools.map((tool) => `${tool} is not on PATH`),
  ...signing.issues,
  ...notarization.issues
];

if (!signing.configured) {
  issues.push("signing is not configured; set CSC_NAME, CSC_LINK/CSC_KEY_PASSWORD, or STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY=1");
}
if (!notarization.configured) {
  issues.push("notarization is not configured; set APPLE_API_KEY trio, APPLE_ID trio, or APPLE_KEYCHAIN_PROFILE");
}
if (requireUpdateUrl) {
  if (!parsedUpdateUrl) {
    issues.push("STACKOS_UPDATE_URL is not configured");
  } else if (parsedUpdateUrl === "invalid") {
    issues.push("STACKOS_UPDATE_URL is invalid");
  } else if (updateUrlLooksLikeFile(parsedUpdateUrl)) {
    issues.push("STACKOS_UPDATE_URL must be the base directory containing latest-mac.yml, not an artifact or metadata file");
  } else if (!releaseUpdateUrl) {
    issues.push("STACKOS_UPDATE_URL must be non-localhost HTTPS for public release");
  }
  if (!signing.cscName) {
    issues.push("public release signing must be pinned with CSC_NAME");
  }
}
if (notaryValidation && !notaryValidation.ok) {
  issues.push("notarytool credential validation failed");
}

console.log("StackOS desktop release preflight");
console.log(`version: ${pkg.version}`);
console.log(`targetArches: ${arches.join(", ") || "none"}`);
console.log(`updateUrl: ${updateUrl ? "configured" : "missing"}`);
console.log(`publicRelease: ${requireUpdateUrl ? "yes" : "no"}`);
console.log("");
console.log("Build tools:");
for (const tool of requiredTools) {
  console.log(`  ${tool}: ${status(commandExists(tool))}`);
}
console.log("");
console.log("Signing:");
console.log(`  CSC_NAME: ${status(signing.cscName)}`);
console.log(`  CSC_LINK: ${status(signing.cscLink)}`);
console.log(`  autoDiscovery: ${signing.autoDiscovery ? "enabled" : "disabled"}`);
printList("visible Developer ID Application identities", signing.identities);
console.log("");
console.log("Notarization:");
console.log(`  APPLE_API_KEY trio: ${status(notarization.methods.includes("api-key"))}`);
console.log(`  APPLE_ID trio: ${status(notarization.methods.includes("apple-id"))}`);
console.log(`  APPLE_KEYCHAIN_PROFILE: ${status(notarization.methods.includes("keychain-profile"))}`);
if (notaryValidation) {
  console.log(`  credentialValidation: ${notaryValidation.ok ? "ok" : `failed (${notaryValidation.status})`}`);
}
console.log("");
console.log("Commands:");
console.log("  dev unsigned: pnpm --dir desktop run dist:mac:dev");
console.log("  signed local: pnpm --dir desktop run dist:mac:signed");
console.log("  signed local pinned: CSC_NAME='Name (TEAMID)' pnpm --dir desktop run dist:mac:signed");
console.log("  notarized release: CSC_NAME='Name (TEAMID)' APPLE_KEYCHAIN_PROFILE='profile' STACKOS_UPDATE_URL='https://stackos.flowmonkey.io/StackOS/' pnpm --dir desktop run dist:mac:release");
console.log("  validate notary profile: STACKOS_PREFLIGHT_VALIDATE_NOTARY=1 CSC_NAME='Name (TEAMID)' APPLE_KEYCHAIN_PROFILE='profile' STACKOS_UPDATE_URL='https://stackos.flowmonkey.io/StackOS/' pnpm --dir desktop run release:preflight");
console.log("");

if (issues.length > 0) {
  console.error("Release preflight failed:");
  for (const issue of issues) {
    console.error(`- ${issue}`);
  }
  process.exit(1);
}

console.log("Release preflight ok");
