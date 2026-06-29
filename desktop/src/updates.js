"use strict";

const fs = require("node:fs");
const path = require("node:path");

function readJsonIfPresent(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (_error) {
    return null;
  }
}

function packagedUpdateConfigPath(resourcesPath = process.resourcesPath) {
  if (resourcesPath) {
    return path.join(resourcesPath, "update-config.json");
  }
  return null;
}

function devUpdateConfigPath(desktopDir = path.resolve(__dirname, "..")) {
  return path.join(desktopDir, "update-config.json");
}

function configuredUpdateUrl(options = {}) {
  const env = options.env || process.env;
  if (env.STACKOS_UPDATE_URL) {
    return env.STACKOS_UPDATE_URL;
  }

  const configPaths =
    options.configPaths ||
    [
      packagedUpdateConfigPath(options.resourcesPath),
      devUpdateConfigPath(options.desktopDir)
    ].filter(Boolean);
  for (const configPath of configPaths) {
    const config = readJsonIfPresent(configPath);
    if (config && config.updateUrl) {
      return config.updateUrl;
    }
  }
  return null;
}

function isLocalhost(hostname) {
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname);
}

function updateUrlPolicy(updateUrl) {
  if (!updateUrl) {
    return {
      ok: false,
      reason: "update endpoint is not configured"
    };
  }

  try {
    const parsed = new URL(updateUrl);
    if (parsed.protocol === "https:") {
      return { ok: true };
    }
    if (parsed.protocol === "http:" && isLocalhost(parsed.hostname)) {
      return { ok: true, local: true };
    }
  } catch (_error) {
    return {
      ok: false,
      reason: "update endpoint is not a valid URL"
    };
  }

  return {
    ok: false,
    reason: "update endpoint must use HTTPS unless it is localhost for local testing"
  };
}

function loadUpdaterLogger() {
  try {
    const logger = require("electron-log");
    if (logger.transports && logger.transports.file) {
      logger.transports.file.level = "info";
    }
    return logger;
  } catch (_error) {
    return console;
  }
}

function createMacUpdater(updateUrl, options) {
  if (options.updater) {
    return options.updater;
  }
  if (options.updaterFactory) {
    return options.updaterFactory({
      provider: "generic",
      url: updateUrl
    });
  }
  const { MacUpdater } = require("electron-updater");
  return new MacUpdater({
    provider: "generic",
    url: updateUrl
  });
}

function disabledReason(updateUrl, platform, urlPolicy) {
  if (platform !== "darwin" && updateUrl) {
    return "updates are only enabled on macOS";
  }
  return urlPolicy.reason;
}

function createDisabledController(state, reason) {
  return {
    state,
    checkForUpdates: async () => ({
      ok: false,
      reason,
      state
    }),
    downloadUpdate: async () => ({
      ok: false,
      reason,
      state
    }),
    quitAndInstall: () => ({
      ok: false,
      reason: "no update is ready to install",
      state
    })
  };
}

function recordError(state, error) {
  state.status = "error";
  state.lastError = error && error.message ? error.message : String(error);
}

function createUpdateController(options = {}) {
  const updateUrl = configuredUpdateUrl(options);
  const platform = options.platform || process.platform;
  const urlPolicy = updateUrlPolicy(updateUrl);
  const state = {
    enabled: Boolean(updateUrl) && platform === "darwin" && urlPolicy.ok,
    status: "idle",
    updateUrl,
    lastError: null,
    updateInfo: null,
    progress: null,
    reason: null
  };

  if (!state.enabled) {
    state.status = "disabled";
    state.reason = disabledReason(updateUrl, platform, urlPolicy);
    return createDisabledController(state, state.reason);
  }

  const updater = createMacUpdater(updateUrl, options);
  updater.autoDownload = false;
  updater.logger = options.logger || loadUpdaterLogger();

  updater.on("checking-for-update", () => {
    state.status = "checking";
    state.lastError = null;
  });
  updater.on("update-available", (info) => {
    state.status = "available";
    state.updateInfo = info;
  });
  updater.on("update-not-available", (info) => {
    state.status = "not-available";
    state.updateInfo = info;
  });
  updater.on("download-progress", (progress) => {
    state.status = "downloading";
    state.progress = progress;
  });
  updater.on("update-downloaded", (info) => {
    state.status = "downloaded";
    state.updateInfo = info;
  });
  updater.on("error", (error) => {
    recordError(state, error);
  });

  return {
    state,
    checkForUpdates: async () => {
      try {
        const result = await updater.checkForUpdates();
        return {
          ok: true,
          updateInfo: result ? result.updateInfo : null,
          state
        };
      } catch (error) {
        recordError(state, error);
        return {
          ok: false,
          reason: state.lastError,
          state
        };
      }
    },
    downloadUpdate: async () => {
      if (state.status === "downloaded") {
        return {
          ok: true,
          state
        };
      }
      if (state.status !== "available") {
        return {
          ok: false,
          reason: "check for updates before downloading",
          state
        };
      }
      try {
        await updater.downloadUpdate();
        return {
          ok: true,
          state
        };
      } catch (error) {
        recordError(state, error);
        return {
          ok: false,
          reason: state.lastError,
          state
        };
      }
    },
    quitAndInstall: () => {
      if (state.status !== "downloaded") {
        return {
          ok: false,
          reason: "download the update before installing",
          state
        };
      }
      try {
        updater.quitAndInstall();
        state.status = "installing";
        return {
          ok: true,
          state
        };
      } catch (error) {
        recordError(state, error);
        return {
          ok: false,
          reason: state.lastError,
          state
        };
      }
    }
  };
}

module.exports = {
  configuredUpdateUrl,
  updateUrlPolicy,
  createUpdateController
};
