"use strict";

function noop() {}

function buildApplicationMenuTemplate(options = {}) {
  const {
    appLabel = "StackOS",
    onOpenStackOS = noop,
    onInstallOrRepair = noop,
    onRestartService = noop,
    onRunDoctor = noop,
    onOpenDaemonLog = noop,
    onOpenGettingStarted = noop,
    onCheckForUpdates = noop,
    onDownloadUpdate = noop,
    onInstallUpdate = noop,
    platform = process.platform
  } = options;

  return [
    {
      label: appLabel,
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "quit" }
      ]
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        ...(platform === "darwin" ? [{ role: "pasteAndMatchStyle" }] : []),
        { role: "delete" },
        { type: "separator" },
        { role: "selectAll" }
      ]
    },
    {
      label: "Service",
      submenu: [
        {
          label: "Open StackOS",
          click: onOpenStackOS
        },
        {
          label: "Install or Repair",
          click: onInstallOrRepair
        },
        {
          label: "Restart Service",
          click: onRestartService
        },
        {
          label: "Run Doctor",
          click: onRunDoctor
        },
        {
          label: "Open Daemon Log",
          click: onOpenDaemonLog
        }
      ]
    },
    {
      label: "Updates",
      submenu: [
        {
          label: "Check for Updates",
          click: onCheckForUpdates
        },
        {
          label: "Download Update",
          click: onDownloadUpdate
        },
        {
          label: "Install Downloaded Update",
          click: onInstallUpdate
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
    },
    {
      label: "Help",
      role: "help",
      submenu: [
        {
          label: "Getting Started",
          click: onOpenGettingStarted
        }
      ]
    }
  ];
}

module.exports = {
  buildApplicationMenuTemplate
};
