"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const service = require("../src/service");

function makeUserDataPath() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "stackos-upgrade-test-"));
}

function cleanup(paths) {
  for (const target of paths) {
    fs.rmSync(target, { recursive: true, force: true });
  }
}

async function main() {
  const tempPaths = [];
  try {
    const userDataPath = makeUserDataPath();
    tempPaths.push(userDataPath);

    const payloadA = {
      name: "stackos",
      version: "1.0.0",
      buildId: "payload-a",
      builtAt: "2026-06-23T00:00:00.000Z"
    };
    const payloadB = {
      ...payloadA,
      buildId: "payload-b"
    };

    let calls = 0;
    const okInstall = async () => {
      calls += 1;
      return {
        ok: true,
        phase: "ready"
      };
    };

    const first = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadA,
      installOrRepairFn: okInstall
    });
    assert.equal(first.ok, true);
    assert.equal(first.skipped, false);
    assert.equal(calls, 1);
    assert.equal(service.readInstallState(userDataPath).installKey, "1.0.0:1.0.0:payload-a");

    const current = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadA,
      installOrRepairFn: okInstall
    });
    assert.equal(current.ok, true);
    assert.equal(current.skipped, true);
    assert.equal(calls, 1);

    const upgradedPayload = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadB,
      installOrRepairFn: okInstall
    });
    assert.equal(upgradedPayload.ok, true);
    assert.equal(upgradedPayload.skipped, false);
    assert.equal(calls, 2);
    assert.equal(service.readInstallState(userDataPath).installKey, "1.0.0:1.0.0:payload-b");

    const forced = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadB,
      force: true,
      installOrRepairFn: okInstall
    });
    assert.equal(forced.ok, true);
    assert.equal(forced.skipped, false);
    assert.equal(calls, 3);

    assert.equal(
      service.installKeyFor({
        version: "2.0.0",
        payloadInfo: {
          name: "stackos",
          version: "2.0.0"
        }
      }),
      "2.0.0:2.0.0:no-build-id"
    );

    const failedUserDataPath = makeUserDataPath();
    tempPaths.push(failedUserDataPath);
    const failed = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath: failedUserDataPath,
      payloadInfo: payloadA,
      installOrRepairFn: async () => ({
        ok: false,
        phase: "install",
        install: { exitCode: 1 }
      })
    });
    assert.equal(failed.ok, false);
    assert.equal(failed.skipped, false);
    assert.deepEqual(service.readInstallState(failedUserDataPath), {});

    const failedUpgradeUserDataPath = makeUserDataPath();
    tempPaths.push(failedUpgradeUserDataPath);
    await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath: failedUpgradeUserDataPath,
      payloadInfo: payloadA,
      installOrRepairFn: okInstall
    });

    let failedUpgradeCalls = 0;
    const failedUpgrade = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath: failedUpgradeUserDataPath,
      payloadInfo: payloadB,
      installOrRepairFn: async () => {
        failedUpgradeCalls += 1;
        return {
          ok: false,
          phase: "install",
          install: { exitCode: 1 }
        };
      }
    });
    assert.equal(failedUpgrade.ok, false);
    assert.equal(failedUpgrade.skipped, false);
    assert.equal(failedUpgradeCalls, 1);
    assert.equal(
      service.readInstallState(failedUpgradeUserDataPath).installKey,
      "1.0.0:1.0.0:payload-a"
    );

    const failedUpgradeRetry = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath: failedUpgradeUserDataPath,
      payloadInfo: payloadB,
      installOrRepairFn: async () => {
        failedUpgradeCalls += 1;
        return {
          ok: false,
          phase: "install",
          install: { exitCode: 1 }
        };
      }
    });
    assert.equal(failedUpgradeRetry.ok, false);
    assert.equal(failedUpgradeRetry.skipped, false);
    assert.equal(failedUpgradeCalls, 2);

    console.log("desktop service upgrade test ok");
  } finally {
    cleanup(tempPaths);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
