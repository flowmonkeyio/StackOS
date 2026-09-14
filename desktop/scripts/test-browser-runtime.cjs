"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const desktopDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(desktopDir, "..");
const payload = fs.readFileSync(path.join(desktopDir, "scripts", "build-stackos-payload.sh"), "utf8");
const service = fs.readFileSync(path.join(desktopDir, "src", "service.js"), "utf8");
const signer = fs.readFileSync(
  path.join(desktopDir, "scripts", "sign-stackos-runtime-mac.cjs"),
  "utf8"
);
const runtime = fs.readFileSync(path.join(repoRoot, "stackos", "browser", "runtime.py"), "utf8");
const bunEntitlements = path.join(desktopDir, "scripts", "bun-entitlements.plist");

assert.match(payload, /ensure_gstack_runtime/);
assert.doesNotMatch(payload, /ensure_chromium_runtime/);
assert.doesNotMatch(service, /PLAYWRIGHT_BROWSERS_PATH/);
assert.match(signer, /bundledMachOFiles/);
assert.match(signer, /browserAppBundles/);
assert.match(signer, /browserSigningTargets/);
assert.match(signer, /nestedBundleDirectories/);
assert.match(signer, /preservedEntitlements/);
assert.match(signer, /bun-entitlements\.plist/);
assert.doesNotMatch(signer, /Chromium\.app/);
assert.match(signer, /codesign/);
assert.match(runtime, /packaged_stackos_root/);
assert.match(runtime, /gstack_runtime_root/);
assert.match(runtime, /gstack_executable_path/);
assert.doesNotMatch(runtime, /Chromium\.app/);
assert.ok(fs.existsSync(bunEntitlements), "Bun hardened-runtime entitlements must ship with desktop scripts");

console.log("desktop browser runtime contract test ok");
