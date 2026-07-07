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

function runBuild(env, options = {}) {
  const writeConfig = options.writeConfig !== false;
  return spawnSync(process.execPath, ["scripts/build-mac.mjs"], {
    cwd: desktopDir,
    env: {
      ...process.env,
      STACKOS_UPDATE_URL: "",
      STACKOS_REQUIRE_SIGNING: "",
      STACKOS_REQUIRE_UPDATE_URL: "",
      STACKOS_ALLOW_UNSIGNED_RELEASE: "",
      STACKOS_UNSIGNED_DEV: "",
      STACKOS_SKIP_NOTARIZATION: "",
      STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY: "",
      CSC_IDENTITY_AUTO_DISCOVERY: "",
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
      STACKOS_DESKTOP_BUILD_DRY_RUN: "1",
      STACKOS_DESKTOP_DRY_RUN_WRITE_CONFIG: writeConfig ? "1" : ""
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

  cleanup();
  const unsignedDev = runBuild({
    STACKOS_UNSIGNED_DEV: "1",
    STACKOS_ALLOW_UNSIGNED_RELEASE: "1",
    STACKOS_SKIP_NOTARIZATION: "1",
    CSC_IDENTITY_AUTO_DISCOVERY: "false",
    CSC_NAME: "Developer ID Application: Bad Env (BADENV1234)",
    APPLE_API_KEY: "partial-env-ignored-for-dev"
  });
  assert.equal(unsignedDev.status, 0, unsignedDev.stderr);
  assert.match(unsignedDev.stdout, /signing=unsigned-dev/);
  assert.match(unsignedDev.stdout, /notarization=disabled/);
  const unsignedDevConfig = JSON.parse(fs.readFileSync(generatedConfigPath, "utf8"));
  assert.equal(unsignedDevConfig.forceCodeSigning, false);
  assert.equal(unsignedDevConfig.mac.identity, null);
  assert.equal(unsignedDevConfig.mac.notarize, false);
  assert.equal(unsignedDevConfig.dmg.sign, false);

  cleanup();
  const unsignedReleaseIntentWithoutBypass = runBuild({
    STACKOS_UNSIGNED_DEV: "1",
    STACKOS_REQUIRE_SIGNING: "1",
    STACKOS_SKIP_NOTARIZATION: "1"
  });
  assert.notEqual(unsignedReleaseIntentWithoutBypass.status, 0);
  assert.match(unsignedReleaseIntentWithoutBypass.stderr, /STACKOS_UNSIGNED_DEV release-intent/);

  cleanup();
  const localUrl = "http://127.0.0.1:8765/stackos/macos";
  const localNoWrite = runBuild({ STACKOS_UPDATE_URL: localUrl }, { writeConfig: false });
  assert.equal(localNoWrite.status, 0, localNoWrite.stderr);
  assert.match(localNoWrite.stdout, /generatedConfig=$/m);
  assert.equal(fs.existsSync(generatedConfigPath), false);
  assert.equal(fs.existsSync(generatedUpdateConfigPath), false);

  cleanup();
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
  const httpsUrl = "https://flowmonkey.io/StackOS/";
  const artifactUrl = "https://flowmonkey.io/StackOS/stackos-1.1.1-mac-arm64.dmg";
  const artifactUrlBuild = runBuild({ STACKOS_UPDATE_URL: artifactUrl });
  assert.notEqual(artifactUrlBuild.status, 0);
  assert.match(artifactUrlBuild.stderr, /base directory containing latest-mac.yml/);

  cleanup();
  const missingReleaseSigning = runBuild({ STACKOS_UPDATE_URL: httpsUrl });
  assert.notEqual(missingReleaseSigning.status, 0);
  assert.match(missingReleaseSigning.stderr, /release builds require CSC_NAME/);
  assert.match(missingReleaseSigning.stderr, /release builds require notarization credentials/);

  cleanup();
  const missingReleaseUpdateUrl = runBuild({
    STACKOS_REQUIRE_SIGNING: "1",
    STACKOS_REQUIRE_UPDATE_URL: "1",
    CSC_NAME: "Example Org (ABCDE12345)",
    APPLE_API_KEY: "fake-base64-p8",
    APPLE_API_KEY_ID: "ABC123DEFG",
    APPLE_API_ISSUER: "00000000-0000-0000-0000-000000000000"
  });
  assert.notEqual(missingReleaseUpdateUrl.status, 0);
  assert.match(missingReleaseUpdateUrl.stderr, /public release builds require STACKOS_UPDATE_URL/);

  cleanup();
  const releaseAutoDiscovery = runBuild({
    STACKOS_REQUIRE_SIGNING: "1",
    STACKOS_REQUIRE_UPDATE_URL: "1",
    STACKOS_UPDATE_URL: httpsUrl,
    STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY: "1",
    APPLE_API_KEY: "fake-base64-p8",
    APPLE_API_KEY_ID: "ABC123DEFG",
    APPLE_API_ISSUER: "00000000-0000-0000-0000-000000000000"
  });
  assert.notEqual(releaseAutoDiscovery.status, 0);
  assert.match(releaseAutoDiscovery.stderr, /public release builds require CSC_NAME/);

  cleanup();
  const signedRelease = runBuild({
    STACKOS_UPDATE_URL: httpsUrl,
    STACKOS_REQUIRE_UPDATE_URL: "1",
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
