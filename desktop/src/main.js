"use strict";

const { app, BrowserWindow, dialog, ipcMain, Menu, Notification, shell } = require("electron");
const path = require("node:path");
const service = require("./service");
const { resolveStackosDeepLink } = require("./deep-links");
const { createNotificationController } = require("./notifications");
const { createUpdateController } = require("./updates");

let mainWindow = null;
let updateController = null;
let notificationController = null;
let pendingDeepLink = null;

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function isStackosUrl(candidate) {
  try {
    const parsed = new URL(candidate);
    const allowed = new URL(service.DAEMON_URL);
    return (
      parsed.protocol === allowed.protocol &&
      ["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname) &&
      String(parsed.port || "80") === String(allowed.port || "80")
    );
  } catch (_error) {
    return false;
  }
}

function openExternalUrl(candidate) {
  try {
    const parsed = new URL(candidate);
    if (["http:", "https:", "mailto:"].includes(parsed.protocol)) {
      shell.openExternal(candidate);
    }
  } catch (_error) {
    // Ignore malformed external navigation attempts.
  }
}

function loadStackosDeepLink(candidate) {
  const targetUrl = resolveStackosDeepLink(candidate, service.DAEMON_URL);
  if (!targetUrl) {
    return false;
  }
  if (!mainWindow || mainWindow.isDestroyed()) {
    if (!app.isReady()) {
      pendingDeepLink = candidate;
      return true;
    }
    createWindow();
  }
  mainWindow.loadURL(targetUrl);
  mainWindow.show();
  mainWindow.focus();
  return true;
}

function handleStackosDeepLink(candidate) {
  if (!app.isReady()) {
    pendingDeepLink = candidate;
    return true;
  }
  return loadStackosDeepLink(candidate);
}

function failureHtml(title, details) {
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>StackOS</title>
  <style>
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f7f7f5;
      color: #151515;
    }
    main {
      max-width: 760px;
      margin: 12vh auto;
      padding: 0 32px;
    }
    h1 {
      font-size: 28px;
      font-weight: 650;
      margin: 0 0 16px;
    }
    pre {
      white-space: pre-wrap;
      background: #fff;
      border: 1px solid #ddd9cf;
      border-radius: 8px;
      padding: 16px;
      overflow: auto;
    }
  </style>
</head>
<body>
  <main>
    <h1>${escapeHtml(title)}</h1>
    <pre>${escapeHtml(details)}</pre>
  </main>
</body>
</html>`;
}

async function loadFailure(title, payload) {
  const details = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  await mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(failureHtml(title, details))}`);
}

async function prepareAndLoadStackOS({ forceInstall = false } = {}) {
  const install = await service.prepareInstalledVersion({
    version: app.getVersion(),
    userDataPath: app.getPath("userData"),
    payloadInfo: service.readPackagedBuildInfo(),
    force: forceInstall
  });
  if (!install.ok) {
    await loadFailure("StackOS install or repair failed", install);
    return;
  }

  const ready = await service.ensureDaemonReady();
  if (!ready.ok) {
    await loadFailure("StackOS service is not ready", ready);
    return;
  }

  await mainWindow.loadURL(service.DAEMON_URL);
  if (pendingDeepLink) {
    const candidate = pendingDeepLink;
    pendingDeepLink = null;
    loadStackosDeepLink(candidate);
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    title: "StackOS",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isStackosUrl(url)) {
      return { action: "allow" };
    }
    openExternalUrl(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isStackosUrl(url)) {
      event.preventDefault();
      openExternalUrl(url);
    }
  });

  return mainWindow;
}

async function showCommandResult(title, result) {
  await dialog.showMessageBox(mainWindow, {
    type: result.ok ? "info" : "error",
    title,
    message: title,
    detail: JSON.stringify(result, null, 2).slice(0, 6000)
  });
}

function createMenu() {
  const template = [
    {
      label: "StackOS",
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "quit" }
      ]
    },
    {
      label: "Service",
      submenu: [
        {
          label: "Open StackOS",
          click: () => prepareAndLoadStackOS()
        },
        {
          label: "Install or Repair",
          click: async () => {
            await prepareAndLoadStackOS({ forceInstall: true });
          }
        },
        {
          label: "Restart Service",
          click: async () => {
            const result = await service.restartDaemon();
            await showCommandResult("Restart Service", result);
            if (result.ok) {
              await prepareAndLoadStackOS();
            }
          }
        },
        {
          label: "Run Doctor",
          click: async () => {
            await showCommandResult("Doctor", await service.runDoctor());
          }
        },
        {
          label: "Open Daemon Log",
          click: () => {
            shell.openPath(service.daemonLogPath());
          }
        }
      ]
    },
    {
      label: "Updates",
      submenu: [
        {
          label: "Check for Updates",
          click: async () => {
            await showCommandResult("Check for Updates", await updateController.checkForUpdates());
          }
        },
        {
          label: "Download Update",
          click: async () => {
            await showCommandResult("Download Update", await updateController.downloadUpdate());
          }
        },
        {
          label: "Install Downloaded Update",
          click: () => {
            updateController.quitAndInstall();
          }
        }
      ]
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" }
      ]
    }
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function registerIpc() {
  ipcMain.handle("stackos:status", async () => ({
    health: await service.checkHealth(),
    command: service.resolveStackosCommand(),
    payload: service.readPackagedBuildInfo(),
    notifications: notificationController ? notificationController.state : null
  }));
  ipcMain.handle("stackos:install-or-repair", async () => {
    const result = await service.installOrRepair();
    if (result.ok) {
      await prepareAndLoadStackOS({ forceInstall: true });
    }
    return result;
  });
  ipcMain.handle("stackos:restart-service", async () => service.restartDaemon());
  ipcMain.handle("stackos:doctor", async () => service.runDoctor());
  ipcMain.handle("stackos:updates:state", async () => updateController.state);
  ipcMain.handle("stackos:updates:check", async () => updateController.checkForUpdates());
  ipcMain.handle("stackos:updates:download", async () => updateController.downloadUpdate());
  ipcMain.handle("stackos:updates:install", async () => updateController.quitAndInstall());
}

async function startNotifications() {
  if (!notificationController) {
    return;
  }
  const result = await notificationController.start();
  if (!result.ok && result.reason !== "notifications are not supported") {
    console.warn("StackOS notifications did not start:", result.error || result.reason);
  }
}

app.on("open-url", (event, url) => {
  event.preventDefault();
  handleStackosDeepLink(url);
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
    if (pendingDeepLink) {
      const candidate = pendingDeepLink;
      pendingDeepLink = null;
      loadStackosDeepLink(candidate);
    } else {
      prepareAndLoadStackOS();
    }
  }
});

app.on("before-quit", () => {
  if (notificationController) {
    notificationController.stop();
  }
});

app.whenReady().then(async () => {
  app.setName("StackOS");
  app.setAsDefaultProtocolClient("stackos");
  updateController = createUpdateController();
  notificationController = createNotificationController({
    service,
    Notification,
    openDeepLink: handleStackosDeepLink,
    userDataPath: app.getPath("userData")
  });
  registerIpc();
  createMenu();
  createWindow();
  await prepareAndLoadStackOS();
  await startNotifications();
});
