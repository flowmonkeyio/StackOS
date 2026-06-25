"use strict";

const { app, BrowserWindow, dialog, ipcMain, Menu, Notification, shell } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
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

let lastLoadingProgress = 0;
let splashLogoDataUri = null;

function getSplashLogo() {
  if (splashLogoDataUri === null) {
    try {
      const buffer = fs.readFileSync(path.join(__dirname, "stackos-splash.png"));
      splashLogoDataUri = `data:image/png;base64,${buffer.toString("base64")}`;
    } catch {
      splashLogoDataUri = "";
    }
  }
  return splashLogoDataUri;
}

function loadingHtml({ phase, progress, from = 0 }) {
  const clamp = (value) => Math.max(0, Math.min(100, Number(value) || 0));
  const target = Math.max(5, clamp(progress));
  const start = Math.min(target, clamp(from));
  const safePhase = escapeHtml(phase);
  const logo = getSplashLogo();
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>StackOS</title>
  <style>
    :root {
      --bg: #f7f7f8;
      --ink: #09090b;
      --fg: #27272a;
      --muted: #52525b;
      --track: #e4e4e7;
      --brand-a: #6366f1;
      --brand-b: #7c3aed;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      font-family: "Inter Variable", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      color: var(--fg);
      background:
        radial-gradient(78% 48% at 50% 40%, rgba(99, 102, 241, 0.08), rgba(99, 102, 241, 0) 62%),
        var(--bg);
      -webkit-font-smoothing: antialiased;
    }
    main {
      align-items: center;
      display: flex;
      min-height: 100vh;
      justify-content: center;
      padding: 32px;
    }
    .panel {
      width: 100%;
      max-width: 320px;
      text-align: center;
      animation: fade 280ms ease-out both;
    }
    .logo {
      width: 88px;
      height: 88px;
      display: block;
      margin: 0 auto 22px;
      filter: drop-shadow(0 14px 28px rgba(16, 16, 20, 0.24)) drop-shadow(0 2px 6px rgba(16, 16, 20, 0.14));
      animation: float 3.6s ease-in-out infinite;
    }
    .brand {
      color: var(--ink);
      font-size: 24px;
      font-weight: 600;
      letter-spacing: -0.011em;
      margin: 0 0 6px;
    }
    .tagline {
      color: var(--muted);
      font-size: 13px;
      margin: 0 0 32px;
    }
    .phase {
      font-size: 13px;
      font-weight: 500;
      margin: 0 0 12px;
      animation: fade 360ms ease-out both;
    }
    .track {
      position: relative;
      overflow: hidden;
      width: 100%;
      height: 6px;
      border-radius: 999px;
      background: var(--track);
    }
    .fill {
      height: 100%;
      width: ${target}%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--brand-a), var(--brand-b));
      animation: grow 720ms cubic-bezier(0.22, 0.61, 0.36, 1) both;
    }
    .sweep {
      position: absolute;
      top: 0;
      bottom: 0;
      left: 0;
      width: 42%;
      background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.6), transparent);
      animation: sweep 1.5s ease-in-out infinite;
    }
    @keyframes float {
      0%, 100% { transform: translateY(0); }
      50% { transform: translateY(-4px); }
    }
    @keyframes grow {
      from { width: ${start}%; }
      to { width: ${target}%; }
    }
    @keyframes sweep {
      0% { transform: translateX(-120%); }
      100% { transform: translateX(280%); }
    }
    @keyframes fade {
      from { opacity: 0; }
      to { opacity: 1; }
    }
    @media (prefers-reduced-motion: reduce) {
      .panel, .phase, .logo, .fill { animation: none !important; }
      .sweep { display: none; }
    }
  </style>
</head>
<body>
  <main>
    <section class="panel" aria-label="StackOS is starting">
      ${logo ? `<img class="logo" src="${logo}" alt="" aria-hidden="true">` : ""}
      <h1 class="brand">StackOS</h1>
      <p class="tagline">Everything runs on your computer</p>
      <p class="phase" aria-live="polite">${safePhase}</p>
      <div class="track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${target}" aria-label="${safePhase}">
        <div class="fill"></div>
        <div class="sweep" aria-hidden="true"></div>
      </div>
    </section>
  </main>
</body>
</html>`;
}

async function loadLoading(phase, progress) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  const target = Math.max(5, Math.min(100, Number(progress) || 5));
  const from = target >= lastLoadingProgress ? lastLoadingProgress : 0;
  lastLoadingProgress = target;
  await mainWindow.loadURL(
    `data:text/html;charset=utf-8,${encodeURIComponent(loadingHtml({ phase, progress: target, from }))}`
  );
}

function failureHtml(title, { summary, repair, details }) {
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
    p {
      color: #4f4a42;
      line-height: 1.55;
      margin: 0 0 16px;
    }
    .summary {
      background: #fff;
      border: 1px solid #ddd9cf;
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
    }
    .label {
      color: #6f6a60;
      font-size: 12px;
      font-weight: 650;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
      text-transform: uppercase;
    }
    details {
      background: #fff;
      border: 1px solid #ddd9cf;
      border-radius: 8px;
      padding: 16px;
    }
    summary {
      cursor: pointer;
      font-weight: 650;
    }
    pre {
      white-space: pre-wrap;
      margin: 16px 0 0;
      overflow: auto;
    }
  </style>
</head>
<body>
  <main>
    <h1>${escapeHtml(title)}</h1>
    <div class="summary">
      <div class="label">What happened</div>
      <p>${escapeHtml(summary)}</p>
      <div class="label">What to do</div>
      <p>${escapeHtml(repair)}</p>
    </div>
    <details>
      <summary>Technical details</summary>
      <pre>${escapeHtml(details)}</pre>
    </details>
  </main>
</body>
</html>`;
}

function payloadText(payload, key) {
  const value = payload && typeof payload === "object" ? payload[key] : null;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function commandError(result) {
  if (!result || typeof result !== "object") {
    return null;
  }
  return payloadText(result, "stderr") || payloadText(result, "error") || payloadText(result, "stdout");
}

function failureCopy(title, payload) {
  if (!payload || typeof payload !== "object") {
    return {
      summary: typeof payload === "string" && payload.trim() ? payload.trim() : title,
      repair: "Try Install or Repair from the StackOS menu. If it fails again, open the daemon log from the Service menu."
    };
  }

  const phase = payloadText(payload, "phase");
  const installError = commandError(payload.install);
  const startError = commandError(payload.start);
  const doctorError = commandError(payload.doctor);
  const readinessRepair =
    payload.readiness && typeof payload.readiness === "object"
      ? payloadText(payload.readiness, "repair")
      : null;

  if (phase === "restart") {
    return {
      summary: startError || "StackOS installed or repaired successfully, but the local service did not restart.",
      repair:
        readinessRepair ||
        "Use Service > Install or Repair once more. If the service was wedged, quit StackOS and reopen it after the repair finishes."
    };
  }
  if (phase === "install") {
    return {
      summary: installError || "StackOS could not finish installing the local runtime.",
      repair:
        readinessRepair ||
        "Make sure the app is in /Applications, then use Service > Install or Repair again."
    };
  }
  if (phase === "doctor") {
    return {
      summary: doctorError || "StackOS started, but doctor found a setup issue.",
      repair:
        readinessRepair ||
        "Open Service > Run Doctor for details, then use the Connections page to finish provider setup if needed."
    };
  }

  return {
    summary: startError || installError || doctorError || "StackOS could not finish preparing the local service.",
    repair:
      readinessRepair ||
      "Try Service > Restart Service, then Service > Run Doctor. If it fails again, open the daemon log from the Service menu."
  };
}

async function loadFailure(title, payload) {
  const details = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  const copy = failureCopy(title, payload);
  await mainWindow.loadURL(
    `data:text/html;charset=utf-8,${encodeURIComponent(
      failureHtml(title, {
        summary: copy.summary,
        repair: copy.repair,
        details
      })
    )}`
  );
}

async function prepareAndLoadStackOS({ forceInstall = false } = {}) {
  await loadLoading(forceInstall ? "Patching things up…" : "Checking your setup…", 20);
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

  await loadLoading("Powering up StackOS…", 70);
  const ready = await service.ensureDaemonReady();
  if (!ready.ok) {
    await loadFailure("StackOS service is not ready", ready);
    return;
  }

  await loadLoading("Opening your workspace…", 92);
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
    backgroundColor: "#f7f7f8",
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

  loadLoading("Getting things ready…", 10);
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

function updateVersionFrom(result) {
  const info =
    result && typeof result === "object"
      ? result.updateInfo || (result.state && result.state.updateInfo)
      : null;
  if (!info || typeof info !== "object") {
    return null;
  }
  return typeof info.version === "string" && info.version ? `v${info.version}` : null;
}

function updateFailureReason(result) {
  if (!result || typeof result !== "object") {
    return "StackOS could not complete the update action.";
  }
  return (
    result.reason ||
    result.lastError ||
    (result.state && (result.state.reason || result.state.lastError)) ||
    "StackOS could not complete the update action."
  );
}

function updateResultCopy(action, result) {
  const state = result && typeof result === "object" ? result.state || result : null;
  const status = state && typeof state === "object" ? state.status : null;
  const version = updateVersionFrom(result);

  if (!result || result.ok === false) {
    return {
      type: "error",
      message:
        action === "download"
          ? "Couldn’t download the update"
          : action === "install"
            ? "Update is not ready to install"
            : "Couldn’t check for updates",
      detail: updateFailureReason(result)
    };
  }

  if (action === "check") {
    if (status === "available") {
      return {
        type: "info",
        message: "Update available",
        detail: `${version || "A new StackOS version"} is available. Use the in-app update prompt or choose Updates > Download Update.`
      };
    }
    if (status === "not-available") {
      return {
        type: "info",
        message: "StackOS is up to date",
        detail: "No newer desktop update is available from the configured channel."
      };
    }
    if (status === "disabled") {
      return {
        type: "info",
        message: "Desktop updates are disabled",
        detail: updateFailureReason(result)
      };
    }
  }

  if (action === "download") {
    if (status === "downloaded") {
      return {
        type: "info",
        message: "Update ready to install",
        detail: `${version || "The update"} is ready. Use the in-app update prompt or choose Updates > Install Downloaded Update to restart into the new app.`
      };
    }
    return {
      type: "info",
      message: "Downloading update",
      detail: "StackOS started downloading the desktop update."
    };
  }

  return {
    type: "info",
    message: "Update action complete",
    detail: "StackOS finished the update action."
  };
}

async function showUpdateResult(action, result) {
  const copy = updateResultCopy(action, result);
  await dialog.showMessageBox(mainWindow, {
    type: copy.type,
    title: copy.message,
    message: copy.message,
    detail: copy.detail
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
            await showUpdateResult("check", await updateController.checkForUpdates());
          }
        },
        {
          label: "Download Update",
          click: async () => {
            await showUpdateResult("download", await updateController.downloadUpdate());
          }
        },
        {
          label: "Install Downloaded Update",
          click: () => {
            const result = updateController.quitAndInstall();
            if (!result.ok) {
              showUpdateResult("install", result);
            }
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
