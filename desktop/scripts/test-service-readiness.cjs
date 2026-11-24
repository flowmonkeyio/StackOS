"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
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

console.log("desktop service readiness test ok");

async function installReadiness() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-install-readiness-"));
  let daemonHostStatusMode = "ok";
  let daemonHostStatusHits = 0;
  const daemonHostStatusDemands = [];
  let cliHostStatusHits = 0;
  const server = http.createServer((req, res) => {
    if (req.url === "/api/v1/operations/hostMcp.status/call" && req.method === "POST") {
      daemonHostStatusHits += 1;
      let body = "";
      req.on("data", (chunk) => { body += chunk; });
      req.on("end", () => { daemonHostStatusDemands.push(JSON.parse(body).arguments.include_models); });
      if (daemonHostStatusMode === "ok" && req.headers.authorization === "Bearer test-daemon-token") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({
          ok: true,
          items: [{ host_key: "codex", status: "registered_current", ok: true, available: true }]
        }));
        return;
      }
      res.writeHead(503, { "content-type": "application/json" });
      res.end('{"detail":"unavailable"}');
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end('{"ok":true}');
  });
  const savedEnv = { ...process.env };
  try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const tokenFile = path.join(root, "state", "auth.token");
    fs.mkdirSync(path.dirname(tokenFile), { recursive: true });
    fs.writeFileSync(tokenFile, "test-daemon-token\n", { mode: 0o600 });
    Object.assign(process.env, {
      HOME: root,
      STACKOS_HOME: root,
      STACKOS_DATA_DIR: path.join(root, "data"),
      STACKOS_STATE_DIR: path.join(root, "state"),
      STACKOS_DESKTOP_AUTH_TOKEN_PATH: tokenFile,
      STACKOS_DESKTOP_DAEMON_HOST: "127.0.0.1",
      STACKOS_DESKTOP_DAEMON_PORT: String(server.address().port)
    });
    const cli = path.join(root, "stackos");
    const fixture = path.join(root, "result.json");
    const cliMarker = path.join(root, "cli-host-status.hits");
    const restartIndex = process.argv.indexOf("--restart-report");
    const restart = restartIndex < 0 ? null : JSON.parse(fs.readFileSync(process.argv[restartIndex + 1], "utf8"));
    fs.writeFileSync(cli, `#!${process.execPath}
const fs = require("node:fs");
const command = process.argv[2];
if (command === "mcp-host-status") {
  fs.appendFileSync(${JSON.stringify(cliMarker)}, JSON.stringify(process.argv.slice(3)) + "\\n");
  console.log(JSON.stringify({ ok: true, hosts: [{ host_key: "codex", status: "registered_current", ok: true, available: true, from: "cli" }] }));
  process.exitCode = 0;
  return;
}
const result = JSON.parse(fs.readFileSync(${JSON.stringify(fixture)}, "utf8"));
if (command === "doctor") {
  console.log(typeof result.payload === "string" ? result.payload : JSON.stringify(result.payload));
  process.exitCode = result.exitCode;
} else if (command === "install" || command === "restart") {
  const replay = command === "restart" ? ${JSON.stringify(restart)} : null;
  if (replay) {
    process.stdout.write(replay.stdout);
    process.stderr.write(replay.stderr);
  }
  process.exitCode = result[command] || replay?.exitCode || 0;
} else { throw new Error("unexpected native command " + command); }
`, { mode: 0o755 });
    process.env.STACKOS_DESKTOP_CLI = cli;
    delete require.cache[require.resolve("../src/service")];
    const lifecycleService = require("../src/service");
    const fromDaemon = await lifecycleService.inspectMcpHosts();
    assert.equal(fromDaemon.ok, true);
    assert.equal(fromDaemon.source, "daemon");
    assert.equal(fromDaemon.items.length, 1);
    assert.equal(fromDaemon.items[0].host_key, "codex");
    assert.equal(daemonHostStatusHits, 1);
    assert.equal(fs.existsSync(cliMarker), false);
    await lifecycleService.inspectMcpHosts({ includeModels: false });
    assert.deepEqual(daemonHostStatusDemands, [true, false]);

    daemonHostStatusMode = "error";
    const fromCliFallback = await lifecycleService.inspectMcpHosts({ includeModels: false });
    assert.equal(fromCliFallback.ok, true);
    assert.equal(fromCliFallback.items.length, 1);
    assert.equal(fromCliFallback.items[0].from, "cli");
    assert.equal(daemonHostStatusHits, 3);
    cliHostStatusHits = fs.readFileSync(cliMarker, "utf8").trim().split("\n").length;
    assert.equal(cliHostStatusHits, 1);
    await lifecycleService.inspectMcpHosts();
    assert.deepEqual(fs.readFileSync(cliMarker, "utf8").trim().split("\n").map(JSON.parse), [
      ["--json", "--no-include-models"], ["--json", "--include-models"]
    ]);
    const reportIndex = process.argv.indexOf("--doctor-report");
    const emitted = reportIndex < 0 ? null : JSON.parse(fs.readFileSync(process.argv[reportIndex + 1], "utf8"));
    const emittedExpected = !process.argv.includes("--expect-install-failure");
    const healthy = JSON.parse(doctorResult.stdout.split("\n").at(-1));
    healthy.installation = { ok: true, code: 0 };
    const attention = emitted || {
      ...healthy, ok: false, code: 9,
      checks: { ...healthy.checks, mcp_registration_ready: false },
      info: { ...healthy.info, mcp_hosts: [{
        host_key: "claude-desktop", connection_state: "restart_required",
        selected: true, managed: true, blocking: true, needs_restart: true,
        ok: false, message: "Restart Claude Desktop."
      }] }
    };
    const cases = [
      { payload: healthy, exitCode: 0, expected: true },
      { payload: attention, exitCode: attention.code, expected: emittedExpected },
      { payload: { ...attention, installation: { ok: false, code: 9 } }, exitCode: 9, expected: false },
      { payload: { ...attention, installation: { ok: false, code: 1 } }, exitCode: 9, expected: false },
      { payload: "not json", exitCode: 9, expected: false },
      { payload: { ...attention, installation: { ok: "true", code: 0 } }, exitCode: 9, expected: false },
      { payload: attention, exitCode: 2, expected: false },
      { payload: healthy, exitCode: 1, expected: false },
      { payload: healthy, exitCode: 0, install: 1, expected: false },
      { payload: healthy, exitCode: 0, restart: 1, expected: false }
    ];
    for (const test of cases) {
      fs.writeFileSync(fixture, JSON.stringify(test));
      const result = await lifecycleService.installOrRepair({ timeoutSeconds: 5 });
      assert.equal(result.ok, test.expected, JSON.stringify({ test, result }));
      if (!test.install && !test.restart) {
        assert.equal(result.phase, test.expected ? "ready" : "doctor");
        assert.equal(result.start.health.ok, true);
        assert.equal(result.doctor.exitCode, test.exitCode);
      }
      if (emitted && test.payload === attention) {
        assert.deepEqual(result.doctor.parsed, emitted);
        if (restart) assert.equal(result.start.stdout, restart.stdout);
      }
      if (test.expected) {
        assert.equal(result.phase, "ready");
        assert.equal(result.readiness.ok, true);
        assert.equal(result.doctor.parsed.ok, test.payload.ok);
        assert.deepEqual(result.doctor.parsed.info, test.payload.info);
        assert.equal(result.doctor.readiness.ok, test.payload.ok);
      }
    }
    console.log("desktop real install result consumer test ok");
  } finally {
    await new Promise((resolve) => server.close(resolve));
    for (const key of Object.keys(process.env)) {
      if (!(key in savedEnv)) delete process.env[key];
    }
    Object.assign(process.env, savedEnv);
    fs.rmSync(root, { recursive: true, force: true });
  }
}

installReadiness().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
