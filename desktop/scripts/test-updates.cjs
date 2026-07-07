"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  configuredUpdateUrl,
  createUpdateController,
  updateUrlPolicy
} = require("../src/updates");

function withTempDir(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-updates-test-"));
  try {
    fn(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

withTempDir((dir) => {
  const packaged = path.join(dir, "packaged.json");
  const dev = path.join(dir, "dev.json");
  fs.writeFileSync(packaged, JSON.stringify({ updateUrl: "https://updates.example.com/packaged" }));
  fs.writeFileSync(dev, JSON.stringify({ updateUrl: "https://updates.example.com/dev" }));

  assert.equal(
    configuredUpdateUrl({
      env: { STACKOS_UPDATE_URL: "https://updates.example.com/env" },
      configPaths: [packaged, dev]
    }),
    "https://updates.example.com/env"
  );
  assert.equal(configuredUpdateUrl({ env: {}, configPaths: [packaged, dev] }), "https://updates.example.com/packaged");
  assert.equal(configuredUpdateUrl({ env: {}, configPaths: [path.join(dir, "missing.json"), dev] }), "https://updates.example.com/dev");
  assert.equal(configuredUpdateUrl({ env: {}, configPaths: [] }), null);
});

assert.equal(updateUrlPolicy("https://flowmonkey.io/StackOS/").ok, true);
assert.equal(updateUrlPolicy("http://127.0.0.1:8765/stackos/macos").ok, true);
assert.equal(updateUrlPolicy("http://updates.example.com/stackos/macos").ok, false);
assert.equal(updateUrlPolicy("not a url").reason, "update endpoint is not a valid URL");
assert.match(
  updateUrlPolicy("https://flowmonkey.io/StackOS/stackos-1.1.1-mac-arm64.dmg").reason,
  /base directory/
);
assert.match(updateUrlPolicy("https://flowmonkey.io/StackOS/latest-mac.yml").reason, /base directory/);

{
  const controller = createUpdateController({ env: {}, platform: "darwin", configPaths: [] });
  assert.equal(controller.state.enabled, false);
  assert.equal(controller.state.status, "disabled");
  assert.equal(controller.state.reason, "update endpoint is not configured");
}

{
  const controller = createUpdateController({
    env: { STACKOS_UPDATE_URL: "https://flowmonkey.io/StackOS/" },
    platform: "linux"
  });
  assert.equal(controller.state.enabled, false);
  assert.equal(controller.state.reason, "updates are only enabled on macOS");
}

{
  const controller = createUpdateController({
    env: { STACKOS_UPDATE_URL: "http://updates.example.com/stackos/macos" },
    platform: "darwin"
  });
  assert.equal(controller.state.enabled, false);
  assert.equal(controller.state.reason, "update endpoint must use HTTPS unless it is localhost for local testing");
}

class FakeUpdater extends EventEmitter {
  constructor() {
    super();
    this.quitArgs = null;
  }

  async checkForUpdates() {
    this.emit("checking-for-update");
    this.emit("update-available", { version: "1.0.1" });
    return { updateInfo: { version: "1.0.1" } };
  }

  async downloadUpdate() {
    this.emit("download-progress", { percent: 50 });
    this.emit("update-downloaded", { version: "1.0.1" });
  }

  quitAndInstall() {
    this.quitArgs = {};
  }
}

(async () => {
  const updater = new FakeUpdater();
  const controller = createUpdateController({
    env: { STACKOS_UPDATE_URL: "https://flowmonkey.io/StackOS/" },
    platform: "darwin",
    updater
  });

  assert.equal(controller.state.enabled, true);
  assert.equal(updater.autoDownload, false);
  assert.equal((await controller.quitAndInstall()).ok, false);
  const earlyDownload = await controller.downloadUpdate();
  assert.equal(earlyDownload.ok, false);
  assert.equal(earlyDownload.reason, "check for updates before downloading");

  const check = await controller.checkForUpdates();
  assert.equal(check.ok, true);
  assert.equal(controller.state.status, "available");
  assert.deepEqual(controller.state.updateInfo, { version: "1.0.1" });

  const download = await controller.downloadUpdate();
  assert.equal(download.ok, true);
  assert.equal(controller.state.status, "downloaded");
  assert.deepEqual(controller.state.progress, { percent: 50 });

  assert.equal(controller.quitAndInstall().ok, true);
  assert.equal(controller.state.status, "installing");
  assert.deepEqual(updater.quitArgs, {});

  class ThrowingUpdater extends EventEmitter {
    async checkForUpdates() {
      throw new Error("feed missing");
    }

    async downloadUpdate() {
      throw new Error("artifact missing");
    }
  }

  const failing = createUpdateController({
    env: { STACKOS_UPDATE_URL: "https://flowmonkey.io/StackOS/" },
    platform: "darwin",
    updater: new ThrowingUpdater()
  });

  const failedCheck = await failing.checkForUpdates();
  assert.equal(failedCheck.ok, false);
  assert.equal(failing.state.status, "error");
  assert.equal(failing.state.lastError, "feed missing");

  const guardedDownload = await failing.downloadUpdate();
  assert.equal(guardedDownload.ok, false);
  assert.equal(guardedDownload.reason, "check for updates before downloading");

  failing.state.status = "available";
  const failedDownload = await failing.downloadUpdate();
  assert.equal(failedDownload.ok, false);
  assert.equal(failing.state.lastError, "artifact missing");

  console.log("desktop updates test ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
