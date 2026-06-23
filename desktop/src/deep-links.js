"use strict";

const STACKOS_PROTOCOL = "stackos:";
const TASK_KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/;

function normalizeStackosPath(parsed) {
  if (parsed.protocol !== STACKOS_PROTOCOL) {
    return null;
  }

  if (parsed.hostname === "projects") {
    return `/projects${parsed.pathname}`;
  }
  return parsed.pathname;
}

function parseStackosDeepLink(candidate) {
  let parsed;
  try {
    parsed = new URL(candidate);
  } catch (_error) {
    return null;
  }

  const pathname = normalizeStackosPath(parsed);
  if (!pathname) {
    return null;
  }

  const match = pathname.match(/^\/projects\/(\d+)\/tasks\/?$/);
  if (!match) {
    return null;
  }

  const taskKey = parsed.searchParams.get("task");
  if (taskKey && !TASK_KEY_PATTERN.test(taskKey)) {
    return null;
  }

  const projectId = Number.parseInt(match[1], 10);
  if (!Number.isSafeInteger(projectId) || projectId <= 0) {
    return null;
  }

  const path = taskKey
    ? `/projects/${projectId}/tasks?task=${encodeURIComponent(taskKey)}`
    : `/projects/${projectId}/tasks`;
  return {
    projectId,
    taskKey,
    path
  };
}

function resolveStackosDeepLink(candidate, daemonUrl) {
  const parsed = parseStackosDeepLink(candidate);
  if (!parsed) {
    return null;
  }
  return new URL(parsed.path, daemonUrl).toString();
}

function stackosTaskDeepLink(projectId, taskKey) {
  if (!Number.isSafeInteger(projectId) || projectId <= 0) {
    return null;
  }
  if (!taskKey || !TASK_KEY_PATTERN.test(taskKey)) {
    return null;
  }
  return `stackos://projects/${projectId}/tasks?task=${encodeURIComponent(taskKey)}`;
}

module.exports = {
  STACKOS_PROTOCOL,
  parseStackosDeepLink,
  resolveStackosDeepLink,
  stackosTaskDeepLink
};
