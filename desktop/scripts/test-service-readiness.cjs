"use strict";

const assert = require("node:assert/strict");
const service = require("../src/service");

const doctorResult = {
  ok: true,
  exitCode: 0,
  stdout: [
    "stackos doctor: OK",
    JSON.stringify({
      ok: true,
      code: 0,
      checks: {
        daemon_up: true,
        provider_readiness_available: true
      },
      info: {
        provider_readiness: {
          status: "needs_connections",
          connected_count: 0,
          setup_required_count: 3,
          connections_url: "http://127.0.0.1:5180/projects/{project_id}/connections"
        }
      }
    })
  ].join("\n"),
  stderr: ""
};

const parsed = service.parseDoctorPayload(doctorResult);
assert.equal(parsed.ok, true);
assert.equal(parsed.code, 0);
assert.equal(parsed.info.provider_readiness.status, "needs_connections");

const readiness = service.readinessFromDoctor(doctorResult);
assert.equal(readiness.ok, true);
assert.equal(readiness.status, "ready");
assert.equal(readiness.code, 0);
assert.equal(readiness.providerReadiness.status, "needs_connections");
assert.equal(readiness.providerReadiness.connected_count, 0);

const unparsed = service.readinessFromDoctor({
  ok: false,
  exitCode: 9,
  stdout: "not json",
  stderr: ""
});
assert.equal(unparsed.ok, false);
assert.equal(unparsed.status, "doctor-unparsed");
assert.equal(unparsed.code, 9);

const hostStatusResult = {
  ok: true,
  exitCode: 0,
  stdout: JSON.stringify({
    ok: true,
    status: "ready",
    hosts: [
      {
        host_key: "codex",
        status: "registered_current",
        ok: true,
        available: true
      }
    ]
  }),
  stderr: ""
};
const hostStatus = service.parseMcpHostStatusPayload(hostStatusResult);
assert.equal(hostStatus.ok, true);
assert.equal(hostStatus.hosts.length, 1);
assert.equal(hostStatus.hosts[0].host_key, "codex");

const mcpFailure = service.installReadiness({
  start: { ok: true },
  startReady: true,
  mcpReady: false,
  mcpRepair: { exitCode: 1 },
  doctor: null
});
assert.equal(mcpFailure.ok, false);
assert.equal(mcpFailure.status, "mcp-failed");
assert.equal(mcpFailure.code, 1);
assert.equal(
  service.installPhase({
    start: { ok: true },
    startReady: true,
    mcpReady: false,
    doctor: null
  }),
  "mcp"
);
assert.deepEqual(service.scopedInstallArgs({ mcpHosts: ["codex"], launchd: true }), [
  "install",
  "--skill-runtime",
  "codex",
  "--mcp-host",
  "codex",
  "--launchd",
  "--force",
  "--skip-doctor"
]);

console.log("desktop service readiness test ok");
