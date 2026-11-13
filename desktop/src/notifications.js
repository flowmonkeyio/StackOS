"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { stackosTaskDeepLink } = require("./deep-links");

const NOTIFICATION_STATE_FILE = "notification-state.json";
const DEFAULT_POLL_INTERVAL_MS = 15000;
const PAGE_SIZE = 50;
const MAX_PAGES_PER_PROJECT = 4;
const TERMINAL_STATUSES = new Set(["complete", "failed", "aborted", "deferred", "skipped"]);

function notificationStatePath(userDataPath) {
  return path.join(userDataPath, NOTIFICATION_STATE_FILE);
}

function readNotificationState(userDataPath) {
  try {
    const value = JSON.parse(fs.readFileSync(notificationStatePath(userDataPath), "utf8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch (_error) {
    return {};
  }
}

function writeNotificationState(userDataPath, state) {
  fs.mkdirSync(userDataPath, { recursive: true });
  const target = notificationStatePath(userDataPath);
  fs.writeFileSync(`${target}.tmp`, `${JSON.stringify(state, null, 2)}\n`);
  fs.renameSync(`${target}.tmp`, target);
}

function pageItems(payload) {
  return Array.isArray(payload?.items) ? payload.items : [];
}

function positiveId(value) {
  return Number.isSafeInteger(value) && value > 0;
}

function activeProjectIds(payload) {
  return pageItems(payload).map((project) => project.id).filter(positiveId);
}

// Failure and stop revisions for one retained incident produce one notice.
function notificationType(change) {
  if (!change?.before_status || !change?.after_status) return null;
  if (change.before_status !== change.after_status) {
    if (change.after_status === "complete") return "complete";
    if (change.after_status === "failed" && change.before_delivery_status !== "stopped") return "failed";
  }
  if (!TERMINAL_STATUSES.has(change.after_status) &&
      change.after_delivery_status === "stopped" && change.before_delivery_status !== "stopped") {
    return "stopped";
  }
  return null;
}

function label(value, fallback) {
  const clean = typeof value === "string" ? value.replace(/[\x00-\x1f\x7f]/g, " ").trim() : "";
  return (clean || fallback).slice(0, 200);
}

function operationData(payload) {
  if (payload?.ok === false) throw new Error("StackOS notification read failed");
  return payload?.data || payload;
}

function createNotificationController({
  service, Notification, openDeepLink, userDataPath,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  setIntervalFn = setInterval, clearIntervalFn = clearInterval
}) {
  const supported = Notification && typeof Notification.isSupported === "function"
    ? Notification.isSupported() : Boolean(Notification);
  const persisted = readNotificationState(userDataPath);
  // Event IDs and revisions are different namespaces. Preserve legacy state as
  // history and baseline an independent, versioned revision cursor.
  const cursors = persisted.cursorVersion === 2 && persisted.projectRevisionCursors &&
    typeof persisted.projectRevisionCursors === "object" && !Array.isArray(persisted.projectRevisionCursors)
    ? { ...persisted.projectRevisionCursors } : {};
  const state = {
    enabled: supported, status: supported ? "idle" : "unsupported",
    lastError: null, lastPollAt: null, projectCount: 0, notifiedCount: 0
  };
  let timer = null;
  let pollInFlight = null;
  let generation = 0;

  function checkpoint(projectId, rev) {
    cursors[String(projectId)] = rev;
    writeNotificationState(userDataPath, {
      ...persisted, cursorVersion: 2, projectRevisionCursors: cursors,
      updatedAt: new Date().toISOString()
    });
  }

  function callOperation(name, args) {
    return service.authenticatedJsonPost(`/api/v1/operations/${name}/call`, {
      arguments: { ...args, response_mode: "raw" }
    }).then(operationData);
  }

  async function openTarget(projectId, change, deepLink) {
    try {
      const snapshot = await callOperation("tracker.get", {
        project_id: projectId, task_key: change.task_key, task_index_only: true,
        include_graph: false, projection: "ui", metric_demand: { task_keys: [] }
      });
      const task = snapshot?.tasks?.find((item) => item.id === change.task_id &&
        item.project_id === projectId && item.key === change.task_key);
      if (task) openDeepLink(deepLink);
    } catch (_error) {
      // Missing/foreign/reused targets or service loss cannot select other work.
    }
  }

  function showNotification(project, change, type) {
    const deepLink = positiveId(change.task_id) && stackosTaskDeepLink(project.project_id, change.task_key);
    if (!deepLink) return;
    const titles = { complete: "Task complete", stopped: "Task needs attention", failed: "Task failed" };
    const task = label(change.task_title, change.task_key);
    const name = label(project.project_name, `Project ${project.project_id}`);
    const notification = new Notification({
      title: titles[type], body: `${task}${type === "stopped" ? " stopped" : ""} · ${name}`, silent: false
    });
    if (typeof notification.on === "function") {
      notification.on("click", () => openTarget(project.project_id, change, deepLink));
    }
    notification.show();
    state.notifiedCount += 1;
  }

  async function pollProject(projectId, epoch) {
    const stored = cursors[String(projectId)];
    let after = Number.isSafeInteger(stored) && stored >= 0 ? stored : null;
    for (let pageIndex = 0; pageIndex < MAX_PAGES_PER_PROJECT; pageIndex += 1) {
      const page = await callOperation("tracker.changed", {
        project_id: projectId, projection: "notifications", since_rev: after, limit: PAGE_SIZE
      });
      if (epoch !== generation) return;
      if (page?.projection !== "notifications" || page.project_id !== projectId ||
          !Number.isSafeInteger(page.current_rev) || page.current_rev < (after ?? 0) ||
          !Array.isArray(page.changes) || page.changes.length > PAGE_SIZE) {
        throw new Error("Invalid StackOS notification page");
      }
      const next = page.next_cursor;
      const end = next ?? page.current_rev;
      if (!Number.isSafeInteger(end) || end > page.current_rev ||
          (next !== null && next !== undefined && end <= (after ?? -1))) {
        throw new Error("Invalid StackOS notification cursor");
      }
      if (after !== null) {
        for (const change of page.changes) {
          const current = cursors[String(projectId)] ?? after;
          if (!positiveId(change.rev) || change.rev <= current || change.rev > end) continue;
          const type = notificationType(change);
          if (type) showNotification(page, change, type);
          checkpoint(projectId, change.rev);
        }
      }
      checkpoint(projectId, end);
      if (next === null || next === undefined) return;
      after = end;
    }
  }

  async function runPollOnce(epoch) {
    if (!state.enabled) return { ok: false, reason: "notifications are not supported", state };
    state.status = "polling";
    state.lastError = null;
    state.projectCount = 0;
    try {
      let after = null;
      const seen = new Set();
      do {
        const params = new URLSearchParams({ active_only: "true", limit: "100" });
        if (after !== null) params.set("after", String(after));
        const projects = await service.authenticatedJsonGet(`/api/v1/projects?${params}`);
        if (epoch !== generation) return { ok: false, reason: "stopped", state };
        for (const projectId of activeProjectIds(projects)) {
          if (seen.has(projectId)) continue;
          seen.add(projectId);
          state.projectCount += 1;
          await pollProject(projectId, epoch);
          if (epoch !== generation) return { ok: false, reason: "stopped", state };
        }
        const next = projects?.next_cursor ?? null;
        if (next !== null && (!positiveId(next) || next === after || seen.has(`cursor:${next}`))) {
          throw new Error("Invalid StackOS project cursor");
        }
        if (next !== null) seen.add(`cursor:${next}`);
        after = next;
      } while (after !== null);
      state.status = "idle";
      state.lastPollAt = new Date().toISOString();
      return { ok: true, state };
    } catch (error) {
      if (epoch !== generation) return { ok: false, reason: "stopped", state };
      state.status = "error";
      state.lastError = error.message;
      return { ok: false, error: error.message, state };
    }
  }

  function pollOnce() {
    if (pollInFlight) return pollInFlight;
    pollInFlight = runPollOnce(generation).finally(() => { pollInFlight = null; });
    return pollInFlight;
  }

  async function start() {
    if (!state.enabled) return { ok: false, reason: "notifications are not supported", state };
    if (timer) return { ok: true, alreadyRunning: true, state };
    const epoch = generation;
    const result = await pollOnce();
    if (epoch === generation && !timer) timer = setIntervalFn(() => { pollOnce(); }, pollIntervalMs);
    return result;
  }

  function stop() {
    generation += 1;
    if (timer) clearIntervalFn(timer);
    timer = null;
    state.status = state.enabled ? "idle" : "unsupported";
    return { ok: true, state };
  }

  return { state, pollOnce, start, stop };
}

module.exports = {
  DEFAULT_POLL_INTERVAL_MS, activeProjectIds, createNotificationController,
  notificationStatePath, notificationType, readNotificationState, writeNotificationState
};
