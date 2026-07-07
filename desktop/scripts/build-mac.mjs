import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const packagePath = path.join(desktopDir, "package.json");
const generatedConfigPath = path.join(desktopDir, ".electron-builder.generated.json");
const generatedUpdateConfigPath = path.join(desktopDir, ".update-config.generated.json");
const updateUrl = process.env.STACKOS_UPDATE_URL;
const dryRun = process.env.STACKOS_DESKTOP_BUILD_DRY_RUN === "1";
const dryRunWriteConfig = process.env.STACKOS_DESKTOP_DRY_RUN_WRITE_CONFIG === "1";

const pkg = JSON.parse(fs.readFileSync(packagePath, "utf8"));
const config = JSON.parse(JSON.stringify(pkg.build));

function isTruthy(value) {
  return ["1", "true", "yes", "on"].includes(String(value || "").toLowerCase());
}

function hasEnv(name) {
  return Boolean(process.env[name] && process.env[name].trim() !== "");
}

function isLocalhost(hostname) {
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname);
}

function parseUpdateUrl(url) {
  if (!url) {
    return null;
  }
  try {
    return new URL(url);
  } catch (_error) {
    console.error("STACKOS_UPDATE_URL must be a valid URL");
    process.exit(1);
  }
}

function validateUpdateUrl(parsed) {
  if (!parsed) {
    return;
  }
  if (updateUrlLooksLikeFile(parsed)) {
    console.error("STACKOS_UPDATE_URL must be the base directory containing latest-mac.yml, not an artifact or metadata file");
    process.exit(1);
  }
  if (parsed.protocol === "https:") {
    return;
  }
  if (parsed.protocol === "http:" && isLocalhost(parsed.hostname)) {
    return;
  }
  console.error("STACKOS_UPDATE_URL must use HTTPS unless it is localhost for local testing");
  process.exit(1);
}

function updateUrlLooksLikeFile(parsed) {
  const basename = path.posix.basename(parsed.pathname || "").toLowerCase();
  return (
    /^latest(?:-[a-z0-9]+)?\.ya?ml$/.test(basename) ||
    /\.(?:dmg|zip|blockmap)$/.test(basename)
  );
}

function validateSigningEnv() {
  const hasCscName = hasEnv("CSC_NAME");
  const hasCscLink = hasEnv("CSC_LINK");
  const hasCscPassword = hasEnv("CSC_KEY_PASSWORD");
  const allowAutoDiscovery = isTruthy(process.env.STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY);

  const issues = [];
  if (hasCscPassword && !hasCscLink) {
    issues.push("CSC_KEY_PASSWORD is set but CSC_LINK is missing");
  }
  if (hasCscName && /^Developer ID Application:/i.test(process.env.CSC_NAME.trim())) {
    issues.push('CSC_NAME must omit the "Developer ID Application:" prefix; use "Example Org (ABCDE12345)"');
  }

  const configured = hasCscName || hasCscLink || allowAutoDiscovery;
  return {
    configured,
    explicit: hasCscName || hasCscLink,
    cscName: hasCscName,
    cscLink: hasCscLink,
    issues,
    method: hasCscLink ? "csc-link" : hasCscName ? "csc-name" : allowAutoDiscovery ? "auto-discovery" : "none"
  };
}

function validateNotarizationEnv() {
  const apiKeyNames = ["APPLE_API_KEY", "APPLE_API_KEY_ID", "APPLE_API_ISSUER"];
  const appleIdNames = ["APPLE_ID", "APPLE_APP_SPECIFIC_PASSWORD", "APPLE_TEAM_ID"];
  const keychainProfileNames = ["APPLE_KEYCHAIN_PROFILE"];

  const apiKeySet = apiKeyNames.filter(hasEnv);
  const appleIdSet = appleIdNames.filter(hasEnv);
  const keychainSet = keychainProfileNames.filter(hasEnv);
  const issues = [];
  const completeMethods = [];

  if (apiKeySet.length > 0 && apiKeySet.length < apiKeyNames.length) {
    issues.push(`App Store Connect API key notarization requires ${apiKeyNames.join(", ")}`);
  }
  if (apiKeySet.length === apiKeyNames.length) {
    completeMethods.push("api-key");
  }

  if (appleIdSet.length > 0 && appleIdSet.length < appleIdNames.length) {
    issues.push(`Apple ID notarization requires ${appleIdNames.join(", ")}`);
  }
  if (appleIdSet.length === appleIdNames.length) {
    completeMethods.push("apple-id");
  }

  if (keychainSet.length > 0) {
    completeMethods.push("keychain-profile");
  }

  if (completeMethods.length > 1) {
    issues.push(
      `configure only one notarization method at a time; found ${completeMethods.join(", ")}`
    );
  }

  return {
    configured: completeMethods.length === 1,
    issues,
    method: completeMethods[0] || "none"
  };
}

function failConfig(issues) {
  if (issues.length === 0) {
    return;
  }
  console.error("Desktop mac release configuration is incomplete:");
  for (const issue of issues) {
    console.error(`- ${issue}`);
  }
  process.exit(1);
}

function runStep(command, args) {
  const result = spawnSync(command, args, {
    cwd: desktopDir,
    stdio: "inherit",
    shell: false
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function notarizationArgsForEnv() {
  if (hasEnv("APPLE_API_KEY")) {
    return [
      "--key",
      process.env.APPLE_API_KEY,
      "--key-id",
      process.env.APPLE_API_KEY_ID,
      "--issuer",
      process.env.APPLE_API_ISSUER
    ];
  }

  if (hasEnv("APPLE_ID")) {
    return [
      "--apple-id",
      process.env.APPLE_ID,
      "--password",
      process.env.APPLE_APP_SPECIFIC_PASSWORD,
      "--team-id",
      process.env.APPLE_TEAM_ID
    ];
  }

  const args = [];
  if (hasEnv("APPLE_KEYCHAIN")) {
    args.push("--keychain", process.env.APPLE_KEYCHAIN);
  }
  args.push("--keychain-profile", process.env.APPLE_KEYCHAIN_PROFILE);
  return args;
}

function configuredDmgArches() {
  const targets = config.mac?.target || [];
  const arches = new Set();
  for (const target of targets) {
    if (typeof target === "string") {
      if (target === "dmg") {
        arches.add(process.arch === "x64" ? "x64" : "arm64");
      }
      continue;
    }
    if (target?.target !== "dmg") {
      continue;
    }
    for (const arch of target.arch || []) {
      arches.add(arch);
    }
  }
  return [...arches];
}

function currentDmgArtifacts() {
  const distDir = path.join(desktopDir, "dist");
  const arches = configuredDmgArches();
  if (arches.length === 0) {
    console.error("Desktop mac release has no configured DMG targets");
    process.exit(1);
  }
  const artifacts = arches.map((arch) =>
    path.join(distDir, `stackos-${pkg.version}-mac-${arch}.dmg`)
  );
  const missing = artifacts.filter((artifact) => !fs.existsSync(artifact));
  if (missing.length > 0) {
    console.error("Desktop mac release DMG artifacts are missing:");
    for (const artifact of missing) {
      console.error(`- ${artifact}`);
    }
    process.exit(1);
  }
  return artifacts;
}

function notarizeDmgArtifacts() {
  const authArgs = notarizationArgsForEnv();
  for (const artifact of currentDmgArtifacts()) {
    console.log(`notarizing ${path.basename(artifact)}`);
    runStep("xcrun", [
      "notarytool",
      "submit",
      artifact,
      ...authArgs,
      "--wait",
      "--output-format",
      "json"
    ]);
    runStep("xcrun", ["stapler", "staple", artifact]);
    runStep("xcrun", ["stapler", "validate", artifact]);
  }
}

const parsedUpdateUrl = parseUpdateUrl(updateUrl);
validateUpdateUrl(parsedUpdateUrl);

const updateUrlIsLocal = parsedUpdateUrl ? isLocalhost(parsedUpdateUrl.hostname) : false;
const releaseUpdateUrl = parsedUpdateUrl ? parsedUpdateUrl.protocol === "https:" && !updateUrlIsLocal : false;
const requireSigning = isTruthy(process.env.STACKOS_REQUIRE_SIGNING);
const requireUpdateUrl = isTruthy(process.env.STACKOS_REQUIRE_UPDATE_URL);
const allowUnsignedRelease = isTruthy(process.env.STACKOS_ALLOW_UNSIGNED_RELEASE);
const unsignedDev = isTruthy(process.env.STACKOS_UNSIGNED_DEV);
const skipNotarization = isTruthy(process.env.STACKOS_SKIP_NOTARIZATION);
const releaseIntent = requireSigning || releaseUpdateUrl;
const signing = validateSigningEnv();
const notarization = validateNotarizationEnv();
const configIssues = unsignedDev ? [] : [...signing.issues, ...notarization.issues];

if (unsignedDev && releaseIntent && !allowUnsignedRelease) {
  configIssues.push(
    "STACKOS_UNSIGNED_DEV release-intent builds require STACKOS_ALLOW_UNSIGNED_RELEASE=1"
  );
}
if (requireUpdateUrl) {
  if (!parsedUpdateUrl) {
    configIssues.push("public release builds require STACKOS_UPDATE_URL");
  } else if (!releaseUpdateUrl) {
    configIssues.push("public release builds require a non-localhost HTTPS STACKOS_UPDATE_URL");
  }
}
if (requireUpdateUrl && !signing.cscName) {
  configIssues.push("public release builds require CSC_NAME; auto-discovery is only for local signed smokes");
}

if (releaseIntent && !allowUnsignedRelease) {
  if (!signing.configured) {
    configIssues.push(
      "release builds require CSC_NAME, CSC_LINK/CSC_KEY_PASSWORD, or STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY=1"
    );
  }
  if (!skipNotarization && !notarization.configured) {
    configIssues.push(
      "release builds require notarization credentials or STACKOS_SKIP_NOTARIZATION=1 for an explicit unsigned/notarization-bypassed smoke"
    );
  }
}

failConfig(configIssues);

if (updateUrl) {
  config.extraResources = [
    ...(config.extraResources || []),
    {
      from: ".update-config.generated.json",
      to: "update-config.json"
    }
  ];
  config.publish = [
    {
      provider: "generic",
      url: updateUrl
    }
  ];
}

if (releaseIntent && !allowUnsignedRelease) {
  config.forceCodeSigning = true;
  config.dmg = {
    ...(config.dmg || {}),
    sign: true
  };
}

if (unsignedDev) {
  config.forceCodeSigning = false;
  config.mac = {
    ...(config.mac || {}),
    identity: null,
    notarize: false
  };
  config.dmg = {
    ...(config.dmg || {}),
    sign: false
  };
} else if (skipNotarization) {
  config.mac = {
    ...(config.mac || {}),
    notarize: false
  };
} else if (notarization.configured) {
  config.mac = {
    ...(config.mac || {}),
    notarize: true
  };
}

const dynamicConfigNeeded =
  Boolean(updateUrl) || releaseIntent || unsignedDev || skipNotarization || notarization.configured;

function writeGeneratedConfigFiles() {
  if (updateUrl) {
    fs.writeFileSync(
      generatedUpdateConfigPath,
      `${JSON.stringify({ updateUrl }, null, 2)}\n`
    );
  }
  if (dynamicConfigNeeded) {
    fs.writeFileSync(generatedConfigPath, `${JSON.stringify(config, null, 2)}\n`);
  }
}

if (dryRun) {
  if (dryRunWriteConfig) {
    writeGeneratedConfigFiles();
  }
  console.log("desktop mac build config dry run ok");
  console.log(`updateUrl=${updateUrl || ""}`);
  console.log(`requireUpdateUrl=${requireUpdateUrl ? "true" : "false"}`);
  console.log(`releaseIntent=${releaseIntent ? "true" : "false"}`);
  console.log(`signing=${unsignedDev ? "unsigned-dev" : signing.method}`);
  console.log(`notarization=${unsignedDev ? "disabled" : skipNotarization ? "skipped" : notarization.method}`);
  console.log(`generatedConfig=${dynamicConfigNeeded && dryRunWriteConfig ? generatedConfigPath : ""}`);
  console.log(`generatedUpdateConfig=${updateUrl && dryRunWriteConfig ? generatedUpdateConfigPath : ""}`);
  process.exit(0);
}

const command = process.platform === "win32" ? "electron-builder.cmd" : "electron-builder";
const args = ["--mac"];
if (dynamicConfigNeeded) {
  writeGeneratedConfigFiles();
  args.push("--config", generatedConfigPath);
}

runStep("bash", ["scripts/build-stackos-payload.sh"]);
runStep(process.execPath, ["scripts/build-icons.mjs"]);
runStep(command, args);

if (releaseIntent && !allowUnsignedRelease && !skipNotarization && notarization.configured) {
  notarizeDmgArtifacts();
}
