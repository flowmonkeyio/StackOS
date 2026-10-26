"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRequire } = require("node:module");
const { spawnSync } = require("node:child_process");

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

// Invalid inputs must fail before signing or loading any native code.
const guardRoot = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-tdlib-input-"));
try {
  const input = path.join(guardRoot, "input.dylib");
  const touched = path.join(guardRoot, "native-command-ran");
  fs.writeFileSync(input, "unapproved-library");
  const before = fs.readFileSync(input);
  for (const command of ["lipo", "otool", "codesign"]) {
    fs.writeFileSync(path.join(guardRoot, command), `#!/bin/sh\ntouch '${touched}'\nexit 97\n`, { mode: 0o755 });
  }
  function runGuard(overrides = {}) {
    return spawnSync("bash", [path.join(desktopDir, "scripts", "build-tdlib-runtime-mac.sh")], {
      env: {
        ...process.env,
        PATH: guardRoot + path.delimiter + process.env.PATH,
        TDLIB_INPUT_VARIANT: "stackos-2.1.38-packaged-local",
        TDLIB_LIBRARY_PATH: input,
        TDLIB_RUNTIME_ROOT: path.join(guardRoot, "runtime"),
        STACKOS_UNSIGNED_DEV: "1",
        STACKOS_REQUIRE_SIGNING: "",
        STACKOS_REQUIRE_UPDATE_URL: "",
        STACKOS_UPDATE_URL: "",
        ...overrides
      },
      encoding: "utf8"
    });
  }
  for (const overrides of [
    { STACKOS_UNSIGNED_DEV: "" }, { TDLIB_LIBRARY_PATH: "" },
    { STACKOS_REQUIRE_SIGNING: "1", STACKOS_ALLOW_UNSIGNED_RELEASE: "1" },
    { STACKOS_REQUIRE_UPDATE_URL: "true", STACKOS_ALLOW_UNSIGNED_RELEASE: "1" },
    { STACKOS_UPDATE_URL: "https://updates.example.com/stackos" },
    { STACKOS_UPDATE_URL: "http://127.0.0.1:8765/stackos" },
    { TDLIB_INPUT_VARIANT: "unknown" }
  ]) {
    const result = runGuard(overrides);
    assert.notEqual(result.status, 0, JSON.stringify(overrides));
    assert.match(result.stderr, /Packaged TDLib reuse|Unknown TDLIB_INPUT_VARIANT/);
  }
  for (const variant of ["original", "stackos-2.1.38-packaged-local"]) {
    const result = runGuard({ TDLIB_INPUT_VARIANT: variant });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /did not match the pinned input artifact digest/);
  }
  assert.equal(fs.existsSync(touched), false, "unverified bytes must not reach native tooling");
  assert.deepEqual(fs.readFileSync(input), before, "input must remain unchanged");
} finally {
  fs.rmSync(guardRoot, { recursive: true, force: true });
}

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

async function verifySecondarySigningPass() {
  const builderRequire = createRequire(require.resolve("electron-builder/package.json"));
  const { MacTargetHelper } = builderRequire("app-builder-lib/out/mac/MacTargetHelper.js");
  const pkg = JSON.parse(fs.readFileSync(path.join(desktopDir, "package.json"), "utf8"));
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-tdlib-secondary-sign-"));
  try {
    const appPath = path.join(fixture, "StackOS.app");
    const stackosRoot = path.join(appPath, "Contents", "Resources", "stackos");
    const runtimeRoot = path.join(stackosRoot, "telegram-tdlib-runtime");
    const libraryPath = path.join(runtimeRoot, "lib", "libtdjson.dylib");
    const manifestPath = path.join(runtimeRoot, "manifest.json");
    const neighborPath = path.join(runtimeRoot, "lib", "another.dylib");
    fs.mkdirSync(path.dirname(libraryPath), { recursive: true });
    fs.writeFileSync(libraryPath, "library-with-afterPack-signature");
    fs.writeFileSync(neighborPath, "another-native-library");
    fs.writeFileSync(manifestPath, JSON.stringify({ library: { path: "lib/libtdjson.dylib" } }));
    refreshTdlibLibraryDigest(stackosRoot);
    const digest = () => crypto.createHash("sha256").update(fs.readFileSync(libraryPath)).digest("hex");
    const declaredDigest = () => JSON.parse(fs.readFileSync(manifestPath, "utf8")).library.sha256;
    const helper = new MacTargetHelper({ config: {} });
    helper.getOptionsForFile = async () => () => ({});
    const optionsFor = (config) =>
      helper.buildSignOptions(appPath, { name: "Fixture identity" }, "distribution", false, config);
    const outerSeal = () => crypto.createHash("sha256")
      .update(fs.readFileSync(manifestPath)).update(fs.readFileSync(libraryPath)).digest("hex");
    const secondarySign = (options) => {
      const signed = [];
      let resourceSeal;
      // Model osx-sign's --force child mutation and final outer resource seal.
      for (const target of [libraryPath, neighborPath, appPath]) {
        if (options.ignore(target)) continue;
        signed.push(target);
        if (target === appPath) resourceSeal = outerSeal();
        else fs.appendFileSync(target, "|secondary-codesign");
      }
      return { signed, resourceSeal };
    };
    const unprotected = await optionsFor({ ...pkg.build.mac, signIgnore: [] });
    secondarySign(unprotected);
    assert.notEqual(declaredDigest(), digest(), "secondary signing must reproduce the stale digest");
    fs.writeFileSync(libraryPath, "library-with-afterPack-signature");
    refreshTdlibLibraryDigest(stackosRoot);
    const protectedOptions = await optionsFor(pkg.build.mac);
    assert.equal(protectedOptions.ignore(libraryPath), true);
    for (const target of [
      neighborPath, runtimeRoot, appPath, libraryPath + ".backup",
      libraryPath.replace("telegram-tdlib-runtime", "telegram-tdlib-runtime-other"),
      path.join(appPath, "Contents", "Frameworks", "libtdjson.dylib")
    ]) {
      assert.equal(protectedOptions.ignore(target), false, target);
    }
    const completed = secondarySign(protectedOptions);
    assert.deepEqual(completed.signed, [neighborPath, appPath]);
    assert.equal(declaredDigest(), digest(), "final signed library must match its sealed manifest");
    assert.equal(completed.resourceSeal, outerSeal(), "manifest must remain covered by the outer seal");
  } finally {
    fs.rmSync(fixture, { recursive: true, force: true });
  }
}

verifySecondarySigningPass().then(
  () => console.log("desktop TDLib runtime contract test ok"),
  (error) => {
    console.error(error);
    process.exitCode = 1;
  }
);
