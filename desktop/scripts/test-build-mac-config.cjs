"use strict";

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const desktopDir = path.resolve(__dirname, "..");
const generatedConfigPath = path.join(desktopDir, ".electron-builder.generated.json");
const generatedUpdateConfigPath = path.join(desktopDir, ".update-config.generated.json");

function cleanup() {
  fs.rmSync(generatedConfigPath, { force: true });
  fs.rmSync(generatedUpdateConfigPath, { force: true });
}

function runBuild(env) {
  return spawnSync(process.execPath, ["scripts/build-mac.mjs"], {
    cwd: desktopDir,
    env: {
      ...process.env,
      STACKOS_UPDATE_URL: "",
      STACKOS_REQUIRE_SIGNING: "",
      STACKOS_ALLOW_UNSIGNED_RELEASE: "",
      STACKOS_SKIP_NOTARIZATION: "",
      STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY: "",
      CSC_NAME: "",
      CSC_LINK: "",
      CSC_KEY_PASSWORD: "",
      APPLE_API_KEY: "",
      APPLE_API_KEY_ID: "",
      APPLE_API_ISSUER: "",
      APPLE_ID: "",
      APPLE_APP_SPECIFIC_PASSWORD: "",
      APPLE_TEAM_ID: "",
      APPLE_KEYCHAIN_PROFILE: "",
      APPLE_KEYCHAIN: "",
      ...env,
      STACKOS_DESKTOP_BUILD_DRY_RUN: "1"
    },
    encoding: "utf8"
  });
}

cleanup();
try {
  const noUrl = runBuild({ STACKOS_UPDATE_URL: "" });
  assert.equal(noUrl.status, 0, noUrl.stderr);
  assert.match(noUrl.stdout, /desktop mac build config dry run ok/);
  assert.match(noUrl.stdout, /releaseIntent=false/);
  assert.equal(fs.existsSync(generatedConfigPath), false);
  assert.equal(fs.existsSync(generatedUpdateConfigPath), false);

  const localUrl = "http://127.0.0.1:8765/stackos/macos";
  const local = runBuild({ STACKOS_UPDATE_URL: localUrl });
  assert.equal(local.status, 0, local.stderr);
  assert.match(local.stdout, /generatedConfig=/);

  const generatedConfig = JSON.parse(fs.readFileSync(generatedConfigPath, "utf8"));
  const generatedUpdateConfig = JSON.parse(fs.readFileSync(generatedUpdateConfigPath, "utf8"));
  assert.deepEqual(generatedConfig.publish, [{ provider: "generic", url: localUrl }]);
  assert.equal(generatedConfig.forceCodeSigning, undefined);
  assert.deepEqual(generatedUpdateConfig, { updateUrl: localUrl });
  assert.ok(
    generatedConfig.extraResources.some(
      (entry) => entry.from === ".update-config.generated.json" && entry.to === "update-config.json"
    )
  );

  cleanup();
  const httpsUrl = "https://updates.example.com/stackos/macos";
  const missingReleaseSigning = runBuild({ STACKOS_UPDATE_URL: httpsUrl });
  assert.notEqual(missingReleaseSigning.status, 0);
  assert.match(missingReleaseSigning.stderr, /release builds require CSC_NAME/);
  assert.match(missingReleaseSigning.stderr, /release builds require notarization credentials/);

  cleanup();
  const signedRelease = runBuild({
    STACKOS_UPDATE_URL: httpsUrl,
    CSC_NAME: "Example Org (ABCDE12345)",
    APPLE_API_KEY: "fake-base64-p8",
    APPLE_API_KEY_ID: "ABC123DEFG",
    APPLE_API_ISSUER: "00000000-0000-0000-0000-000000000000"
  });
  assert.equal(signedRelease.status, 0, signedRelease.stderr);
  assert.match(signedRelease.stdout, /releaseIntent=true/);
  assert.match(signedRelease.stdout, /signing=csc-name/);
  assert.match(signedRelease.stdout, /notarization=api-key/);
  assert.equal(JSON.parse(fs.readFileSync(generatedUpdateConfigPath, "utf8")).updateUrl, httpsUrl);
  const signedReleaseConfigText = fs.readFileSync(generatedConfigPath, "utf8");
  assert.doesNotMatch(signedReleaseConfigText, /fake-base64-p8/);
  const signedReleaseConfig = JSON.parse(signedReleaseConfigText);
  assert.equal(signedReleaseConfig.forceCodeSigning, true);
  assert.equal(signedReleaseConfig.mac.notarize, true);
  assert.equal(signedReleaseConfig.dmg.sign, true);

  cleanup();
  const explicitUnsignedSmoke = runBuild({
    STACKOS_REQUIRE_SIGNING: "1",
    STACKOS_SKIP_NOTARIZATION: "1",
    STACKOS_ALLOW_UNSIGNED_RELEASE: "1"
  });
  assert.equal(explicitUnsignedSmoke.status, 0, explicitUnsignedSmoke.stderr);
  assert.match(explicitUnsignedSmoke.stdout, /notarization=skipped/);
  assert.equal(JSON.parse(fs.readFileSync(generatedConfigPath, "utf8")).mac.notarize, false);

  cleanup();
  const partialAppleApi = runBuild({
    CSC_NAME: "Example Org (ABCDE12345)",
    APPLE_API_KEY: "fake-base64-p8",
    APPLE_API_KEY_ID: "ABC123DEFG"
  });
  assert.notEqual(partialAppleApi.status, 0);
  assert.match(partialAppleApi.stderr, /APPLE_API_KEY, APPLE_API_KEY_ID, APPLE_API_ISSUER/);

  cleanup();
  const prefixedCscName = runBuild({
    STACKOS_REQUIRE_SIGNING: "1",
    STACKOS_SKIP_NOTARIZATION: "1",
    CSC_NAME: "Developer ID Application: Example Org (ABCDE12345)"
  });
  assert.notEqual(prefixedCscName.status, 0);
  assert.match(prefixedCscName.stderr, /CSC_NAME must omit/);

  cleanup();
  const unsafe = runBuild({ STACKOS_UPDATE_URL: "http://updates.example.com/stackos/macos" });
  assert.notEqual(unsafe.status, 0);
  assert.match(unsafe.stderr, /must use HTTPS/);

  console.log("desktop build mac config test ok");
} finally {
  cleanup();
}
