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
const license = path.join(repoRoot, "stackos", "browser", "CHROMIUM-LICENSE");

assert.match(payload, /ensure_chromium_runtime/);
assert.doesNotMatch(payload, /playwright install chromium/);
assert.doesNotMatch(payload, /PLAYWRIGHT_BROWSERS_PATH/);
assert.doesNotMatch(payload, /Chrome for Testing/);
assert.doesNotMatch(service, /PLAYWRIGHT_BROWSERS_PATH/);
assert.match(signer, /bundledMachOFiles/);
assert.match(signer, /chromiumSigningTargets/);
assert.match(signer, /nestedBundleDirectories/);
assert.match(signer, /Chromium\.app/);
assert.match(signer, /\.\.\.nestedBundleDirectories\(chromiumApp, chromiumApp\)/);
assert.match(signer, /codesign/);
assert.match(runtime, /packaged_stackos_root/);
assert.match(runtime, /Chromium\.app/);
assert.match(runtime, /executable_path=str\(executable_path\)/);
assert.doesNotMatch(runtime, /playwright_chromium_executable_path/);
assert.ok(fs.existsSync(license), "Chromium license must ship beside the runtime");

console.log("desktop browser runtime contract test ok");
