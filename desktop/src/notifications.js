"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { stackosTaskDeepLink } = require("./deep-links");

const NOTIFICATION_STATE_FILE = "notification-state.json";
const TASK_STATUS_EVENT = "tracker.task.status_changed";
const COMPLETE_STATUS = "complete";
const DEFAULT_POLL_INTERVAL_MS = 15000;

function notificationStatePath(userDataPath) {
  return path.join(userDataPath, NOTIFICATION_STATE_FILE);
}

function readNotificationState(userDataPath) {
  try {
    return JSON.parse(fs.readFileSync(notificationStatePath(userDataPath), "utf8"));
  } catch (_error) {
    return {};
  }
}

function writeNotificationState(userDataPath, state) {
  fs.mkdirSync(userDataPath, { recursive: true });
  fs.writeFileSync(notificationStatePath(userDataPath), `${JSON.stringify(state, null, 2)}\n`);
}

function pageItems(payload) {
  return Array.isArray(payload?.items) ? payload.items : [];
}

function activeProjectIds(payload) {
  return pageItems(payload)
    .map((project) => project.id)
    .filter((id) => Number.isSafeInteger(id) && id > 0);
}

function shouldNotifyEvent(event) {
  const metadata = event?.metadata_json || {};
  return event?.event_type === TASK_STATUS_EVENT && metadata.new_status === COMPLETE_STATUS;
}

function deepLinkForEvent(event) {
  const metadata = event?.metadata_json || {};
  const projectId = event?.project_id;
  const taskKey = metadata.task_key;
  if (!Number.isSafeInteger(projectId) || typeof taskKey !== "string") {
    return null;
  }
  return stackosTaskDeepLink(projectId, taskKey);
}

function titleForEvent(event) {
  const metadata = event?.metadata_json || {};
  const taskTitle = typeof metadata.task_title === "string" ? metadata.task_title : null;
  const taskKey = typeof metadata.task_key === "string" ? metadata.task_key : null;
  return taskTitle || taskKey ? `Task complete: ${taskTitle || taskKey}` : "Task complete";
}

function bodyForEvent(event) {
  const metadata = event?.metadata_json || {};
  if (typeof metadata.task_key === "string" && typeof event?.summary === "string") {
    return event.summary;
  }
  return "A StackOS tracker task completed.";
}

function createNotificationController({
  service,
  Notification,
  openDeepLink,
  userDataPath,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  setIntervalFn = setInterval,
  clearIntervalFn = clearInterval
}) {
  const supported =
    Notification && typeof Notification.isSupported === "function"
      ? Notification.isSupported()
      : Boolean(Notification);
  const persisted = readNotificationState(userDataPath);
  const watermarks =
    persisted && typeof persisted.projectEventWatermarks === "object"
      ? { ...persisted.projectEventWatermarks }
      : {};
  const state = {
    enabled: supported,
    status: supported ? "idle" : "unsupported",
    lastError: null,
    lastPollAt: null,
    projectCount: 0,
    notifiedCount: 0
  };
  let timer = null;
  let pollInFlight = null;

  function saveWatermarks() {
    writeNotificationState(userDataPath, {
      projectEventWatermarks: watermarks,
      updatedAt: new Date().toISOString()
    });
  }

  function showNotification(event) {
    const deepLink = deepLinkForEvent(event);
    const notification = new Notification({
      title: titleForEvent(event),
      body: bodyForEvent(event),
      silent: false
    });
    if (deepLink && typeof notification.on === "function") {
      notification.on("click", () => openDeepLink(deepLink));
    }
    notification.show();
    state.notifiedCount += 1;
  }

  async function timelinePage(projectId, after) {
    const params = new URLSearchParams({
      event_type: TASK_STATUS_EVENT,
      limit: "50"
    });
    if (after !== null && after !== undefined) {
      params.set("after", String(after));
    }
    return service.authenticatedJsonGet(
      `/api/v1/projects/${projectId}/context/timeline?${params.toString()}`
    );
  }

  async function pollProject(projectId) {
    const previous = watermarks[String(projectId)];
    const firstPoll = previous === undefined || previous === null;
    let after = firstPoll ? null : Number(previous);
    let highWater = Number.isSafeInteger(after) ? after : 0;
    let hasNext = true;

    while (hasNext) {
      const page = await timelinePage(projectId, after);
      const items = pageItems(page);
      for (const event of items) {
        if (Number.isSafeInteger(event.id) && event.id > highWater) {
          highWater = event.id;
        }
        if (!firstPoll && shouldNotifyEvent(event)) {
          showNotification(event);
        }
      }
      after = page?.next_cursor || null;
      hasNext = Boolean(after);
    }

    watermarks[String(projectId)] = highWater;
  }

  async function runPollOnce() {
    if (!state.enabled) {
      return { ok: false, reason: "notifications are not supported", state };
    }
    state.status = "polling";
    state.lastError = null;
    try {
      const projects = await service.authenticatedJsonGet("/api/v1/projects?active_only=true&limit=100");
      const projectIds = activeProjectIds(projects);
      state.projectCount = projectIds.length;
      for (const projectId of projectIds) {
        await pollProject(projectId);
      }
      saveWatermarks();
      state.status = "idle";
      state.lastPollAt = new Date().toISOString();
      return { ok: true, state };
    } catch (error) {
      state.status = "error";
      state.lastError = error.message;
      return { ok: false, error: error.message, state };
    }
  }

  function pollOnce() {
    if (pollInFlight) {
      return pollInFlight;
    }
    pollInFlight = runPollOnce().finally(() => {
      pollInFlight = null;
    });
    return pollInFlight;
  }

  async function start() {
    if (!state.enabled) {
      return { ok: false, reason: "notifications are not supported", state };
    }
    if (timer) {
      return { ok: true, alreadyRunning: true, state };
    }
    const result = await pollOnce();
    timer = setIntervalFn(() => {
      pollOnce();
    }, pollIntervalMs);
    return result;
  }

  function stop() {
    if (timer) {
      clearIntervalFn(timer);
      timer = null;
    }
    state.status = state.enabled ? "idle" : "unsupported";
    return { ok: true, state };
  }

  return {
    state,
    pollOnce,
    start,
    stop
  };
}

module.exports = {
  DEFAULT_POLL_INTERVAL_MS,
  TASK_STATUS_EVENT,
  activeProjectIds,
  createNotificationController,
  deepLinkForEvent,
  notificationStatePath,
  readNotificationState,
  shouldNotifyEvent,
  writeNotificationState
};
