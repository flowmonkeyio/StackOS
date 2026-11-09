"use strict";

const assert = require("node:assert/strict");
const { EventEmitter, once } = require("node:events");
const http = require("node:http");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createAvailabilityController } = require("../src/availability");

function lifecycle() {
  const app = new EventEmitter();
  const powerMonitor = new EventEmitter();
  const connections = [];
  const retries = new Set();
  const controller = createAvailabilityController({
    app, powerMonitor,
    service: {
      openDesktopAvailability(disconnect) {
        const conn = { disconnect, closed: false, close() { this.closed = true; } };
        connections.push(conn);
        return conn;
      }
    },
    schedule(fn) { retries.add(fn); return fn; },
    cancel(fn) { retries.delete(fn); }
  });
  controller.start();
  controller.start();
  app.emit("window-all-closed");
  app.emit("minimize");
  assert.equal(connections.length, 1);
  assert.equal(connections[0].closed, false);
  powerMonitor.emit("suspend");
  assert.equal(connections[0].closed, true);
  connections[0].disconnect();
  assert.equal(retries.size, 0);
  controller.start();
  assert.equal(connections.length, 1);
  powerMonitor.emit("resume");
  assert.equal(connections.length, 2);
  connections[0].disconnect(); // stale generation cannot retry/replace
  assert.equal(retries.size, 0);
  connections[1].disconnect(); // abrupt daemon disconnect
  assert.equal(retries.size, 1);
  const retry = [...retries][0];
  retries.delete(retry);
  retry();
  assert.equal(connections.length, 3);
  app.emit("before-quit");
  assert.equal(connections[2].closed, true);
  powerMonitor.emit("resume");
  connections[2].disconnect();
  assert.equal(connections.length, 3);
  assert.equal(retries.size, 0);
}

async function transport() {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-availability-"));
  const tokenPath = path.join(scratch, "auth.token");
  fs.writeFileSync(tokenPath, "synthetic-native-token", { mode: 0o600 });
  let response;
  let accepted;
  const ready = new Promise((resolve) => { accepted = resolve; });
  const server = http.createServer((req, res) => {
    assert.equal(req.method, "POST");
    assert.equal(req.url, "/api/v1/desktop/availability");
    assert.equal(req.headers.authorization, "Bearer synthetic-native-token");
    response = res;
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write("event: ready\ndata: {}\n\n");
    accepted();
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  process.env.STACKOS_DESKTOP_DAEMON_PORT = String(server.address().port);
  process.env.STACKOS_DESKTOP_AUTH_TOKEN_PATH = tokenPath;
  const service = require("../src/service");
  try {
    const disconnected = new Promise((resolve) => service.openDesktopAvailability(resolve));
    await ready;
    response.destroy(); // real socket loss, no graceful message
    await disconnected;

    let unexpected = false;
    const conn = service.openDesktopAvailability(() => { unexpected = true; });
    const requestSeen = once(server, "request");
    await requestSeen;
    const closed = once(response, "close");
    conn.close();
    await closed;
    assert.equal(unexpected, false);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(scratch, { recursive: true, force: true });
  }
}

lifecycle();
transport().then(() => console.log("desktop availability lifecycle and native HTTP transport tests ok"))
  .catch((error) => { console.error(error); process.exitCode = 1; });
