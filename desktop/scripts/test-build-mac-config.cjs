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
  assert.equal(fs.existsSync(generatedConfigPath), false);
  assert.equal(fs.existsSync(generatedUpdateConfigPath), false);

  const localUrl = "http://127.0.0.1:8765/stackos/macos";
  const local = runBuild({ STACKOS_UPDATE_URL: localUrl });
  assert.equal(local.status, 0, local.stderr);
  assert.match(local.stdout, /generatedConfig=/);

  const generatedConfig = JSON.parse(fs.readFileSync(generatedConfigPath, "utf8"));
  const generatedUpdateConfig = JSON.parse(fs.readFileSync(generatedUpdateConfigPath, "utf8"));
  assert.deepEqual(generatedConfig.publish, [{ provider: "generic", url: localUrl }]);
  assert.deepEqual(generatedUpdateConfig, { updateUrl: localUrl });
  assert.ok(
    generatedConfig.extraResources.some(
      (entry) => entry.from === ".update-config.generated.json" && entry.to === "update-config.json"
    )
  );

  cleanup();
  const httpsUrl = "https://updates.example.com/stackos/macos";
  const https = runBuild({ STACKOS_UPDATE_URL: httpsUrl });
  assert.equal(https.status, 0, https.stderr);
  assert.equal(JSON.parse(fs.readFileSync(generatedUpdateConfigPath, "utf8")).updateUrl, httpsUrl);

  cleanup();
  const unsafe = runBuild({ STACKOS_UPDATE_URL: "http://updates.example.com/stackos/macos" });
  assert.notEqual(unsafe.status, 0);
  assert.match(unsafe.stderr, /must use HTTPS/);

  console.log("desktop build mac config test ok");
} finally {
  cleanup();
}
