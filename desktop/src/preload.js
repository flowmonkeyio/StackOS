"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("stackosDesktop", {
  setupBackups: () => ipcRenderer.invoke("stackos:backups:setup"),
  openBackupFolder: () => ipcRenderer.invoke("stackos:backups:open-folder"),
  exportLatestBackup: () => ipcRenderer.invoke("stackos:backups:export-latest"),
  exportRecoveryKey: () => ipcRenderer.invoke("stackos:backups:export-key"),
  restoreBackup: () => ipcRenderer.invoke("stackos:backups:restore"),
  status: () => ipcRenderer.invoke("stackos:status"),
  installOrRepair: () => ipcRenderer.invoke("stackos:install-or-repair"),
  restartService: () => ipcRenderer.invoke("stackos:restart-service"),
  runDoctor: () => ipcRenderer.invoke("stackos:doctor"),
  hostStatuses: () => ipcRenderer.invoke("stackos:mcp-host-status"),
  checkForUpdates: () => ipcRenderer.invoke("stackos:updates:check"),
  downloadUpdate: () => ipcRenderer.invoke("stackos:updates:download"),
  installUpdate: () => ipcRenderer.invoke("stackos:updates:install"),
  updateState: () => ipcRenderer.invoke("stackos:updates:state")
});
