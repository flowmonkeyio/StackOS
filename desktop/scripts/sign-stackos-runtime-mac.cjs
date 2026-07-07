"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const desktopDir = path.resolve(__dirname, "..");

function isTruthy(value) {
  return ["1", "true", "yes", "on"].includes(String(value || "").toLowerCase());
}

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

function runCapture(command, args) {
  const result = spawnSync(command, args, {
    cwd: desktopDir,
    encoding: "utf8",
    shell: false
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    return "";
  }
  return `${result.stdout || ""}${result.stderr || ""}`;
}

function signingIdentity() {
  if (isTruthy(process.env.STACKOS_UNSIGNED_DEV)) {
    return null;
  }
  const cscName = String(process.env.CSC_NAME || "").trim();
  if (cscName) {
    return /^Developer ID Application:/i.test(cscName)
      ? cscName
      : `Developer ID Application: ${cscName}`;
  }
  const canDiscover =
    process.env.CSC_LINK || String(process.env.STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY || "") === "1";
  if (canDiscover) {
    const identities = developerIdIdentities();
    if (identities.length === 1) {
      return identities[0];
    }
    throw new Error(
      `expected exactly one Developer ID Application identity for auto-discovery, found ${identities.length}`
    );
  }
  return null;
}

function developerIdIdentities() {
  const output = runCapture("security", ["find-identity", "-v", "-p", "codesigning"]);
  const identities = [];
  for (const line of output.split(/\r?\n/)) {
    const match = line.match(/"([^"]*Developer ID Application:[^"]+)"/);
    if (match) {
      identities.push(match[1]);
    }
  }
  return identities;
}

function walkFiles(root) {
  const out = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const fullPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      walkFiles(fullPath).forEach((filePath) => out.push(filePath));
    } else if (entry.isFile()) {
      out.push(fullPath);
    }
  }
  return out;
}

function isMachO(filePath) {
  const output = runCapture("file", [filePath]);
  return /Mach-O/.test(output);
}

function bundledMachOFiles(stackosRoot) {
  return walkFiles(stackosRoot)
    .filter((filePath) => isMachO(filePath))
    .sort((a, b) => b.length - a.length);
}

module.exports = async function signStackosRuntime(context) {
  if (context.electronPlatformName !== "darwin") {
    return;
  }

  const pkg = JSON.parse(fs.readFileSync(path.join(desktopDir, "package.json"), "utf8"));
  const productName = pkg.build?.productName || pkg.productName || pkg.name;
  const appPath = path.join(context.appOutDir, `${productName}.app`);
  const stackosRoot = path.join(appPath, "Contents", "Resources", "stackos");
  const pythonPath = path.join(stackosRoot, ".venv", "bin", "python");
  const pythonLibDir = path.join(stackosRoot, ".venv", "lib");
  const pythonLibs = fs.existsSync(pythonLibDir)
    ? fs.readdirSync(pythonLibDir).filter((entry) => /^libpython.*\.dylib$/.test(entry))
    : [];

  if (!fs.existsSync(pythonPath) && !fs.existsSync(stackosRoot)) {
    return;
  }
  if (!fs.existsSync(pythonPath)) {
    throw new Error(`packaged Python executable is missing at ${pythonPath}`);
  }
  if (pythonLibs.length !== 1) {
    throw new Error(`expected exactly one packaged libpython dylib, found ${pythonLibs.length}`);
  }

  const identity = signingIdentity();
  if (!identity) {
    return;
  }

  for (const filePath of bundledMachOFiles(stackosRoot)) {
    run("codesign", ["--force", "--sign", identity, "--timestamp", "--options", "runtime", filePath]);
  }
};
