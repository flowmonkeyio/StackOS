"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createHash, randomUUID } = require("node:crypto");

const METHODS = Object.freeze({
  setupBackups: "stackos:backups:setup",
  openBackupFolder: "stackos:backups:open-folder",
  exportLatestBackup: "stackos:backups:export-latest",
  exportRecoveryKey: "stackos:backups:export-key",
  restoreBackup: "stackos:backups:restore"
});
const MESSAGES = Object.freeze({
  already_configured: "Backups are already set up. Your existing recovery key and settings were kept.",
  setup_conflict: "A different backup folder is already saved. Keep that folder or change it in settings.",
  recipient_changed: "The recovery key changed during this action. Refresh Backups and try again.",
  key_mismatch: "That recovery key does not match this setup. Select the original recovery key.",
  invalid_private_key: "Select an unencrypted StackOS recovery key file.",
  output_exists: "That file or folder already exists. Choose a new name.",
  unsafe_path: "Choose a regular private file or folder without symbolic links.",
  unsafe_destination: "Choose a location outside live data, enrolled folders and recovery key storage.",
  recovery_key_capture: "Keep recovery keys separate from backups and enrolled folders.",
  backup_busy: "Another backup action is running. Try again when it finishes.",
  backup_required: "Create a successful backup before exporting it.",
  key_required: "Set up backups before exporting a recovery key.",
  archive_changed: "The saved archive no longer matches its backup receipt. Create another backup.",
  secure_storage_unavailable: "Protected key storage is unavailable. Unlock your system keychain and try again.",
  invalid_request: "This backup action is only available from the StackOS desktop window.",
  backup_failed: "The backup action could not finish. Check Backups status and the selected files, then try again."
});

function result(status, message, code, outputPath) {
  return { ok: status === "completed", status, message,
    ...(code ? { code } : {}), ...(outputPath ? { outputPath } : {}) };
}

function failure(code = "backup_failed") {
  return result("failed", MESSAGES[code] || MESSAGES.backup_failed,
    Object.hasOwn(MESSAGES, code) ? code : "backup_failed");
}

function fail(code) {
  const error = new Error("Local backup action failed");
  error.backupCode = code;
  throw error;
}

function checkPath(file) {
  if (typeof file !== "string" || !path.isAbsolute(file)) fail("unsafe_path");
  for (let current = file; ; current = path.dirname(current)) {
    try {
      if (fs.lstatSync(current).isSymbolicLink() && !["/tmp", "/var", "/etc"].includes(current)) {
        fail("unsafe_path");
      }
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    if (path.dirname(current) === current) break;
  }
  return file;
}

function readSmall(file, privateMode = false) {
  checkPath(file);
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const before = fs.fstatSync(fd);
    if (!before.isFile() || before.nlink !== 1 || before.size > 8192 || !before.size ||
        (privateMode && (before.mode & 0o077))) fail("unsafe_path");
    const data = Buffer.alloc(before.size);
    if (fs.readSync(fd, data, 0, data.length, 0) !== data.length) fail("unsafe_path");
    const after = fs.fstatSync(fd), current = fs.lstatSync(file);
    if ([after, current].some((stat) => stat.ino !== before.ino || stat.dev !== before.dev ||
        stat.size !== before.size || stat.mtimeMs !== before.mtimeMs || stat.ctimeMs !== before.ctimeMs)) {
      fail("unsafe_path");
    }
    return data;
  } finally { fs.closeSync(fd); }
}

function privateDirectory(directory) {
  checkPath(directory);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const stat = fs.lstatSync(directory);
  if (!stat.isDirectory() || (stat.mode & 0o077)) fail("unsafe_path");
}

function writePrivate(file, value) {
  checkPath(file);
  const fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT |
    fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  try {
    fs.writeFileSync(fd, value);
    fs.fsyncSync(fd);
    const parent = fs.openSync(path.dirname(file), fs.constants.O_RDONLY |
      fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
    try { fs.fsyncSync(parent); } finally { fs.closeSync(parent); }
  } catch (error) {
    fs.unlinkSync(file);
    throw error;
  } finally { fs.closeSync(fd); }
}

function createBackupController({ dialog, safeStorage, service, shell, getWindow, userData,
  installationKey = [service.authTokenPath(), service.DAEMON_URL, process.env.STACKOS_DATA_DIR || ""].join("\0"),
  platform = process.platform }) {
  const installation = createHash("sha256").update(installationKey).digest("hex");
  const custody = path.join(userData, "backup-recovery", installation);
  let busy = false;

  const storageAvailable = () => {
    try {
      return safeStorage.isEncryptionAvailable() && (platform !== "linux" ||
        ["gnome_libsecret", "kwallet", "kwallet5", "kwallet6"].includes(safeStorage.getSelectedStorageBackend()));
    } catch { return false; }
  };
  const fingerprint = (value) => {
    if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) fail("backup_failed");
    return value;
  };
  const cachePath = (recipient) => path.join(custody, `${fingerprint(recipient)}.enc`);
  async function command(args) {
    const response = await service.runStackos(args, { timeoutMs: 900000, outputLimit: 1048576 });
    if (!response.ok) {
      const code = String(response.stderr || "").match(/backup: ([a-z_]+):/)?.[1];
      fail(code && Object.hasOwn(MESSAGES, code) ? code : "backup_failed");
    }
    try {
      const value = JSON.parse(response.stdout);
      if (!value || typeof value !== "object" || Array.isArray(value)) fail("backup_failed");
      return value;
    } catch { fail("backup_failed"); }
  }
  async function status() {
    const value = await command(["backups", "status"]);
    if (typeof value.configured !== "boolean") fail("backup_failed");
    checkPath(value.destination);
    if (value.configured) fingerprint(value.recipient_fingerprint);
    return value;
  }
  async function open(title, properties, filters) {
    const choice = await dialog.showOpenDialog(getWindow(), { title, properties, ...(filters ? { filters } : {}) });
    return choice.canceled ? null : checkPath(choice.filePaths?.[0]);
  }
  async function save(title, defaultPath, filters, buttonLabel = "Save") {
    const choice = await dialog.showSaveDialog(getWindow(), {
      title, defaultPath, buttonLabel, ...(filters ? { filters } : {}),
      properties: ["createDirectory", "showOverwriteConfirmation"]
    });
    return choice.canceled ? null : checkPath(choice.filePath);
  }
  const pem = [{ name: "Recovery key", extensions: ["pem"] }, { name: "All files", extensions: ["*"] }];
  const archive = [{ name: "StackOS backup", extensions: ["sbak", "sobak", "stackos-backup"] },
    { name: "All files", extensions: ["*"] }];
  const cancelled = () => result("cancelled", "Backup action cancelled.");
  async function reserve(recipient) {
    await command(["backups", "custody", "--reserve", "--protected-path", custody,
      "--expected-recipient", recipient]);
    privateDirectory(custody);
  }
  function removeOwned(file) {
    if (file) fs.rmSync(file, { force: true });
  }

  async function setupBackups() {
    const current = await status();
    if (current.configured) return failure("already_configured");
    if (!storageAvailable()) return failure("secure_storage_unavailable");
    const destination = await open("Choose a backup folder", ["openDirectory", "createDirectory"]);
    if (!destination) return cancelled();
    const portable = await save("Save your recovery key separately from backups", "StackOS recovery key.pem", pem);
    if (!portable) return cancelled();
    const publicFile = path.join(custody, `public-${randomUUID()}.pem`);
    let generated = false, attemptedInitialize = false, recipient, encryptedFile, ownsCache = false;
    try {
      const keys = await command(["backups", "keygen", "--first-time", "--private-key-file", portable,
        "--public-key-file", publicFile, "--protected-path", custody, "--destination", destination]);
      generated = true;
      recipient = fingerprint(keys.recipient_fingerprint);
      privateDirectory(custody);
      if (!storageAvailable()) fail("secure_storage_unavailable");
      const portableKey = readSmall(portable).toString("utf8");
      let encrypted;
      try { encrypted = safeStorage.encryptString(portableKey); }
      catch { fail("secure_storage_unavailable"); }
      if (!Buffer.isBuffer(encrypted) || !encrypted.length || encrypted.length > 8192) fail("secure_storage_unavailable");
      encryptedFile = cachePath(recipient);
      writePrivate(encryptedFile, encrypted);
      ownsCache = true;
      attemptedInitialize = true;
      await command(["backups", "initialize", "--public-key-file", publicFile,
        "--private-key-file", portable, "--protected-key-file", encryptedFile, "--destination", destination]);
      await command(["backup"]);
      return result("completed", "Backups are enabled and the first backup is saved. Keep your recovery key and an archive copy off this device.", undefined, destination);
    } catch (error) {
      if (attemptedInitialize) {
        try {
          const latest = await status();
          if (latest.configured && latest.recipient_fingerprint === recipient) {
            return result("partial", "Backups are set up, but the first backup could not be confirmed. Use Back up now to retry.", "first_backup_failed");
          }
        } catch {
          // The commit may have succeeded. Keep both copies until status can be read.
          return result("partial", "Setup could not be confirmed. Keep your recovery file and refresh Backups before trying again.", "setup_unconfirmed");
        }
      }
      if (ownsCache) removeOwned(encryptedFile);
      if (generated) {
        const failed = failure(error.backupCode || (error.code === "EEXIST" ? "output_exists" : undefined));
        failed.message += " Your saved recovery file was kept. Check Backups status before trying again.";
        return failed;
      }
      throw error;
    } finally { if (generated) removeOwned(publicFile); }
  }

  async function openBackupFolder() {
    const current = await status();
    if (await shell.openPath(current.destination)) fail("backup_failed");
    return result("completed", "Backup folder opened.", undefined, current.destination);
  }

  async function exportLatestBackup() {
    const output = await save("Export latest backup", "StackOS backup.sbak", archive);
    if (!output) return cancelled();
    await command(["backups", "export", "--output", output]);
    return result("completed", "Encrypted backup exported. Keep a copy off this device.", undefined, output);
  }

  async function exportRecoveryKey() {
    const current = await status();
    if (!current.configured) return failure("key_required");
    const recipient = fingerprint(current.recipient_fingerprint);
    const output = await save("Export recovery key separately from backups", "StackOS recovery key.pem", pem);
    if (!output) return cancelled();
    const encryptedFile = cachePath(recipient);
    let plaintext;
    if (storageAvailable()) {
      try { plaintext = safeStorage.decryptString(readSmall(encryptedFile, true)); } catch { /* Ask for the original below. */ }
    }
    if (typeof plaintext === "string" && plaintext.length && Buffer.byteLength(plaintext) <= 8192) {
      await reserve(recipient);
      const staging = path.join(custody, `export-${randomUUID()}.pem`);
      let ownsStaging = false;
      try {
        writePrivate(staging, plaintext);
        ownsStaging = true;
        await command(["backups", "export-key", "--private-key-file", staging,
          "--output", output, "--expected-recipient", recipient]);
        return result("completed", "Recovery key exported. Store it separately from your archives.", undefined, output);
      } catch (error) {
        if (!["key_mismatch", "invalid_private_key"].includes(error.backupCode)) throw error;
      } finally { if (ownsStaging) removeOwned(staging); }
    }
    const original = await open("Select the original recovery key for this setup", ["openFile"], pem);
    if (!original) return cancelled();
    await command(["backups", "export-key", "--private-key-file", original,
      "--output", output, "--expected-recipient", recipient]);
    let savedCopy = false;
    if (storageAvailable()) {
      try {
        await reserve(recipient);
        const encrypted = safeStorage.encryptString(readSmall(output, true).toString("utf8"));
        if (!Buffer.isBuffer(encrypted) || !encrypted.length || encrypted.length > 8192) fail("secure_storage_unavailable");
        writePrivate(encryptedFile, encrypted);
        savedCopy = true;
      } catch { /* The portable export succeeded. Never clobber an existing convenience copy. */ }
    }
    return result("completed", savedCopy
      ? "Recovery key exported. A protected copy is available for future exports."
      : "Recovery key exported. Keep the original available; the app could not save a protected copy.",
    undefined, output);
  }

  async function restoreBackup() {
    const source = await open("Choose a backup to recover", ["openFile"], archive);
    if (!source) return cancelled();
    const key = await open("Choose the recovery key for this archive", ["openFile"], pem);
    if (!key) return cancelled();
    const output = await save("Choose a new recovery folder", "StackOS recovery", undefined, "Restore here");
    if (!output) return cancelled();
    const recovered = await command(["restore", source, "--private-key-file", key, "--output-dir", output]);
    if (recovered.verified !== true || recovered.adoption_required !== true) fail("backup_failed");
    return result("completed", "Backup verified and recovered into a separate folder. Your current installation is unchanged.", undefined, output);
  }

  return Object.fromEntries(Object.entries({ setupBackups, openBackupFolder, exportLatestBackup,
    exportRecoveryKey, restoreBackup }).map(([name, action]) => [name, async (...args) => {
    if (args.length) return failure("invalid_request");
    if (busy) return failure("backup_busy");
    busy = true;
    try { return await action(); }
    catch (error) { return failure(error.backupCode || (error.code === "EEXIST" ? "output_exists" : undefined)); }
    finally { busy = false; }
  }]));
}

function registerBackupIpc({ ipcMain, controller, getWindow, trustedUrl }) {
  for (const [method, channel] of Object.entries(METHODS)) {
    ipcMain.handle(channel, async (event, ...args) => {
      const window = getWindow();
      try {
        if (args.length || !window || window.isDestroyed() || event.sender !== window.webContents ||
            !event.senderFrame || event.senderFrame !== window.webContents.mainFrame ||
            new URL(event.senderFrame.url).origin !== new URL(trustedUrl).origin) {
          return failure("invalid_request");
        }
      } catch { return failure("invalid_request"); }
      return controller[method]();
    });
  }
}

module.exports = { createBackupController, registerBackupIpc };
