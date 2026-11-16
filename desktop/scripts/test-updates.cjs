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

assert.equal(updateUrlPolicy("https://stackos.flowmonkey.io/StackOS/").ok, true);
assert.equal(updateUrlPolicy("http://127.0.0.1:8765/stackos/macos").ok, true);
assert.equal(updateUrlPolicy("http://updates.example.com/stackos/macos").ok, false);
assert.equal(updateUrlPolicy("not a url").reason, "update endpoint is not a valid URL");
assert.match(
  updateUrlPolicy("https://stackos.flowmonkey.io/StackOS/stackos-1.1.1-mac-arm64.dmg").reason,
  /base directory/
);
assert.match(updateUrlPolicy("https://stackos.flowmonkey.io/StackOS/latest-mac.yml").reason, /base directory/);

{
  const controller = createUpdateController({ env: {}, platform: "darwin", configPaths: [] });
  assert.equal(controller.state.enabled, false);
  assert.equal(controller.state.status, "disabled");
  assert.equal(controller.state.reason, "update endpoint is not configured");
}

{
  const controller = createUpdateController({
    env: { STACKOS_UPDATE_URL: "https://stackos.flowmonkey.io/StackOS/" },
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
    // MacUpdater emits the artifact ref, then resolves [] when automatic
    // installation on quit is disabled.
    this.emit("update-downloaded", { version: "1.0.1", downloadedFile: "/tmp/downloaded-stackos.zip" });
    return [];
  }

  quitAndInstall() {
    this.quitArgs = {};
  }
}

(async () => {
  const updater = new FakeUpdater();
  const controller = createUpdateController({
    env: { STACKOS_UPDATE_URL: "https://stackos.flowmonkey.io/StackOS/" },
    platform: "darwin",
    updater,
    prepareUpdate: async (archive) => {
      assert.equal(archive, "/tmp/downloaded-stackos.zip");
      assert.equal(updater.quitArgs, null);
      return { ok: true, maintenance: { upgrade_id: "fixture" } };
    }
  });

  assert.equal(controller.state.enabled, true);
  assert.equal(updater.autoDownload, false);
  assert.equal(updater.autoInstallOnAppQuit, false);
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

  assert.equal((await controller.quitAndInstall()).ok, true);
  assert.equal(controller.state.status, "installing");
  assert.deepEqual(updater.quitArgs, {});

  const refusedUpdater = new FakeUpdater();
  let attempts = 0;
  const refused = createUpdateController({
    env: { STACKOS_UPDATE_URL: "https://stackos.flowmonkey.io/StackOS/" },
    platform: "darwin",
    updater: refusedUpdater,
    prepareUpdate: async () => {
      attempts += 1;
      return { ok: attempts > 1, reason: "Source handshake unavailable: project 7 task active. Controlled bootstrap required." };
    }
  });
  await refused.checkForUpdates();
  await refused.downloadUpdate();
  const deniedInstall = await refused.quitAndInstall();
  assert.equal(deniedInstall.ok, false);
  assert.match(deniedInstall.reason, /Controlled bootstrap/);
  assert.equal(refusedUpdater.quitArgs, null);
  assert.equal(refused.state.status, "downloaded");
  assert.equal((await refused.quitAndInstall()).ok, true);

  class ThrowingUpdater extends EventEmitter {
    async checkForUpdates() {
      throw new Error("feed missing");
    }

    async downloadUpdate() {
      throw new Error("artifact missing");
    }
  }

  const failing = createUpdateController({
    env: { STACKOS_UPDATE_URL: "https://stackos.flowmonkey.io/StackOS/" },
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
