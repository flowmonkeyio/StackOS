"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const { createBackupController, registerBackupIpc } = require("../src/backups");

const PRIVATE = "synthetic-private-recovery-material";
const RECIPIENT = "a".repeat(64);

function fixture(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-native-backups-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [], prompts = [], answers = [];
  const state = { configured: false, recipient_fingerprint: null, destination: path.join(root, "archives") };
  Object.assign(state, options.state || {});
  const arg = (args, name) => args[args.indexOf(name) + 1];
  const write = (file, value) => {
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    fs.writeFileSync(file, value, { flag: "wx", mode: 0o600 });
  };
  let reserved = false;
  const service = {
    DAEMON_URL: "http://127.0.0.1:5180/", authTokenPath: () => path.join(root, "state/auth.token"),
    async runStackos(args) {
      calls.push(args);
      if (options.beforeCommand) await options.beforeCommand(args);
      const failure = options.fail?.(args);
      if (failure) return { ok: false, stderr: `backup: ${failure}: ${PRIVATE}`, stdout: "" };
      let value;
      if (args[0] === "backups" && args[1] === "status") value = { ...state };
      else if (args[1] === "keygen") {
        assert.equal(state.configured, false);
        assert.ok(args.includes("--first-time"));
        reserved = true;
        write(arg(args, "--private-key-file"), PRIVATE);
        write(arg(args, "--public-key-file"), "synthetic-public-key");
        value = { private_key_path: arg(args, "--private-key-file"),
          public_key_path: arg(args, "--public-key-file"), recipient_fingerprint: RECIPIENT };
      } else if (args[1] === "custody") {
        assert.ok(args.includes("--reserve"));
        reserved = true;
        value = { reserved: true };
      } else if (args[1] === "initialize") {
        assert.ok(reserved);
        const encrypted = fs.readFileSync(arg(args, "--protected-key-file"));
        assert.ok(encrypted.length && !encrypted.includes(Buffer.from(PRIVATE)));
        state.configured = true;
        state.recipient_fingerprint = RECIPIENT;
        state.destination = arg(args, "--destination");
        value = { ...state };
      } else if (args[1] === "export-key") {
        const original = fs.readFileSync(arg(args, "--private-key-file"), "utf8");
        if (original !== PRIVATE) return { ok: false, stderr: "backup: key_mismatch: wrong key" };
        const output = arg(args, "--output");
        write(output, original);
        value = { path: output, recipient_fingerprint: RECIPIENT };
      } else if (args[1] === "export") {
        const output = arg(args, "--output");
        write(output, "synthetic-encrypted-archive");
        value = { path: output, sha256: "b".repeat(64), bytes: 27 };
      } else if (args[0] === "backup") {
        state.last_success = { path: path.join(state.destination, "latest.sbak") };
        value = state.last_success;
      } else if (args[0] === "restore") {
        const output = arg(args, "--output-dir");
        assert.equal(fs.existsSync(output), false);
        fs.mkdirSync(output, { mode: 0o700 });
        value = { output_dir: output, verified: true, adoption_required: true };
      } else throw new Error(`Unexpected command ${args[0]} ${args[1]}`);
      return { ok: true, stdout: JSON.stringify(value), stderr: "" };
    }
  };
  const safeStorage = {
    isEncryptionAvailable: () => options.available !== false,
    getSelectedStorageBackend: () => options.backend || "gnome_libsecret",
    encryptString(value) {
      assert.ok(reserved, "custody must be reserved before encryption");
      if (options.encryptFails) throw new Error(PRIVATE);
      return Buffer.from(`protected:${Buffer.from(value).toString("base64")}`);
    },
    decryptString(value) {
      if (!value.toString().startsWith("protected:")) throw new Error(PRIVATE);
      return Buffer.from(value.toString().slice(10), "base64").toString();
    }
  };
  const dialog = Object.fromEntries(["showOpenDialog", "showSaveDialog"].map((name) => [name,
    async (_window, opts) => {
      prompts.push({ name, opts });
      const answer = answers.shift();
      assert.notEqual(answer, undefined, "unexpected dialog");
      if (answer === null) return { canceled: true, filePaths: [] };
      return name === "showOpenDialog" ? { canceled: false, filePaths: [answer] }
        : { canceled: false, filePath: answer };
    }
  ]));
  const userData = path.join(root, "app");
  const opened = [];
  const inputs = { dialog, safeStorage, service, userData, installationKey: root,
    getWindow: () => ({}), platform: options.platform || "darwin",
    shell: { openPath: async (file) => { opened.push(file); return ""; } } };
  return { root, userData, state, calls, prompts, answers, inputs, opened, write,
    controller: createBackupController(inputs) };
}

for (const cancelAt of [0, 1]) test(`setup cancellation ${cancelAt} leaves no key or configuration`, async (t) => {
  const f = fixture(t);
  f.answers.push(...(cancelAt ? [f.state.destination, null] : [null]));
  const result = await f.controller.setupBackups();
  assert.equal(result.status, "cancelled");
  assert.deepEqual(f.calls.map((args) => args.slice(0, 2)), [["backups", "status"]]);
  assert.equal(fs.existsSync(f.userData), false);
});

test("existing setup never generates or changes a recovery key", async (t) => {
  const f = fixture(t, { state: { configured: true, recipient_fingerprint: RECIPIENT } });
  const result = await f.controller.setupBackups();
  assert.equal(result.code, "already_configured");
  assert.equal(f.prompts.length, 0);
  assert.equal(f.calls.length, 1);
});

test("setup saves a protected copy before initialization and exports after restart", async (t) => {
  const f = fixture(t);
  const portable = path.join(f.root, "portable.pem");
  f.answers.push(f.state.destination, portable);
  assert.equal((await f.controller.setupBackups()).status, "completed");
  const initialize = f.calls.find((args) => args[1] === "initialize");
  const encrypted = initialize[initialize.indexOf("--protected-key-file") + 1];
  assert.equal(fs.statSync(encrypted).mode & 0o777, 0o600);
  assert.equal(fs.statSync(path.dirname(encrypted)).mode & 0o777, 0o700);
  fs.unlinkSync(portable);
  f.calls.length = 0;
  const restarted = createBackupController(f.inputs);
  const copy = path.join(f.root, "exported.pem");
  f.answers.push(copy);
  const result = await restarted.exportRecoveryKey();
  assert.equal(result.status, "completed");
  assert.equal(fs.readFileSync(copy, "utf8"), PRIVATE);
  assert.ok(f.calls.findIndex((args) => args[1] === "custody") < f.calls.findIndex((args) => args[1] === "export-key"));
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
  assert.ok(!fs.readdirSync(path.dirname(encrypted)).some((name) => name.endsWith(".pem")));
});

test("missing legacy cache selects original without rotation and caches matching export", async (t) => {
  const f = fixture(t, { state: { configured: true, recipient_fingerprint: RECIPIENT } });
  const original = path.join(f.root, "original.pem"), output = path.join(f.root, "exported.pem");
  f.write(original, PRIVATE);
  f.answers.push(output, original);
  assert.equal((await f.controller.exportRecoveryKey()).status, "completed");
  assert.ok(!f.calls.some((args) => ["keygen", "initialize", "configure"].includes(args[1])));
  fs.unlinkSync(original);
  f.answers.push(path.join(f.root, "exported-again.pem"));
  assert.equal((await createBackupController(f.inputs).exportRecoveryKey()).status, "completed");
});

for (const options of [{ available: false }, { platform: "linux", backend: "basic_text" },
  { platform: "linux", backend: "unknown" }]) {
  test(`setup requires OS protected storage ${JSON.stringify(options)}`, async (t) => {
    const f = fixture(t, options);
    assert.equal((await f.controller.setupBackups()).code, "secure_storage_unavailable");
    assert.equal(f.prompts.length, 0);
    assert.equal(fs.existsSync(f.userData), false);
  });
}

test("encryption failure keeps the portable key and never enables setup", async (t) => {
  const f = fixture(t, { encryptFails: true });
  const portable = path.join(f.root, "portable.pem");
  f.answers.push(f.state.destination, portable);
  const result = await f.controller.setupBackups();
  assert.equal(result.status, "failed");
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
  assert.equal(fs.readFileSync(portable, "utf8"), PRIVATE);
  assert.equal(f.state.configured, false);
  assert.ok(!f.calls.some((args) => args[1] === "initialize"));
});

test("initialization conflict leaves existing setup and user recovery file intact", async (t) => {
  const f = fixture(t, { fail: (args) => args[1] === "initialize" ? "already_configured" : null });
  const portable = path.join(f.root, "portable.pem");
  f.answers.push(f.state.destination, portable);
  assert.equal((await f.controller.setupBackups()).code, "already_configured");
  assert.equal(fs.readFileSync(portable, "utf8"), PRIVATE);
  const initialize = f.calls.find((args) => args[1] === "initialize");
  assert.equal(fs.existsSync(initialize[initialize.indexOf("--protected-key-file") + 1]), false);
});

test("first backup failure is partial and never triggers replacement key generation", async (t) => {
  const f = fixture(t, { fail: (args) => args[0] === "backup" ? "backup_failed" : null });
  f.answers.push(f.state.destination, path.join(f.root, "portable.pem"));
  const result = await f.controller.setupBackups();
  assert.equal(result.status, "partial");
  assert.equal(result.code, "first_backup_failed");
  assert.equal(f.state.configured, true);
  assert.equal((await f.controller.setupBackups()).code, "already_configured");
  assert.equal(f.calls.filter((args) => args[1] === "keygen").length, 1);
});

test("corrupt cache requests original and never overwrites an existing cache", async (t) => {
  const f = fixture(t);
  const portable = path.join(f.root, "portable.pem");
  f.answers.push(f.state.destination, portable);
  assert.equal((await f.controller.setupBackups()).status, "completed");
  const initialize = f.calls.find((args) => args[1] === "initialize");
  const cache = initialize[initialize.indexOf("--protected-key-file") + 1];
  fs.writeFileSync(cache, "corrupted ciphertext");
  f.calls.length = 0;
  const output = path.join(f.root, "exported.pem");
  f.answers.push(output, portable);
  const result = await createBackupController(f.inputs).exportRecoveryKey();
  assert.equal(result.status, "completed");
  assert.equal(fs.readFileSync(output, "utf8"), PRIVATE);
  assert.equal(fs.readFileSync(cache, "utf8"), "corrupted ciphertext");
  assert.ok(!f.calls.some((args) => ["keygen", "initialize", "configure"].includes(args[1])));
});

test("wrong original key is rejected before caching and no error exposes bytes", async (t) => {
  const f = fixture(t, { state: { configured: true, recipient_fingerprint: RECIPIENT } });
  const wrong = path.join(f.root, "wrong.pem"), output = path.join(f.root, "copy.pem");
  f.write(wrong, "another synthetic key");
  f.answers.push(output, wrong);
  const result = await f.controller.exportRecoveryKey();
  assert.equal(result.code, "key_mismatch");
  assert.equal(fs.existsSync(output), false);
  assert.equal(fs.existsSync(f.userData), false);
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
});

test("failed reservation stops cached plaintext staging", async (t) => {
  const options = {};
  const f = fixture(t, options);
  f.answers.push(f.state.destination, path.join(f.root, "portable.pem"));
  assert.equal((await f.controller.setupBackups()).status, "completed");
  const initialize = f.calls.find((args) => args[1] === "initialize");
  const directory = path.dirname(initialize[initialize.indexOf("--protected-key-file") + 1]);
  options.fail = (args) => args[1] === "custody" ? "recovery_key_capture" : null;
  f.answers.push(path.join(f.root, "copy.pem"));
  const result = await f.controller.exportRecoveryKey();
  assert.equal(result.code, "recovery_key_capture");
  assert.ok(fs.readdirSync(directory).every((name) => name.endsWith(".enc")));
});

for (const method of ["exportLatestBackup", "exportRecoveryKey"]) {
  test(`${method} cancellation does not write`, async (t) => {
    const f = fixture(t, { state: { configured: true, recipient_fingerprint: RECIPIENT } });
    f.answers.push(null);
    assert.equal((await f.controller[method]()).status, "cancelled");
    assert.ok(f.calls.every((args) => args[1] === "status"));
    assert.equal(fs.existsSync(f.userData), false);
  });
}
test("legacy original-key cancellation does not write", async (t) => {
  const f = fixture(t, { state: { configured: true, recipient_fingerprint: RECIPIENT } });
  f.answers.push(path.join(f.root, "output.pem"), null);
  assert.equal((await f.controller.exportRecoveryKey()).status, "cancelled");
  assert.ok(f.calls.every((args) => args[1] === "status"));
  assert.equal(fs.existsSync(f.userData), false);
});

for (const cancelAt of [0, 1, 2]) test(`restore cancellation ${cancelAt} does not invoke CLI`, async (t) => {
  const f = fixture(t);
  const files = [path.join(f.root, "archive.sbak"), path.join(f.root, "key.pem")];
  f.answers.push(...files.slice(0, cancelAt), null);
  assert.equal((await f.controller.restoreBackup()).status, "cancelled");
  assert.equal(f.calls.length, 0);
});

test("restore selects a fresh output path and keeps live setup unchanged", async (t) => {
  const f = fixture(t, { state: { configured: true, recipient_fingerprint: RECIPIENT } });
  const before = JSON.stringify(f.state), output = path.join(f.root, "restored");
  f.answers.push(path.join(f.root, "archive.sbak"), path.join(f.root, "old-key.pem"), output);
  const result = await f.controller.restoreBackup();
  assert.equal(result.status, "completed");
  assert.equal(result.outputPath, output);
  assert.equal(f.prompts[2].name, "showSaveDialog");
  assert.equal(JSON.stringify(f.state), before);
  assert.equal(f.calls[0][0], "restore");
});

test("all actions share a busy fence", async (t) => {
  let release;
  const paused = new Promise((resolve) => { release = resolve; });
  const f = fixture(t, { beforeCommand: () => paused });
  const pending = f.controller.openBackupFolder();
  assert.equal((await f.controller.exportLatestBackup()).code, "backup_busy");
  assert.equal(f.prompts.length, 0);
  release();
  assert.equal((await pending).status, "completed");
  assert.deepEqual(f.opened, [f.state.destination]);
});

test("exports do not overwrite a selected existing output", async (t) => {
  const f = fixture(t);
  const output = path.join(f.root, "existing.sbak");
  f.write(output, "existing user file");
  f.answers.push(output);
  assert.equal((await f.controller.exportLatestBackup()).code, "output_exists");
  assert.equal(fs.readFileSync(output, "utf8"), "existing user file");
});

test("truncated CLI JSON and raw errors become bounded user messages", async (t) => {
  const f = fixture(t);
  f.inputs.service.runStackos = async () => ({ ok: true, stdout: '{"configured":' + PRIVATE });
  const result = await f.controller.setupBackups();
  assert.equal(result.status, "failed");
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
});

test("IPC rejects foreign windows, subframes, origins and arguments", async () => {
  const handlers = new Map();
  const frame = { url: "http://127.0.0.1:5180/setup" }, contents = { mainFrame: null };
  contents.mainFrame = frame;
  const window = { webContents: contents, isDestroyed: () => false };
  let calls = 0;
  const names = ["setupBackups", "openBackupFolder", "exportLatestBackup", "exportRecoveryKey", "restoreBackup"];
  const controller = Object.fromEntries(names.map((name) => [name, async () => { calls += 1; return { ok: true }; }]));
  registerBackupIpc({ ipcMain: { handle: (name, handler) => handlers.set(name, handler) }, controller,
    getWindow: () => window, trustedUrl: "http://127.0.0.1:5180/" });
  assert.equal(handlers.size, 5);
  for (const handler of handlers.values()) {
    const good = { sender: contents, senderFrame: frame };
    assert.equal((await handler(good)).ok, true);
    assert.equal((await handler(good, "/arbitrary/path")).code, "invalid_request");
    assert.equal((await handler({ ...good, sender: {} })).code, "invalid_request");
    assert.equal((await handler({ ...good, senderFrame: { ...frame } })).code, "invalid_request");
    frame.url = "https://untrusted.example/";
    assert.equal((await handler(good)).code, "invalid_request");
    frame.url = "http://127.0.0.1:5180/setup";
  }
  assert.equal(calls, 5);
});

test("preload exposes five argument-free backup methods", () => {
  let bridge;
  const invoked = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../src/preload.js"), "utf8"), {
    require: () => ({ contextBridge: { exposeInMainWorld: (_name, value) => { bridge = value; } },
      ipcRenderer: { invoke: (...args) => invoked.push(args) } })
  });
  for (const name of ["setupBackups", "openBackupFolder", "exportLatestBackup", "exportRecoveryKey", "restoreBackup"]) {
    bridge[name]("untrusted path", PRIVATE);
  }
  assert.equal(invoked.length, 5);
  assert.ok(invoked.every((args) => args.length === 1 && args[0].startsWith("stackos:backups:")));
});

test("runStackos output limit preserves default and clamps optional structured output", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-backup-output-"));
  const prior = process.env.STACKOS_DESKTOP_CLI;
  t.after(() => {
    if (prior === undefined) delete process.env.STACKOS_DESKTOP_CLI;
    else process.env.STACKOS_DESKTOP_CLI = prior;
    fs.rmSync(root, { recursive: true, force: true });
  });
  const cli = path.join(root, "synthetic-cli");
  fs.writeFileSync(cli, '#!/usr/bin/env node\nprocess.stdout.write("x".repeat(1100000));\n', { mode: 0o700 });
  process.env.STACKOS_DESKTOP_CLI = cli;
  const service = require("../src/service");
  for (const [options, expected] of [[{}, 12000], [{ outputLimit: 20000 }, 20000],
    [{ outputLimit: 1e12 }, 1048576], [{ outputLimit: -1 }, 12000], [{ outputLimit: Infinity }, 12000]]) {
    const response = await service.runStackos([], options);
    assert.equal(response.ok, true);
    assert.equal(response.stdout.indexOf("\n[trimmed"), expected);
  }
});
