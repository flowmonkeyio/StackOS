"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { app } = require("electron");
const { MacUpdater } = require("electron-updater");

function readJsonIfPresent(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (_error) {
    return null;
  }
}

function packagedUpdateConfigPath() {
  if (process.resourcesPath) {
    return path.join(process.resourcesPath, "update-config.json");
  }
  return null;
}

function devUpdateConfigPath() {
  return path.resolve(__dirname, "..", "update-config.json");
}

function configuredUpdateUrl() {
  if (process.env.STACKOS_UPDATE_URL) {
    return process.env.STACKOS_UPDATE_URL;
  }

  const configPaths = [packagedUpdateConfigPath(), devUpdateConfigPath()].filter(Boolean);
  for (const configPath of configPaths) {
    const config = readJsonIfPresent(configPath);
    if (config && config.updateUrl) {
      return config.updateUrl;
    }
  }
  return null;
}

function createUpdateController() {
  const updateUrl = configuredUpdateUrl();
  const state = {
    enabled: Boolean(updateUrl) && process.platform === "darwin",
    status: "idle",
    updateUrl,
    lastError: null,
    updateInfo: null
  };

  if (!state.enabled) {
    return {
      state,
      checkForUpdates: async () => ({
        ok: false,
        reason: updateUrl ? "updates are only enabled on macOS" : "update endpoint is not configured"
      }),
      downloadUpdate: async () => ({
        ok: false,
        reason: "update endpoint is not configured"
      }),
      quitAndInstall: () => ({
        ok: false,
        reason: "no update is ready to install"
      })
    };
  }

  const updater = new MacUpdater({
    provider: "generic",
    url: updateUrl
  });
  updater.autoDownload = false;
  updater.autoInstallOnAppQuit = false;

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
    state.status = "error";
    state.lastError = error.message;
  });

  return {
    state,
    checkForUpdates: async () => {
      const result = await updater.checkForUpdates();
      return {
        ok: true,
        updateInfo: result ? result.updateInfo : null,
        state
      };
    },
    downloadUpdate: async () => {
      await updater.downloadUpdate();
      return {
        ok: true,
        state
      };
    },
    quitAndInstall: () => {
      if (state.status !== "downloaded") {
        return {
          ok: false,
          reason: "no update is ready to install"
        };
      }
      app.removeAllListeners("window-all-closed");
      updater.quitAndInstall(false, true);
      return {
        ok: true
      };
    }
  };
}

module.exports = {
  configuredUpdateUrl,
  createUpdateController
};
