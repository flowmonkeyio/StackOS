"use strict";

const { spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
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

function nestedBundleDirectories(root, outerBundle) {
  const bundles = [];
  for (const filePath of walkFiles(root)) {
    let candidate = path.dirname(filePath);
    while (candidate.startsWith(root) && candidate !== outerBundle) {
      if (/\.(app|framework|xpc|appex)$/i.test(candidate)) {
        bundles.push(candidate);
      }
      const parent = path.dirname(candidate);
      if (parent === candidate) {
        break;
      }
      candidate = parent;
    }
  }
  return [...new Set(bundles)].sort((a, b) => b.length - a.length);
}

function browserAppBundles(stackosRoot) {
  const browsersRoot = path.join(stackosRoot, "browser-runtime", "browsers");
  if (!fs.existsSync(browsersRoot)) {
    return [];
  }
  const apps = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (!entry.isDirectory()) {
        continue;
      }
      const child = path.join(directory, entry.name);
      if (/\.app$/i.test(entry.name)) {
        apps.push(child);
      }
      visit(child);
    }
  };
  visit(browsersRoot);
  return apps.sort((a, b) => b.length - a.length);
}

function browserSigningTargets(appBundle) {
  return [
    ...bundledMachOFiles(appBundle),
    ...nestedBundleDirectories(appBundle, appBundle),
    appBundle
  ];
}

function preservedEntitlements(target, temporaryDir, index) {
  const output = runCapture("codesign", ["-d", "--entitlements", ":-", target]);
  const start = output.indexOf("<?xml");
  const end = output.indexOf("</plist>");
  if (start === -1 || end === -1) {
    return null;
  }
  const destination = path.join(temporaryDir, `browser-entitlements-${index}.plist`);
  fs.writeFileSync(destination, output.slice(start, end + "</plist>".length));
  return destination;
}

function signTarget(identity, target, entitlementsPath = null) {
  const args = ["--force", "--sign", identity, "--timestamp", "--options", "runtime"];
  if (entitlementsPath) {
    args.push("--entitlements", entitlementsPath);
  }
  args.push(target);
  run("codesign", args);
}

function refreshTdlibLibraryDigest(stackosRoot) {
  const runtimeRoot = path.join(stackosRoot, "telegram-tdlib-runtime");
  const manifestPath = path.join(runtimeRoot, "manifest.json");
  if (!fs.existsSync(runtimeRoot)) {
    throw new Error(`packaged TDLib runtime is missing at ${runtimeRoot}`);
  }
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`packaged TDLib runtime manifest is missing at ${manifestPath}`);
  }
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch (error) {
    throw new Error(`packaged TDLib runtime manifest is invalid: ${error.message}`);
  }
  const relativeLibraryPath = manifest?.library?.path;
  if (relativeLibraryPath !== "lib/libtdjson.dylib") {
    throw new Error("packaged TDLib runtime manifest has an unexpected library path");
  }
  const libraryPath = path.resolve(runtimeRoot, relativeLibraryPath);
  if (!libraryPath.startsWith(`${runtimeRoot}${path.sep}`) || !fs.statSync(libraryPath).isFile()) {
    throw new Error(`packaged TDLib library is missing at ${libraryPath}`);
  }
  manifest.library.sha256 = crypto.createHash("sha256").update(fs.readFileSync(libraryPath)).digest("hex");
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
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

  const appBundles = browserAppBundles(stackosRoot);
  const protectedRoots = appBundles.map((appBundle) => `${appBundle}${path.sep}`);
  const bunPath = path.join(stackosRoot, "browser-runtime", "bin", "bun");
  const browsePath = path.join(stackosRoot, "browser-runtime", "gstack", "browse", "dist", "browse");
  const jitEntitlements = path.join(__dirname, "bun-entitlements.plist");
  if (!fs.existsSync(bunPath) || !fs.existsSync(browsePath)) {
    throw new Error("packaged gstack Bun and browse executables are missing");
  }
  if (!fs.existsSync(jitEntitlements)) {
    throw new Error(`Bun hardened-runtime entitlements are missing at ${jitEntitlements}`);
  }

  for (const filePath of bundledMachOFiles(stackosRoot).filter(
    (filePath) =>
      filePath !== bunPath &&
      filePath !== browsePath &&
      !protectedRoots.some((root) => filePath.startsWith(root))
  )) {
    signTarget(identity, filePath);
  }
  signTarget(identity, bunPath, jitEntitlements);
  signTarget(identity, browsePath, jitEntitlements);

  const temporaryDir = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-browser-entitlements-"));
  try {
    let index = 0;
    for (const appBundle of appBundles) {
      for (const target of browserSigningTargets(appBundle)) {
        signTarget(identity, target, preservedEntitlements(target, temporaryDir, index));
        index += 1;
      }
    }
  } finally {
    fs.rmSync(temporaryDir, { recursive: true, force: true });
  }

  // codesign changes libtdjson's bytes. Refresh the payload's integrity
  // manifest before electron-builder signs the enclosing app bundle.
  refreshTdlibLibraryDigest(stackosRoot);
};

module.exports.refreshTdlibLibraryDigest = refreshTdlibLibraryDigest;
