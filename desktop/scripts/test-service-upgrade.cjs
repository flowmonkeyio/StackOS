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
  const originalDesktopCli = process.env.STACKOS_DESKTOP_CLI;
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
    let mcpRepairCalls = 0;
    const okInstall = async () => {
      calls += 1;
      return {
        ok: true,
        phase: "ready"
      };
    };
    const okMcpRepair = async () => {
      mcpRepairCalls += 1;
      return {
        ok: true,
        phase: "external-registration"
      };
    };

    const first = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadA,
      installOrRepairFn: okInstall,
      repairMcpRegistrationsFn: okMcpRepair
    });
    assert.equal(first.ok, true);
    assert.equal(first.skipped, false);
    assert.equal(calls, 1);
    assert.equal(mcpRepairCalls, 0);
    assert.equal(
      service.readInstallState(userDataPath).installKey,
      service.installKeyFor({ version: "1.0.0", payloadInfo: payloadA })
    );

    const current = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadA,
      installOrRepairFn: okInstall,
      repairMcpRegistrationsFn: okMcpRepair
    });
    assert.equal(current.ok, true);
    assert.equal(current.skipped, true);
    assert.equal(calls, 1);
    assert.equal(mcpRepairCalls, 1);
    assert.equal(current.externalRepair.ok, true);

    const upgradedPayload = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadB,
      installOrRepairFn: okInstall,
      repairMcpRegistrationsFn: okMcpRepair
    });
    assert.equal(upgradedPayload.ok, true);
    assert.equal(upgradedPayload.skipped, false);
    assert.equal(calls, 2);
    assert.equal(mcpRepairCalls, 1);
    assert.equal(
      service.readInstallState(userDataPath).installKey,
      service.installKeyFor({ version: "1.0.0", payloadInfo: payloadB })
    );

    const forced = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadB,
      force: true,
      installOrRepairFn: okInstall,
      repairMcpRegistrationsFn: okMcpRepair
    });
    assert.equal(forced.ok, true);
    assert.equal(forced.skipped, false);
    assert.equal(calls, 3);
    assert.equal(mcpRepairCalls, 1);

    const currentWithFailedMcpRepair = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadB,
      installOrRepairFn: okInstall,
      repairMcpRegistrationsFn: async () => ({
        ok: false,
        phase: "external-registration",
        stderr: "Claude unavailable"
      })
    });
    assert.equal(currentWithFailedMcpRepair.ok, true);
    assert.equal(currentWithFailedMcpRepair.skipped, true);
    assert.equal(currentWithFailedMcpRepair.externalRepair.ok, false);

    const currentWithThrownMcpRepair = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath,
      payloadInfo: payloadB,
      installOrRepairFn: okInstall,
      repairMcpRegistrationsFn: async () => {
        throw new Error("Claude repair crashed");
      }
    });
    assert.equal(currentWithThrownMcpRepair.ok, true);
    assert.equal(currentWithThrownMcpRepair.skipped, true);
    assert.equal(currentWithThrownMcpRepair.externalRepair.ok, false);
    assert.equal(currentWithThrownMcpRepair.externalRepair.phase, "external-registration");

    assert.match(
      service.installKeyFor({
        version: "2.0.0",
        payloadInfo: {
          name: "stackos",
          version: "2.0.0"
        }
      }),
      /^2\.0\.0:2\.0\.0:no-build-id:/
    );

    const movedAppUserDataPath = makeUserDataPath();
    tempPaths.push(movedAppUserDataPath);
    let movedInstallCalls = 0;
    process.env.STACKOS_DESKTOP_CLI = "/tmp/StackOS-A.app/Contents/Resources/stackos/bin/stackos";
    await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath: movedAppUserDataPath,
      payloadInfo: payloadA,
      installOrRepairFn: async () => {
        movedInstallCalls += 1;
        return { ok: true, phase: "ready" };
      },
      repairMcpRegistrationsFn: okMcpRepair
    });
    process.env.STACKOS_DESKTOP_CLI = "/tmp/StackOS-B.app/Contents/Resources/stackos/bin/stackos";
    const movedPathResult = await service.prepareInstalledVersion({
      version: "1.0.0",
      userDataPath: movedAppUserDataPath,
      payloadInfo: payloadA,
      installOrRepairFn: async () => {
        movedInstallCalls += 1;
        return { ok: true, phase: "ready" };
      },
      repairMcpRegistrationsFn: okMcpRepair
    });
    assert.equal(movedPathResult.skipped, false);
    assert.equal(movedInstallCalls, 2);
    delete process.env.STACKOS_DESKTOP_CLI;

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
      }),
      repairMcpRegistrationsFn: okMcpRepair
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
      installOrRepairFn: okInstall,
      repairMcpRegistrationsFn: okMcpRepair
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
      },
      repairMcpRegistrationsFn: okMcpRepair
    });
    assert.equal(failedUpgrade.ok, false);
    assert.equal(failedUpgrade.skipped, false);
    assert.equal(failedUpgradeCalls, 1);
    assert.equal(
      service.readInstallState(failedUpgradeUserDataPath).installKey,
      service.installKeyFor({ version: "1.0.0", payloadInfo: payloadA })
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
      },
      repairMcpRegistrationsFn: okMcpRepair
    });
    assert.equal(failedUpgradeRetry.ok, false);
    assert.equal(failedUpgradeRetry.skipped, false);
    assert.equal(failedUpgradeCalls, 2);

    console.log("desktop service upgrade test ok");
  } finally {
    if (originalDesktopCli === undefined) {
      delete process.env.STACKOS_DESKTOP_CLI;
    } else {
      process.env.STACKOS_DESKTOP_CLI = originalDesktopCli;
    }
    cleanup(tempPaths);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
