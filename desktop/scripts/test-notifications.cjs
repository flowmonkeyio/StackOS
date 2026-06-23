"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  parseStackosDeepLink,
  resolveStackosDeepLink,
  stackosProjectTasksDeepLink,
  stackosTaskDeepLink
} = require("../src/deep-links");
const {
  TASK_STATUS_EVENT,
  createNotificationController,
  readNotificationState,
  showTestNotification,
  shouldNotifyEvent
} = require("../src/notifications");

const parsed = parseStackosDeepLink("stackos://projects/7/tasks?task=launch-check");
assert.deepEqual(parsed, {
  projectId: 7,
  taskKey: "launch-check",
  path: "/projects/7/tasks?task=launch-check"
});
assert.equal(
  resolveStackosDeepLink("stackos://projects/7/tasks?task=launch-check", "http://127.0.0.1:5180/"),
  "http://127.0.0.1:5180/projects/7/tasks?task=launch-check"
);
assert.equal(stackosTaskDeepLink(7, "launch-check"), "stackos://projects/7/tasks?task=launch-check");
assert.equal(stackosProjectTasksDeepLink(7), "stackos://projects/7/tasks");
assert.deepEqual(parseStackosDeepLink("stackos://projects/7/tasks"), {
  projectId: 7,
  taskKey: null,
  path: "/projects/7/tasks"
});
assert.equal(parseStackosDeepLink("https://example.com/projects/7/tasks?task=x"), null);
assert.equal(parseStackosDeepLink("stackos://projects/7/connections"), null);
assert.equal(parseStackosDeepLink("stackos://projects/7/tasks?task=../bad"), null);

assert.equal(
  shouldNotifyEvent({
    event_type: TASK_STATUS_EVENT,
    metadata_json: { new_status: "complete" }
  }),
  true
);
assert.equal(
  shouldNotifyEvent({
    event_type: "tracker.ticket.status_changed",
    metadata_json: { new_status: "complete" }
  }),
  false
);
assert.equal(
  shouldNotifyEvent({
    event_type: TASK_STATUS_EVENT,
    metadata_json: { new_status: "in-progress" }
  }),
  false
);

class FakeNotification {
  static instances = [];

  static isSupported() {
    return true;
  }

  constructor(options) {
    this.options = options;
    this.handlers = {};
    this.shown = false;
    FakeNotification.instances.push(this);
  }

  on(name, handler) {
    this.handlers[name] = handler;
  }

  show() {
    this.shown = true;
  }
}

const testNotificationOpened = [];
const testResult = showTestNotification({
  Notification: FakeNotification,
  openDeepLink: (deepLink) => {
    testNotificationOpened.push(deepLink);
  },
  projectId: 7
});
assert.deepEqual(testResult, { ok: true, deepLink: "stackos://projects/7/tasks" });
assert.equal(FakeNotification.instances.length, 1);
assert.equal(FakeNotification.instances[0].options.title, "StackOS notification test");
assert.equal(FakeNotification.instances[0].shown, true);
FakeNotification.instances[0].handlers.click();
assert.deepEqual(testNotificationOpened, ["stackos://projects/7/tasks"]);
FakeNotification.instances = [];

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-notifications-"));
const eventsByProject = new Map([
  [
    1,
    [
      {
        id: 4,
        project_id: 1,
        event_type: TASK_STATUS_EVENT,
        summary: "Task old-task changed from in-progress to complete.",
        metadata_json: {
          task_key: "old-task",
          task_title: "Old task",
          new_status: "complete"
        }
      }
    ]
  ]
]);
const opened = [];
const service = {
  authenticatedJsonGet: async (pathname) => {
    const url = new URL(pathname, "http://127.0.0.1:5180");
    if (url.pathname === "/api/v1/projects") {
      return { items: [{ id: 1 }] };
    }
    const match = url.pathname.match(/^\/api\/v1\/projects\/(\d+)\/context\/timeline$/);
    assert.ok(match, `unexpected URL ${pathname}`);
    const projectId = Number.parseInt(match[1], 10);
    const after = Number.parseInt(url.searchParams.get("after") || "0", 10);
    return {
      items: (eventsByProject.get(projectId) || []).filter((event) => event.id > after),
      next_cursor: null
    };
  }
};

const controller = createNotificationController({
  service,
  Notification: FakeNotification,
  openDeepLink: (deepLink) => {
    opened.push(deepLink);
  },
  userDataPath: tmp,
  setIntervalFn: () => 123,
  clearIntervalFn: () => {}
});

(async () => {
  try {
    await controller.pollOnce();
    assert.equal(FakeNotification.instances.length, 0);
    assert.equal(readNotificationState(tmp).projectEventWatermarks["1"], 4);

    eventsByProject.get(1).push({
      id: 5,
      project_id: 1,
      event_type: TASK_STATUS_EVENT,
      summary: "Task new-task changed from in-progress to complete.",
      metadata_json: {
        task_key: "new-task",
        task_title: "New task",
        new_status: "complete"
      }
    });

    await controller.pollOnce();
    assert.equal(FakeNotification.instances.length, 1);
    assert.equal(FakeNotification.instances[0].options.title, "Task complete: New task");
    assert.equal(FakeNotification.instances[0].shown, true);
    FakeNotification.instances[0].handlers.click();
    assert.deepEqual(opened, ["stackos://projects/1/tasks?task=new-task"]);
    assert.equal(readNotificationState(tmp).projectEventWatermarks["1"], 5);

    const overlapDir = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-notifications-overlap-"));
    let resolveProjects;
    let projectRequests = 0;
    const slowService = {
      authenticatedJsonGet: async (pathname) => {
        const url = new URL(pathname, "http://127.0.0.1:5180");
        if (url.pathname === "/api/v1/projects") {
          projectRequests += 1;
          await new Promise((resolve) => {
            resolveProjects = resolve;
          });
          return { items: [{ id: 1 }] };
        }
        return { items: [], next_cursor: null };
      }
    };
    const overlapController = createNotificationController({
      service: slowService,
      Notification: FakeNotification,
      openDeepLink: () => {},
      userDataPath: overlapDir
    });
    const firstPoll = overlapController.pollOnce();
    const secondPoll = overlapController.pollOnce();
    assert.equal(projectRequests, 1);
    resolveProjects();
    await Promise.all([firstPoll, secondPoll]);
    assert.equal(projectRequests, 1);
    fs.rmSync(overlapDir, { recursive: true, force: true });

    console.log("desktop notifications test ok");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
