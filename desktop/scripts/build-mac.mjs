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

const pkg = JSON.parse(fs.readFileSync(packagePath, "utf8"));
const config = { ...pkg.build };

function isLocalhost(hostname) {
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname);
}

function validateUpdateUrl(url) {
  if (!url) {
    return;
  }
  let parsed;
  try {
    parsed = new URL(url);
  } catch (_error) {
    console.error("STACKOS_UPDATE_URL must be a valid URL");
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

validateUpdateUrl(updateUrl);

if (updateUrl) {
  fs.writeFileSync(
    generatedUpdateConfigPath,
    `${JSON.stringify({ updateUrl }, null, 2)}\n`
  );
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
  fs.writeFileSync(generatedConfigPath, `${JSON.stringify(config, null, 2)}\n`);
}

if (dryRun) {
  console.log("desktop mac build config dry run ok");
  console.log(`updateUrl=${updateUrl || ""}`);
  console.log(`generatedConfig=${updateUrl ? generatedConfigPath : ""}`);
  console.log(`generatedUpdateConfig=${updateUrl ? generatedUpdateConfigPath : ""}`);
  process.exit(0);
}

const command = process.platform === "win32" ? "electron-builder.cmd" : "electron-builder";
const args = ["--mac"];
if (updateUrl) {
  args.push("--config", generatedConfigPath);
}

runStep(process.execPath, ["scripts/build-icons.mjs"]);
runStep(command, args);
