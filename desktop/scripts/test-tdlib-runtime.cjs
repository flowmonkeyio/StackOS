"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const desktopDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(desktopDir, "..");
const payload = fs.readFileSync(path.join(desktopDir, "scripts", "build-stackos-payload.sh"), "utf8");
const builder = fs.readFileSync(path.join(desktopDir, "scripts", "build-tdlib-runtime-mac.sh"), "utf8");
const runtime = fs.readFileSync(
  path.join(repoRoot, "stackos", "integrations", "telegram_tdlib", "runtime.py"),
  "utf8"
);
const signer = fs.readFileSync(
  path.join(desktopDir, "scripts", "sign-stackos-runtime-mac.cjs"),
  "utf8"
);
const { refreshTdlibLibraryDigest } = require("./sign-stackos-runtime-mac.cjs");

assert.match(payload, /build-tdlib-runtime-mac\.sh/);
assert.match(payload, /STACKOS_DESKTOP_PAYLOAD_DIR/);
assert.match(payload, /STACKOS_DESKTOP_BUILD_DIR/);
assert.match(builder, /TDLIB_LIBRARY_PATH/);
assert.match(builder, /archive_sha256/);
assert.match(builder, /system-only/);
assert.match(builder, /TDLib native JSON ABI smoke passed/);
assert.match(runtime, /TDLIB_SOURCE_ARCHIVE_SHA256/);
assert.match(runtime, /TDLIB_RUNTIME_REPAIR/);
assert.match(signer, /bundledMachOFiles/);
assert.match(signer, /refreshTdlibLibraryDigest/);

const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-tdlib-sign-manifest-"));
try {
  const runtimeRoot = path.join(temporaryRoot, "telegram-tdlib-runtime");
  const libraryPath = path.join(runtimeRoot, "lib", "libtdjson.dylib");
  fs.mkdirSync(path.dirname(libraryPath), { recursive: true });
  fs.writeFileSync(libraryPath, "pre-sign-library");
  const manifestPath = path.join(runtimeRoot, "manifest.json");
  fs.writeFileSync(
    manifestPath,
    JSON.stringify({ library: { path: "lib/libtdjson.dylib", sha256: "stale" } })
  );

  refreshTdlibLibraryDigest(temporaryRoot);
  const firstDigest = JSON.parse(fs.readFileSync(manifestPath, "utf8")).library.sha256;
  assert.equal(
    firstDigest,
    crypto.createHash("sha256").update(fs.readFileSync(libraryPath)).digest("hex")
  );

  // A codesign seal mutates the Mach-O. The post-sign manifest must follow
  // the new bytes, or daemon runtime verification rejects the packaged app.
  fs.appendFileSync(libraryPath, "post-sign-seal");
  refreshTdlibLibraryDigest(temporaryRoot);
  const signedDigest = JSON.parse(fs.readFileSync(manifestPath, "utf8")).library.sha256;
  assert.notEqual(signedDigest, firstDigest);
  assert.equal(
    signedDigest,
    crypto.createHash("sha256").update(fs.readFileSync(libraryPath)).digest("hex")
  );
} finally {
  fs.rmSync(temporaryRoot, { recursive: true, force: true });
}

console.log("desktop TDLib runtime contract test ok");
