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

console.log("desktop service readiness test ok");
