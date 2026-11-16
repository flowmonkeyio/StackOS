"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const vm = require("node:vm");
const { parseStackosDeepLink, resolveStackosDeepLink, stackosTaskDeepLink } = require("../src/deep-links");
const { createNotificationController, notificationType, notificationPresentation, registerNotificationIpc,
  readNotificationState, writeNotificationState } = require("../src/notifications");

class FakeNotification {
  static instances = [];
  static isSupported() { return true; }
  constructor(options) { this.options = options; this.handlers = {}; FakeNotification.instances.push(this); }
  on(name, handler) { this.handlers[name] = handler; }
  show() { this.shown = true; }
  close() { this.closed = true; this.handlers.close?.(); }
}

const roots = [];
function temp() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-notifications-"));
  roots.push(dir);
  return dir;
}
function change(rev, before = "in-progress", after = "complete", extra = {}) {
  return { rev, task_id: 9, task_key: "work", task_title: "Work", before_status: before,
    after_status: after, before_delivery_status: null, after_delivery_status: null, ...extra };
}
function page(rev, changes = [], next = null, project = 1) {
  return { projection: "notifications", project_id: project, project_name: "Project",
    current_rev: rev, changes, next_cursor: next };
}
function setup(overrides = {}) {
  const dir = overrides.userDataPath || temp();
  const opened = [];
  const controller = createNotificationController({ Notification: FakeNotification,
    userDataPath: dir, openDeepLink: (value) => opened.push(value), ...overrides });
  return { controller, dir, opened };
}

async function unitTests() {
  assert.deepEqual(parseStackosDeepLink("stackos://projects/7/tasks?task=launch-check"), {
    projectId: 7, taskKey: "launch-check", path: "/projects/7/tasks?view=graph&task=launch-check"
  });
  assert.equal(resolveStackosDeepLink("stackos://projects/7/tasks?task=launch-check", "http://127.0.0.1:5180/"),
    "http://127.0.0.1:5180/projects/7/tasks?view=graph&task=launch-check");
  assert.equal(stackosTaskDeepLink(7, "launch-check"), "stackos://projects/7/tasks?task=launch-check");
  for (const link of ["https://example.com/projects/7/tasks?task=x", "stackos://projects/7/connections",
    "stackos://projects/7/tasks?task=../bad"]) assert.equal(parseStackosDeepLink(link), null);

  assert.equal(notificationType(change(1)), "complete");
  assert.equal(notificationType(change(1, "in-progress", "failed")), "failed");
  assert.equal(notificationType(change(1, "complete", "complete")), null);
  assert.equal(notificationType(change(1, null)), null);
  assert.equal(notificationType(change(1, "in-progress", "in-progress")), null);
  assert.equal(notificationType(change(1, "in-progress", "in-progress", {
    approval_status: "pending", requires_input: true })), null);
  assert.equal(notificationType(change(1, "in-progress", "in-progress", {
    before_delivery_status: "running", after_delivery_status: "stopped" })), "stopped");
  assert.equal(notificationType(change(1, "in-progress", "failed", {
    before_delivery_status: "stopped", after_delivery_status: "stopped" })), null);
  assert.equal(notificationType(change(1, "failed", "failed", {
    before_delivery_status: "running", after_delivery_status: "stopped" })), null);
  assert.equal(notificationType(change(1, "in-progress", "failed", {
    before_delivery_status: "running", after_delivery_status: "stopped" })), "failed");

  let current = page(100, [change(100)]);
  let failSecond = false;
  let snapshot = { tasks: [{ id: 9, project_id: 1, key: "work" }] };
  const requests = [];
  const service = {
    authenticatedJsonGet: async (url) => {
      const parsed = new URL(url, "http://localhost");
      return parsed.searchParams.has("after") ? { items: [{ id: 2 }] } : { items: [{ id: 1 }], next_cursor: 1 };
    },
    authenticatedJsonPost: async (url, body) => {
      const args = body.arguments;
      requests.push({ url, args });
      if (url.includes("tracker.get")) return { ok: true, data: snapshot };
      if (args.project_id === 2) {
        if (failSecond) throw new Error("offline");
        return { ok: true, data: page(20, [], null, 2) };
      }
      return { ok: true, data: current };
    }
  };
  const { controller, dir, opened } = setup({ service });
  writeNotificationState(dir, { projectEventWatermarks: { "1": 9000 } });
  // Construct again to consume a legacy event ID that must never become a revision.
  const migrated = setup({ service, userDataPath: dir, openDeepLink: (link) => opened.push(link) }).controller;
  assert.equal((await migrated.pollOnce()).ok, true);
  assert.equal(FakeNotification.instances.length, 0);
  assert.equal(requests[0].args.since_rev, null);
  assert.equal(migrated.state.projectCount, 2);
  assert.equal(readNotificationState(dir).projectEventWatermarks["1"], 9000);
  assert.equal(readNotificationState(dir).projectRevisionCursors["1"], 100);

  current = page(103, [change(100), change(101), change(101), change(102, "complete", "complete"),
    change(103, "in-progress", "in-progress", { after_delivery_status: "stopped" })]);
  failSecond = true;
  assert.equal((await migrated.pollOnce()).ok, false);
  assert.equal(FakeNotification.instances.length, 2);
  assert.equal(readNotificationState(dir).projectRevisionCursors["1"], 103);
  assert.deepEqual(FakeNotification.instances.map((n) => n.options), [
    { title: "Task complete", body: "Work · Project", silent: false },
    { title: "Task needs attention", body: "Work stopped · Project", silent: false }
  ]);
  await FakeNotification.instances[0].handlers.click();
  assert.deepEqual(opened, ["stackos://projects/1/tasks?task=work"]);
  for (const tasks of [[], [{ id: 10, project_id: 1, key: "work" }],
    [{ id: 9, project_id: 2, key: "work" }], [{ id: 9, project_id: 1, key: "foreign" }]]) {
    snapshot = { tasks };
    await FakeNotification.instances[0].handlers.click();
    assert.equal(opened.length, 1);
  }
  failSecond = false;
  const restarted = setup({ service, userDataPath: dir }).controller;
  assert.equal((await restarted.pollOnce()).ok, true);
  assert.equal(FakeNotification.instances.length, 2);
  current = page(104, [change(104, "in-progress", "failed")]);
  assert.equal((await restarted.pollOnce()).ok, true);
  assert.equal(FakeNotification.instances.at(-1).options.title, "Task failed");
  // Corrupt/foreign page does not advance the cursor or notify.
  current = page(105, [change(105)], null, 2);
  assert.equal((await restarted.pollOnce()).ok, false);
  assert.equal(readNotificationState(dir).projectRevisionCursors["1"], 104);

  // Bound catch-up to four pages per poll, preserving quiet-revision cursors.
  const pagedDir = temp();
  writeNotificationState(pagedDir, { cursorVersion: 2, projectRevisionCursors: { "1": 0 } });
  let pageCalls = 0;
  const paged = setup({ userDataPath: pagedDir, service: {
    authenticatedJsonGet: async () => ({ items: [{ id: 1 }] }),
    authenticatedJsonPost: async (_url, { arguments: args }) => {
      pageCalls += 1;
      const end = Math.min(args.since_rev + 50, 260);
      return page(260, [change(end, "in-progress", "in-progress")], end < 260 ? end : null);
    }
  } }).controller;
  assert.equal((await paged.pollOnce()).ok, true);
  assert.equal(pageCalls, 4);
  assert.equal(readNotificationState(pagedDir).projectRevisionCursors["1"], 200);
  assert.equal((await paged.pollOnce()).ok, true);
  assert.equal(pageCalls, 6);
  assert.equal(readNotificationState(pagedDir).projectRevisionCursors["1"], 260);

  // A later page failure must retain earlier page progress across restart.
  let calls = 0;
  const partialDir = temp();
  writeNotificationState(partialDir, { cursorVersion: 2, projectRevisionCursors: { "1": 0 } });
  const partial = setup({ userDataPath: partialDir, service: {
    authenticatedJsonGet: async () => ({ items: [{ id: 1 }] }),
    authenticatedJsonPost: async () => {
      if (++calls === 2) throw new Error("disconnected between pages");
      return page(2, [change(1)], 1);
    }
  } }).controller;
  assert.equal((await partial.pollOnce()).ok, false);
  assert.equal(readNotificationState(partialDir).projectRevisionCursors["1"], 1);

  // Overlapping callers share one read; stop fences a late outcome and timer.
  let resolveRead;
  let projectReads = 0;
  let timerCount = 0;
  const slowDir = temp();
  writeNotificationState(slowDir, { cursorVersion: 2, projectRevisionCursors: { "1": 0 } });
  const slow = setup({ userDataPath: slowDir, setIntervalFn: () => { timerCount += 1; return 1; }, service: {
    authenticatedJsonGet: async () => { projectReads += 1; return { items: [{ id: 1 }] }; },
    authenticatedJsonPost: () => new Promise((resolve) => { resolveRead = resolve; })
  } }).controller;
  const first = slow.start();
  const second = slow.pollOnce();
  await new Promise(setImmediate);
  slow.stop();
  const before = FakeNotification.instances.length;
  resolveRead(page(1, [change(1)]));
  await Promise.all([first, second]);
  assert.equal(projectReads, 1);
  assert.equal(timerCount, 0);
  assert.equal(FakeNotification.instances.length, before);
  assert.equal(readNotificationState(slowDir).projectRevisionCursors["1"], 0);
  controller.stop();
  await presentationTests();
  await serviceTransport();
}

async function presentationTests() {
  const catalog = [
    ["complete", "Task complete", "Work · Project", "success"],
    ["stopped", "Task needs attention", "Work stopped · Project", "warning"],
    ["failed", "Task failed", "Work · Project", "danger"]
  ];
  for (const [type, title, body, tone] of catalog) {
    assert.deepEqual(notificationPresentation(page(1), change(1), type), {
      projectId: 1, taskId: 9, taskKey: "work", revision: 1,
      title, body, tone, actionLabel: "Open task"
    });
  }
  const long = notificationPresentation({ project_name: "😀".repeat(200) },
    { task_title: "A\n\t" + "B".repeat(500) }, "stopped");
  assert.equal(Array.from(long.body).length, 211);
  assert.ok(!long.body.includes("\n"));
  assert.ok(!long.body.includes("\ufffd"));
  const missing = notificationPresentation({}, { task_key: "internal-key" }, "complete");
  assert.equal(missing.body, "Task · Project");

  let current = page(0);
  const service = {
    authenticatedJsonGet: async () => ({ items: [{ id: 1 }] }),
    authenticatedJsonPost: async (url) => url.includes("tracker.get")
      ? { tasks: [{ project_id: 1, id: 9, key: "work" }] } : current
  };
  const notices = [];
  const { controller, opened } = setup({ service, onNotice: (notice) => notices.push(notice) });
  await controller.pollOnce();
  current = page(1, [change(1)]);
  await controller.pollOnce();
  const first = FakeNotification.instances.at(-1);
  current = page(2, [change(2, "in-progress", "in-progress", { after_delivery_status: "stopped" })]);
  await controller.pollOnce();
  assert.equal(first.closed, true);
  assert.deepEqual(notices.map((n) => n.tone), ["success", "warning"]);
  await controller.pollOnce();
  assert.equal(notices.length, 2);
  const latest = FakeNotification.instances.at(-1);
  latest.handlers.failed();
  assert.equal(notices.at(-1).nativeStatus, "failed");
  assert.equal(controller.state.nativeStatus, "failed");
  controller.stop();
  const count = notices.length;
  latest.handlers.failed();
  assert.equal(notices.length, count, "late native failure after stop must not publish");

  // Both native-unavailable and native-failed paths retain in-app delivery,
  // checkpoint the event and suppress it on replay.
  for (const Notification of [class { static isSupported() { return false; } },
    class { static isSupported() { return true; } constructor() { throw new Error("disabled"); } }]) {
    const received = [];
    current = page(0);
    const fallback = setup({ service, Notification, onNotice: (notice) => received.push(notice) }).controller;
    await fallback.pollOnce();
    current = page(1, [change(1)]);
    assert.equal((await fallback.pollOnce()).ok, true);
    await fallback.pollOnce();
    assert.equal(received.length, 1);
    assert.ok(["unsupported", "failed"].includes(received[0].nativeStatus));
  }

  // Exercise the real notification IPC caller with the controller's real target
  // validation, including foreign frame/window denies without reads/navigation.
  const handlers = {};
  const sent = [];
  const frame = { url: "http://127.0.0.1:5180/projects/1" };
  const contents = { mainFrame: frame, send: (...args) => sent.push(args) };
  const window = { isDestroyed: () => false, webContents: contents };
  const publish = registerNotificationIpc({
    ipcMain: { handle: (channel, fn) => { handlers[channel] = fn; } },
    controller, getWindow: () => window, trustedUrl: "http://127.0.0.1:5180/"
  });
  const open = handlers["stackos:notifications:open-task"];
  const target = { projectId: 1, taskId: 9, taskKey: "work" };
  assert.equal((await open({ sender: contents, senderFrame: frame }, target)).ok, true);
  assert.equal(opened.length, 1);
  for (const event of [{ sender: {}, senderFrame: frame },
    { sender: contents, senderFrame: { url: frame.url } },
    { sender: contents, senderFrame: null }]) {
    assert.equal((await open(event, target)).ok, false);
  }
  frame.url = "https://foreign.test/";
  assert.equal((await open({ sender: contents, senderFrame: frame }, target)).ok, false);
  publish(notices[0]);
  assert.equal(sent.length, 0);
  frame.url = "http://127.0.0.1:5180/";
  assert.equal((await open({ sender: contents, senderFrame: frame }, { ...target, projectId: 2 })).ok, false);
  assert.equal(opened.length, 1);
  publish(notices[0]);
  assert.equal(sent[0][0], "stackos:notifications:task");

  let exposed;
  let listener;
  let removed;
  const invokes = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../src/preload.js"), "utf8"), {
    require: () => ({ contextBridge: { exposeInMainWorld: (_name, api) => { exposed = api; } },
      ipcRenderer: { on: (_channel, fn) => { listener = fn; },
        removeListener: (_channel, fn) => { removed = fn; },
        invoke: (...args) => { invokes.push(args); } } })
  });
  let received;
  const unsubscribe = exposed.onTaskNotice((notice) => { received = notice; });
  listener({ privateElectronEvent: true }, notices[0]);
  assert.equal(received, notices[0]);
  exposed.openNotificationTask(target);
  assert.deepEqual(invokes[0], ["stackos:notifications:open-task", target]);
  unsubscribe();
  assert.equal(removed, listener);
}

async function transportClient() {
  const service = require("../src/service");
  assert.deepEqual(await service.authenticatedJsonGet("/projects"), { items: [] });
  assert.deepEqual(await service.authenticatedJsonPost("/changed", { arguments: { project_id: 7 } }),
    { current_rev: 8, changes: [] });
  await assert.rejects(service.authenticatedJsonPost("/missing", {}), { statusCode: 404 });
}

async function serviceTransport() {
  const tokenPath = path.join(temp(), "auth.token");
  fs.writeFileSync(tokenPath, "synthetic-test-token");
  const seen = [];
  const server = http.createServer(async (req, res) => {
    assert.equal(req.headers.authorization, "Bearer synthetic-test-token");
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    seen.push(req.method);
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/projects") res.end(JSON.stringify({ items: [] }));
    else if (req.url === "/changed") {
      assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()), { arguments: { project_id: 7 } });
      res.end(JSON.stringify({ current_rev: 8, changes: [] }));
    } else { res.statusCode = 404; res.end("{}"); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    await promisify(execFile)(process.execPath, [__filename, "--transport-client"], { env: {
      ...process.env, STACKOS_DESKTOP_DAEMON_HOST: "127.0.0.1",
      STACKOS_DESKTOP_DAEMON_PORT: String(server.address().port),
      STACKOS_DESKTOP_AUTH_TOKEN_PATH: tokenPath
    } });
    assert.deepEqual(seen, ["GET", "POST", "POST"]);
  } finally { await new Promise((resolve) => server.close(resolve)); }
}

// Real producer/REST envelope fixture supplied by the Python integration test.
// Only the HTTP adapter and OS Notification are substituted in this mode.
async function integrationFixture() {
  const fixture = JSON.parse(fs.readFileSync(0, "utf8"));
  let phase = 0;
  let target = fixture.snapshot;
  const notices = [];
  const { controller, dir, opened } = setup({ onNotice: (notice) => notices.push(notice), service: {
    authenticatedJsonGet: async () => fixture.projects,
    authenticatedJsonPost: async (url, body) => {
      if (url.includes("tracker.get")) return target;
      const cursor = body.arguments.since_rev;
      if (phase === 0) return fixture.baseline;
      return fixture.pages.find((item) => item.since === cursor).envelope;
    }
  } });
  assert.equal((await controller.pollOnce()).ok, true);
  assert.equal(FakeNotification.instances.length, 0);
  phase = 1;
  assert.equal((await controller.pollOnce()).ok, true);
  assert.deepEqual(FakeNotification.instances.map((item) => item.options.title),
    ["Task needs attention", "Task needs attention", "Task complete", "Task failed"]);
  assert.deepEqual(notices.map((item) => item.tone), ["warning", "warning", "success", "danger"]);
  assert.ok(notices.every((item, index) => item.projectId === fixture.project_id &&
    item.actionLabel === "Open task" && item.body === FakeNotification.instances[index].options.body &&
    !item.body.includes("private") && item.body.length < 230));
  await FakeNotification.instances[0].handlers.click();
  assert.deepEqual(opened, [stackosTaskDeepLink(fixture.project_id, "work")]);
  target = fixture.missing;
  await FakeNotification.instances[0].handlers.click();
  assert.equal(opened.length, 1);
  assert.equal(readNotificationState(dir).projectRevisionCursors[String(fixture.project_id)], fixture.final_rev);
  console.log("real producer/operation envelope/controller/deep-link fixture ok");
}

// Generate the real long-name/native-failure regression for the approved native
// browser route. The existing source preview supplies emitTaskNotice/clearNotices.
// Run: node desktop/scripts/test-notifications.cjs --stack-fixture /tmp/stack.js
// Then: stackos.browser --session <ref> eval /tmp/stack.js --tab-id <owned-tab>
async function stackFixture() {
  const target = process.argv[process.argv.indexOf("--stack-fixture") + 1];
  if (!target) throw new Error("--stack-fixture requires an output file");
  const notices = [1, 2, 3].map((id) => ({
    ...notificationPresentation({ project_id: 1, project_name: "W".repeat(200) }, {
      task_id: id, task_key: `work-${id}`, task_title: "W".repeat(200), rev: id
    }, "stopped"), nativeStatus: "failed"
  }));
  fs.writeFileSync(target, "(async () => { window.clearNotices();\n" + notices.map((notice) =>
    `window.emitTaskNotice(${JSON.stringify(notice)});`).join("\n") +
    `\nreturn await (${checkStackBounds.toString()})(); })()`);
}

async function checkStackBounds() {
  await new Promise(window.requestAnimationFrame);
  const notices = Array.from(document.querySelectorAll(".ui-toast"));
  if (notices.length !== 3) throw new Error("Expected three retained notices");
  const stack = notices[0].closest("ul");
  await Promise.all(Array.from(stack.children).flatMap((item) =>
    item.getAnimations().map((animation) => animation.finished)));
  const bounds = stack.getBoundingClientRect();
  if (bounds.top < 0 || bounds.bottom > window.innerHeight) {
    throw new Error(`Notification stack extends outside viewport: ${bounds.top}..${bounds.bottom}`);
  }
  for (const notice of notices) {
    const controls = Array.from(notice.querySelectorAll("button"));
    if (!notice.textContent.includes("Check system notification settings.") || controls.length !== 2) {
      throw new Error("Expected native fallback content and both notice controls");
    }
    for (const control of controls) {
      control.focus();
      await new Promise(window.requestAnimationFrame);
      const box = control.getBoundingClientRect();
      const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      if (box.top < bounds.top || box.bottom > bounds.bottom || !control.contains(hit)) {
        throw new Error("Retained notice control is clipped or cannot be reached");
      }
    }
  }
  return { passed: true, notices: notices.length, controls: 6,
    viewportHeight: window.innerHeight, stackHeight: bounds.height,
    clientHeight: stack.clientHeight, scrollHeight: stack.scrollHeight, scrollTop: stack.scrollTop };
}

(process.argv.includes("--transport-client") ? transportClient() :
  process.argv.includes("--integration-fixture") ? integrationFixture() :
  process.argv.includes("--stack-fixture") ? stackFixture() : unitTests())
  .then(() => console.log(process.argv.includes("--stack-fixture")
    ? "desktop notification stack fixture generated" : "desktop notifications test ok"))
  .catch((error) => { console.error(error); process.exitCode = 1; })
  .finally(() => { for (const dir of roots) fs.rmSync(dir, { recursive: true, force: true }); });
