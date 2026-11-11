"use strict";
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const servicePath = require.resolve("../src/service");
const selected = "http://127.0.0.1:19940";
const { resolveStackosDeepLink } = require('../src/deep-links');
assert.equal(resolveStackosDeepLink('stackos://sign-in', selected), selected + '/sign-in');
for (const target of ['stackos://sign-in?token=x', 'stackos://sign-in#token', 'stackos://user@sign-in', 'stackos://sign-in:19940', 'https://sign-in']) {
  assert.equal(resolveStackosDeepLink(target, selected), null, target);
}
process.env.STACKOS_SERVER_URL = selected;
const service = require(servicePath);
assert.equal(service.APP_URL, `${selected}/`);
assert.equal(service.isAppUrl(`${selected}/api/v1/auth/callback?type=signup`), true);
for (const target of ["http://localhost:19940/", "http://127.0.0.1:19941/", "https://127.0.0.1:19940/", "http://user@127.0.0.1:19940/"]) {
  assert.equal(service.isAppUrl(target), false, target);
}
for (const target of ["", "http://example.test", "https://example.test/path", "https://user@example.test/", "https://example.test/?x=1"]) {
  const result = spawnSync(process.execPath, ["-e", `require(${JSON.stringify(servicePath)})`], {
    env: { ...process.env, STACKOS_SERVER_URL: target }, encoding: "utf8"
  });
  assert.notEqual(result.status, 0, target);
}
async function checkAdmission() {
  const original = global.fetch;
  try {
    for (const [status, body, expected] of [
      [404, {}, false], [503, {}, false],
      [401, { detail: { code: "session_expired_or_invalid" } }, true]
    ]) {
      global.fetch = async (url, init) => {
        assert.equal(url.toString(), `${selected}/api/v1/auth/session`);
        assert.equal(init.credentials, "omit");
        assert.equal(init.redirect, "error");
        return new Response(JSON.stringify(body), { status });
      };
      assert.equal(await service.checkSelectedServer(), expected);
    }
  } finally {
    global.fetch = original;
  }
  console.log("Desktop selected-origin auth checks passed");
}
async function checkRuntimeStartup() {
  const childProcess = require("node:child_process");
  const http = require("node:http");
  const { EventEmitter } = require("node:events");
  const originalSpawn = childProcess.spawn;
  const originalGet = http.get;
  const commands = [];
  const config = {
    process_role: "runtime", environment_id: "desktop-startup-proof",
    server_url: "http://127.0.0.1:19950", host: "127.0.0.1", port: 19951,
    state_dir: "/private/tmp/stackos-desktop-startup-proof/state"
  };
  delete process.env.STACKOS_SERVER_URL;
  process.env.STACKOS_ENV_FILE = "/private/tmp/stackos-desktop-startup-proof/runtime.env";
  let failConfig = false;
  let healthCalls = 0;
  let wrongTarget = false;
  try {
    childProcess.spawn = (_command, args, options) => {
      const cliArgs = args.slice(args.indexOf("stackos") + 1);
      // Repo Python uses -m stackos; packaged commands have no prefix.
      const actual = args.includes("-m") ? cliArgs : args;
      commands.push(actual);
      assert.equal(options.env.STACKOS_ENV_FILE, process.env.STACKOS_ENV_FILE);
      const child = new EventEmitter();
      child.stdout = new EventEmitter();
      child.stderr = new EventEmitter();
      child.kill = () => {};
      process.nextTick(() => {
        if (actual[0] === "runtime-config" && !failConfig) {
          child.stdout.emit("data", Buffer.from(JSON.stringify(config)));
        }
        child.emit("close", failConfig ? 1 : 0, null);
      });
      return child;
    };
    http.get = (url, callback) => {
      assert.equal(url, "http://127.0.0.1:19951/api/v1/health");
      const request = new EventEmitter();
      request.setTimeout = () => {};
      request.destroy = () => {};
      process.nextTick(() => {
        const response = new EventEmitter();
        response.statusCode = healthCalls++ === 0 ? 503 : 200;
        response.setEncoding = () => {};
        callback(response);
        response.emit("data", JSON.stringify({ ...config,
          environment_id: wrongTarget ? "foreign" : config.environment_id }));
        response.emit("end");
      });
      return request;
    };
    await service.configureSelectedRuntime();
    assert.equal(service.APP_URL, `${config.server_url}/`);
    assert.equal(service.DAEMON_URL, "http://127.0.0.1:19951/");
    assert.equal(service.daemonLogPath(), `${config.state_dir}/daemon.log`);
    assert.equal(service.authTokenPath(), `${config.state_dir}/auth.token`);
    assert.equal(service.isAppUrl(selected), false);
    const ready = await service.prepareConnectedRuntime();
    assert.equal(ready.ok, true);
    assert.deepEqual(commands.map((args) => args[0]), ["runtime-config", "install", "start"]);
    assert.deepEqual(commands[1], ["install", "--mcp-only", "--skip-doctor"]);
    wrongTarget = true;
    assert.equal((await service.checkHealth()).ok, false);
    failConfig = true;
    await assert.rejects(service.configureSelectedRuntime(), /private runtime environment/);
    assert.equal(commands.at(-1)[0], "runtime-config");
    console.log("Desktop runtime startup and wrong-target refusal checks passed");
  } finally {
    childProcess.spawn = originalSpawn;
    http.get = originalGet;
    delete process.env.STACKOS_ENV_FILE;
  }
}
async function checkConnectedAvailabilityStartup() {
  const fs = require("node:fs");
  const path = require("node:path");
  const vm = require("node:vm");
  const { EventEmitter } = require("node:events");
  const source = fs.readFileSync(path.join(__dirname, "../src/main.js"), "utf8");
  const { createAvailabilityController } = require("../src/availability");

  function startup({ serverReady = true, runtime = { ok: true } } = {}) {
    const events = [];
    const handlers = new Map();
    const frame = { url: selected + '/sign-in' };
    const contents = { mainFrame: frame };
    const app = new EventEmitter();
    const powerMonitor = new EventEmitter();
    app.whenReady = () => ({ then() {} }); // Exercise the caller without installing/opening windows.
    const selectedService = {
      SERVER_URL: selected + "/", APP_URL: selected + "/",
      isAppUrl: (url) => new URL(url).origin === selected,
      checkSelectedServer: async () => { events.push("server-ready"); return serverReady; },
      prepareConnectedRuntime: async () => { events.push("runtime-ready"); return runtime; },
      openDesktopAvailability: () => {
        events.push("availability-opened");
        return { close() { events.push("availability-closed"); } };
      }
    };
    const context = vm.createContext({
      console: { warn() {} }, process, URL, setTimeout, clearTimeout,
      require(name) {
        if (name === "electron") return { app, powerMonitor,
          ipcMain: { handle(name, handler) { handlers.set(name, handler); } },
          shell: { openExternal: async (url) => events.push('external:' + url) } };
        if (name === "./service") return selectedService;
        if (name === "./availability") return { createAvailabilityController };
        if (name.startsWith("node:")) return require(name);
        return {}; // Unused notification/update/UI boundaries.
      },
      recordLoad: async (url) => events.push("loaded:" + url),
      bridgeContents: contents,
      recordFailure: async () => events.push("server-failed")
    });
    vm.runInContext(source + `
      mainWindow = { loadURL: recordLoad, webContents: bridgeContents };
      loadFailure = recordFailure;
      availabilityController = createAvailabilityController({ app, powerMonitor, service });
      globalThis.startup = prepareAndLoadStackOS;
    `, context, { filename: "desktop/src/main.js" });
    return { run: context.startup, events, app, powerMonitor,
      account: (flow, sender = contents, senderFrame = frame) => handlers.get('stackos:open-account')({ sender, senderFrame }, flow),
      frame };
  }

  const ready = startup();
  await ready.run();
  assert.deepEqual(ready.events, ["server-ready", "runtime-ready", "availability-opened", "loaded:" + selected + "/"]);
  await ready.run();
  assert.equal(ready.events.filter(event => event === "availability-opened").length, 1);
  ready.powerMonitor.emit("suspend");
  assert.equal(ready.events.at(-1), "availability-closed");
  ready.powerMonitor.emit("resume");
  assert.equal(ready.events.filter(event => event === "availability-opened").length, 2);
  ready.app.emit("before-quit");
  assert.equal(ready.events.at(-1), "availability-closed");

  for (const runtime of [{ ok: true, browsingOnly: true }, { ok: false }]) {
    const unavailable = startup({ runtime });
    await unavailable.run();
    unavailable.powerMonitor.emit("resume");
    assert.deepEqual(unavailable.events, ["server-ready", "runtime-ready", "loaded:" + selected + "/"]);
  }
  const serverUnavailable = startup({ serverReady: false });
  await serverUnavailable.run();
  assert.deepEqual(serverUnavailable.events, ["server-ready", "server-failed"]);
  const account = startup();
  await account.account('register');
  assert.equal(account.events.at(-1), 'external:' + selected + '/sign-in?flow=register&client=desktop');
  await assert.rejects(account.account('unknown'));
  await assert.rejects(account.account('recover', {}));
  await assert.rejects(account.account('recover', undefined, {url:selected + '/sign-in'}));
  account.frame.url = 'http://localhost:19940/sign-in';
  await assert.rejects(account.account('recover'));
  assert.equal(account.events.length, 1);
  console.log("Connected main-process startup availability and refusal checks passed");
}

checkAdmission().then(checkRuntimeStartup).then(checkConnectedAvailabilityStartup)
  .catch((error) => { console.error(error); process.exitCode = 1; });
