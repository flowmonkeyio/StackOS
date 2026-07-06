"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const desktopDir = path.resolve(__dirname, "..");

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: desktopDir,
    stdio: "inherit",
    shell: false
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed with ${result.status}`);
  }
}

function signingIdentity() {
  const cscName = String(process.env.CSC_NAME || "").trim();
  if (!cscName) {
    throw new Error("CSC_NAME is required to re-sign the packaged Python runtime");
  }
  return /^Developer ID Application:/i.test(cscName)
    ? cscName
    : `Developer ID Application: ${cscName}`;
}

function findEntitlements() {
  const override = String(process.env.STACKOS_DESKTOP_ENTITLEMENTS || "").trim();
  if (override) {
    return override;
  }

  const pnpmDir = path.join(desktopDir, "node_modules", ".pnpm");
  for (const entry of fs.readdirSync(pnpmDir)) {
    if (!entry.startsWith("app-builder-lib@")) {
      continue;
    }
    const candidate = path.join(
      pnpmDir,
      entry,
      "node_modules",
      "app-builder-lib",
      "templates",
      "entitlements.mac.plist"
    );
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error("Unable to find electron-builder entitlements.mac.plist");
}

module.exports = async function afterSign(context) {
  if (context.electronPlatformName !== "darwin") {
    return;
  }

  const pkg = JSON.parse(fs.readFileSync(path.join(desktopDir, "package.json"), "utf8"));
  const productName = pkg.build?.productName || pkg.productName || pkg.name;
  const appPath = path.join(context.appOutDir, `${productName}.app`);
  const pythonPath = path.join(appPath, "Contents", "Resources", "stackos", ".venv", "bin", "python");
  const pythonLibPath = path.join(
    appPath,
    "Contents",
    "Resources",
    "stackos",
    ".venv",
    "lib",
    "libpython3.12.dylib"
  );

  if (!fs.existsSync(pythonPath) || !fs.existsSync(pythonLibPath)) {
    return;
  }

  const identity = signingIdentity();
  const entitlements = findEntitlements();

  run("codesign", ["--force", "--sign", identity, "--timestamp", "--options", "runtime", pythonLibPath]);
  run("codesign", ["--force", "--sign", identity, "--timestamp", "--options", "runtime", pythonPath]);
  run("codesign", [
    "--force",
    "--sign",
    identity,
    "--timestamp",
    "--options",
    "runtime",
    "--entitlements",
    entitlements,
    appPath
  ]);
  run("codesign", ["--verify", "--deep", "--strict", "--verbose=2", appPath]);
};
