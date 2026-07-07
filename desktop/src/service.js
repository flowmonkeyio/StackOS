"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const DAEMON_HOST = process.env.STACKOS_DESKTOP_DAEMON_HOST || "127.0.0.1";
const DAEMON_PORT = Number.parseInt(process.env.STACKOS_DESKTOP_DAEMON_PORT || "5180", 10);
const DAEMON_URL = `http://${DAEMON_HOST}:${DAEMON_PORT}/`;
const HEALTH_URL = `http://${DAEMON_HOST}:${DAEMON_PORT}/api/v1/health`;
const INSTALL_STATE_FILE = "install-state.json";
const PAYLOAD_BUILD_INFO_FILE = "build-info.json";
const STACKOS_STATE_DIR =
  process.env.STACKOS_STATE_DIR || path.join(os.homedir(), ".local", "state", "stackos");
const AUTH_TOKEN_PATH =
  process.env.STACKOS_DESKTOP_AUTH_TOKEN_PATH || path.join(STACKOS_STATE_DIR, "auth.token");
let packagedRuntime = false;

function isExecutable(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch (_error) {
    return false;
  }
}

function repoRoot() {
  return path.resolve(__dirname, "..", "..");
}

function packagedStackosRoot() {
  if (!process.resourcesPath) {
    return null;
  }
  return path.join(process.resourcesPath, "stackos");
}

function packagedStackosPath() {
  const root = packagedStackosRoot();
  if (!root) {
    return null;
  }
  return path.join(root, "bin", "stackos");
}

function readPackagedBuildInfo() {
  const root = packagedStackosRoot();
  if (!root) {
    return null;
  }
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(root, PAYLOAD_BUILD_INFO_FILE), "utf8"));
    if (!raw || typeof raw !== "object") {
      return null;
    }
    return {
      name: typeof raw.name === "string" ? raw.name : "stackos",
      version: typeof raw.version === "string" ? raw.version : null,
      buildId: typeof raw.buildId === "string" ? raw.buildId : null,
      builtAt: typeof raw.builtAt === "string" ? raw.builtAt : null
    };
  } catch (_error) {
    return null;
  }
}

function resolveStackosCommand() {
  if (process.env.STACKOS_DESKTOP_CLI) {
    return {
      command: process.env.STACKOS_DESKTOP_CLI,
      baseArgs: [],
      mode: "env"
    };
  }

  const packaged = packagedStackosPath();
  if (packaged && isExecutable(packaged)) {
    return {
      command: packaged,
      baseArgs: [],
      mode: "packaged"
    };
  }

  if (packagedRuntime) {
    return {
      command: null,
      baseArgs: [],
      mode: "packaged-missing",
      error: `packaged StackOS CLI is missing or not executable at ${packaged || packagedStackosPath()}`
    };
  }

  const root = repoRoot();
  const venvPython = path.join(root, ".venv", "bin", "python");
  if (isExecutable(venvPython)) {
    return {
      command: venvPython,
      baseArgs: ["-m", "stackos"],
      mode: "repo-venv"
    };
  }

  return {
    command: "stackos",
    baseArgs: [],
    mode: "path"
  };
}

function setPackagedRuntime(value) {
  packagedRuntime = Boolean(value);
}

function trimOutput(value, limit = 12000) {
  if (!value) {
    return "";
  }
  if (value.length <= limit) {
    return value;
  }
  return `${value.slice(0, limit)}\n[trimmed ${value.length - limit} chars]`;
}

function normalizeLifecycleOptions(value, defaultTimeoutSeconds) {
  if (typeof value === "number") {
    return {
      timeoutSeconds: value
    };
  }
  if (value && typeof value === "object") {
    return {
      ...value,
      timeoutSeconds: Number.isFinite(value.timeoutSeconds)
        ? value.timeoutSeconds
        : defaultTimeoutSeconds
    };
  }
  return {
    timeoutSeconds: defaultTimeoutSeconds
  };
}

async function reportProgress(options, phase, progress) {
  if (!options || typeof options.onProgress !== "function") {
    return;
  }
  try {
    await options.onProgress({
      phase,
      progress
    });
  } catch (_error) {
    // Startup progress should never make lifecycle work fail.
  }
}

function reportInstallLineProgress(options, line) {
  const progressByPattern = [
    [/Install mode/i, ["Checking install mode...", 32]],
    [/Bootstrap state ready/i, ["Preparing local state...", 36]],
    [/Database schema/i, ["Updating local database...", 42]],
    [/Browser runtime/i, ["Checking browser runtime...", 48]],
    [/Installed \d+ skills/i, ["Installing agent skills...", 54]],
    [/Installed \d+ plugins/i, ["Installing plugins...", 58]],
    [/marketplace/i, ["Registering plugin marketplace...", 60]],
    [/(Codex|Claude|Gemini|MCP)/i, ["Registering app connections...", 63]],
    [/launchd|autostart/i, ["Installing autostart...", 66]]
  ];
  for (const [pattern, [phase, progress]] of progressByPattern) {
    if (pattern.test(line)) {
      void reportProgress(options, phase, progress);
      return;
    }
  }
}

function createInstallProgressReporter(options) {
  let buffer = "";
  return (chunk) => {
    buffer += chunk;
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || "";
    for (const line of lines) {
      reportInstallLineProgress(options, line);
    }
  };
}

function runStackos(args, options = {}) {
  const resolved = resolveStackosCommand();
  const timeoutMs = options.timeoutMs || 120000;
  const cwd = options.cwd || repoRoot();
  if (resolved.error || !resolved.command) {
    return Promise.resolve({
      ok: false,
      commandMode: resolved.mode,
      args,
      exitCode: null,
      signal: null,
      stdout: "",
      stderr: resolved.error || "StackOS command is not available"
    });
  }
  const env = {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONNOUSERSITE: "1",
    PYTHONUNBUFFERED: "1"
  };
  if (resolved.mode === "packaged") {
    const root = packagedStackosRoot();
    env.PYTHONHOME = path.join(root, ".venv");
    env.PLAYWRIGHT_BROWSERS_PATH = path.join(root, "ms-playwright");
    env.STACKOS_PACKAGED_CLI = packagedStackosPath();
    delete env.PYTHONPATH;
    delete env.VIRTUAL_ENV;
    delete env.__PYVENV_LAUNCHER__;
  }
  return new Promise((resolve) => {
    const child = childProcess.spawn(resolved.command, [...resolved.baseArgs, ...args], {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"]
    });

    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      const text = chunk.toString();
      stdout += text;
      if (typeof options.onStdout === "function") {
        options.onStdout(text);
      }
    });
    child.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      stderr += text;
      if (typeof options.onStderr === "function") {
        options.onStderr(text);
      }
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({
        ok: false,
        commandMode: resolved.mode,
        args,
        exitCode: null,
        stdout: "",
        stderr: error.message
      });
    });
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      resolve({
        ok: code === 0,
        commandMode: resolved.mode,
        args,
        exitCode: code,
        signal,
        stdout: trimOutput(stdout),
        stderr: trimOutput(stderr)
      });
    });
  });
}

function parseDoctorPayload(result) {
  const output = `${result.stdout || ""}\n${result.stderr || ""}`;
  const lines = output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index];
    if (!line.startsWith("{")) {
      continue;
    }
    try {
      const parsed = JSON.parse(line);
      if (
        parsed &&
        typeof parsed === "object" &&
        typeof parsed.ok === "boolean" &&
        typeof parsed.code === "number" &&
        parsed.checks &&
        typeof parsed.checks === "object"
      ) {
        return parsed;
      }
    } catch (_error) {
      // Keep scanning for the JSON envelope.
    }
  }
  return null;
}

function readinessFromDoctor(result) {
  const parsed = result.parsed || parseDoctorPayload(result);
  if (!parsed) {
    return {
      ok: false,
      status: "doctor-unparsed",
      code: result.exitCode,
      repair: "run `stackos doctor --json` from a terminal"
    };
  }
  const provider = parsed.info?.provider_readiness || null;
  return {
    ok: parsed.ok,
    status: parsed.ok ? "ready" : "needs-repair",
    code: parsed.code,
    checks: parsed.checks,
    providerReadiness: provider
  };
}

function checkHealth(timeoutMs = 1000) {
  return new Promise((resolve) => {
    const req = http.get(HEALTH_URL, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        resolve({
          ok: res.statusCode >= 200 && res.statusCode < 300,
          statusCode: res.statusCode,
          body: trimOutput(body, 2000)
        });
      });
    });

    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error("health check timed out"));
    });
    req.on("error", (error) => {
      resolve({
        ok: false,
        error: error.message
      });
    });
  });
}

function authTokenPath() {
  return AUTH_TOKEN_PATH;
}

function readAuthToken() {
  return fs.readFileSync(authTokenPath(), "utf8").trim();
}

function daemonJsonGet(pathname, { auth = false, timeoutMs = 5000 } = {}) {
  return new Promise((resolve, reject) => {
    let url;
    try {
      url = new URL(pathname, DAEMON_URL);
    } catch (error) {
      reject(error);
      return;
    }

    const headers = {};
    if (auth) {
      headers.Authorization = `Bearer ${readAuthToken()}`;
    }

    const req = http.get(url, { headers }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          const error = new Error(`StackOS request failed with HTTP ${res.statusCode}`);
          error.statusCode = res.statusCode;
          error.body = trimOutput(body, 2000);
          reject(error);
          return;
        }
        if (!body.trim()) {
          resolve(null);
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });

    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error("StackOS request timed out"));
    });
    req.on("error", reject);
  });
}

function authenticatedJsonGet(pathname, options = {}) {
  return daemonJsonGet(pathname, { ...options, auth: true });
}

async function waitForHealth(timeoutMs = 20000) {
  const startedAt = Date.now();
  let last = await checkHealth();
  while (!last.ok && Date.now() - startedAt < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    last = await checkHealth();
  }
  return last;
}

async function startDaemon(options = 20) {
  const lifecycle = normalizeLifecycleOptions(options, 20);
  const timeoutSeconds = lifecycle.timeoutSeconds;
  await reportProgress(lifecycle, "Starting local service...", 58);
  const result = await runStackos(["start", "--timeout", String(timeoutSeconds)], {
    timeoutMs: (timeoutSeconds + 5) * 1000
  });
  if (!result.ok) {
    return result;
  }
  await reportProgress(lifecycle, "Waiting for local service...", 72);
  const health = await waitForHealth(timeoutSeconds * 1000);
  return {
    ...result,
    health
  };
}

async function restartDaemon(options = 20) {
  const lifecycle = normalizeLifecycleOptions(options, 20);
  const timeoutSeconds = lifecycle.timeoutSeconds;
  await reportProgress(lifecycle, "Restarting local service...", 62);
  const result = await runStackos(["restart", "--timeout", String(timeoutSeconds)], {
    timeoutMs: (timeoutSeconds + 10) * 1000
  });
  if (!result.ok) {
    return result;
  }
  await reportProgress(lifecycle, "Waiting for local service...", 74);
  const health = await waitForHealth(timeoutSeconds * 1000);
  return {
    ...result,
    health
  };
}

function parseJsonResult(result) {
  if (!result || !result.stdout) {
    return null;
  }
  try {
    return JSON.parse(result.stdout);
  } catch (_error) {
    return null;
  }
}

async function autostartStatus(options = 20) {
  const lifecycle = normalizeLifecycleOptions(options, 20);
  const timeoutSeconds = lifecycle.timeoutSeconds;
  const result = await runStackos(["autostart", "status", "--json"], {
    timeoutMs: timeoutSeconds * 1000
  });
  return {
    ...result,
    parsed: parseJsonResult(result)
  };
}

function packagedLaunchdIsCurrent(status) {
  const root = packagedStackosRoot();
  if (!root || !status || !status.ok || !status.parsed) {
    return false;
  }
  const message = String(status.parsed.launchctl_message || "");
  return (
    status.parsed.plist_present === true &&
    status.parsed.launchd_loaded === true &&
    message.includes(path.join(root, ".venv", "bin", "python")) &&
    message.includes("STACKOS_PACKAGED_CLI") &&
    message.includes("PYTHONHOME") &&
    message.includes("PLAYWRIGHT_BROWSERS_PATH")
  );
}

async function ensureDaemonReady(options = 20) {
  const lifecycle = normalizeLifecycleOptions(options, 20);
  const timeoutSeconds = lifecycle.timeoutSeconds;
  await reportProgress(lifecycle, "Checking local service...", 48);
  const health = await checkHealth();
  if (health.ok) {
    await reportProgress(lifecycle, "Local service is running...", 78);
    return {
      ok: true,
      alreadyRunning: true,
      health
    };
  }
  return startDaemon({
    ...lifecycle,
    timeoutSeconds
  });
}

async function installOrRepair(options = 240) {
  const lifecycle = normalizeLifecycleOptions(options, 240);
  const timeoutSeconds = lifecycle.timeoutSeconds;
  const shouldRunDoctor = lifecycle.runDoctor !== false;
  const installArgs =
    process.platform === "darwin"
      ? ["install", "--launchd", "--force", "--skip-doctor"]
      : ["install", "--skip-doctor"];
  await reportProgress(lifecycle, "Preparing local files...", 30);
  const install = await runStackos(installArgs, {
    timeoutMs: timeoutSeconds * 1000,
    onStdout: createInstallProgressReporter(lifecycle)
  });
  if (!install.ok) {
    return {
      ok: false,
      phase: "install",
      install
    };
  }
  const start = await restartDaemon({
    ...lifecycle,
    timeoutSeconds: 20
  });
  const startReady = start.ok && start.health && start.health.ok;
  let doctor = null;
  if (startReady && shouldRunDoctor) {
    await reportProgress(lifecycle, "Running doctor...", 84);
    doctor = await runDoctor();
  }
  const readiness = doctor
    ? readinessFromDoctor(doctor)
    : startReady
      ? {
          ok: true,
          status: "health-ready",
          code: null,
          repair: null
        }
      : {
          ok: false,
          status: start.ok ? "health-failed" : "restart-failed",
          code: null,
          repair: start.ok
            ? "wait for the StackOS service, then run doctor"
            : "restart the StackOS service, then run doctor"
        };
  if (startReady && (!doctor || doctor.ok)) {
    await reportProgress(lifecycle, "Local service is ready...", 88);
  }
  return {
    ok: startReady && (!doctor || doctor.ok),
    phase: !start.ok ? "restart" : startReady ? (doctor && !doctor.ok ? "doctor" : "ready") : "health",
    install,
    start,
    doctor,
    readiness
  };
}

async function repairMcpRegistrations(options = 60) {
  const lifecycle = normalizeLifecycleOptions(options, 60);
  const timeoutSeconds = lifecycle.timeoutSeconds;
  await reportProgress(lifecycle, "Refreshing app connections...", 32);
  return runStackos(["install", "--mcp-only", "--skip-doctor"], {
    timeoutMs: timeoutSeconds * 1000,
    onStdout: createInstallProgressReporter(lifecycle)
  });
}

async function repairPreparedInstall(options = 180) {
  const lifecycle = normalizeLifecycleOptions(options, 180);
  const timeoutSeconds = lifecycle.timeoutSeconds;
  const commandInfo = resolveStackosCommand();
  if (process.platform !== "darwin" || commandInfo.mode !== "packaged") {
    return repairMcpRegistrations(lifecycle);
  }

  await reportProgress(lifecycle, "Checking autostart...", 20);
  const status = await autostartStatus({
    ...lifecycle,
    timeoutSeconds: 20
  });
  if (packagedLaunchdIsCurrent(status)) {
    return repairMcpRegistrations(lifecycle);
  }

  await reportProgress(lifecycle, "Repairing autostart...", 46);
  const install = await runStackos(["install", "--launchd", "--force", "--skip-doctor"], {
    timeoutMs: timeoutSeconds * 1000,
    onStdout: createInstallProgressReporter(lifecycle)
  });
  if (!install.ok) {
    return {
      ok: false,
      phase: "prepared-launchd-repair",
      status,
      install
    };
  }
  const restart = await restartDaemon({
    ...lifecycle,
    timeoutSeconds: 20
  });
  return {
    ok: restart.ok,
    phase: restart.ok ? "prepared-launchd-repaired" : "prepared-launchd-restart",
    status,
    install,
    restart
  };
}

function installStatePath(userDataPath) {
  return path.join(userDataPath, INSTALL_STATE_FILE);
}

function readInstallState(userDataPath) {
  try {
    return JSON.parse(fs.readFileSync(installStatePath(userDataPath), "utf8"));
  } catch (_error) {
    return {};
  }
}

function writeInstallState(userDataPath, state) {
  fs.mkdirSync(userDataPath, { recursive: true });
  fs.writeFileSync(installStatePath(userDataPath), `${JSON.stringify(state, null, 2)}\n`);
}

function installKeyFor({ version, payloadInfo, commandInfo = resolveStackosCommand() }) {
  if (!payloadInfo) {
    return [version, commandInfo.mode, commandInfo.command, ...commandInfo.baseArgs].join(":");
  }
  return [
    version,
    payloadInfo.version || "unknown",
    payloadInfo.buildId || "no-build-id",
    commandInfo.mode,
    commandInfo.command,
    ...commandInfo.baseArgs
  ].join(":");
}

async function prepareInstalledVersion({
  version,
  userDataPath,
  force = false,
  payloadInfo = readPackagedBuildInfo(),
  installOrRepairFn = installOrRepair,
  repairPreparedInstallFn = null,
  repairMcpRegistrationsFn = null,
  repairPreparedInstallBeforeReady = false,
  runDoctor = force,
  onProgress = null
}) {
  const progressOptions = { onProgress };
  const state = readInstallState(userDataPath);
  const commandInfo = resolveStackosCommand();
  const installKey = installKeyFor({ version, payloadInfo, commandInfo });
  const preparedCurrentInstall =
    state.prepared === true &&
    (state.installKey === installKey || (!payloadInfo && !state.installKey && state.version === version));
  if (!force && preparedCurrentInstall) {
    await reportProgress(progressOptions, "Setup is current...", 30);
    if (repairPreparedInstallBeforeReady) {
      let externalRepair;
      try {
        externalRepair = await (
          repairPreparedInstallFn ||
          repairMcpRegistrationsFn ||
          repairPreparedInstall
        )({
          onProgress
        });
      } catch (error) {
        externalRepair = {
          ok: false,
          phase: "external-registration",
          error: error && error.message ? error.message : String(error)
        };
      }
      const repairOk = externalRepair && externalRepair.ok !== false;
      return {
        ok: repairOk,
        phase: repairOk ? "prepared" : externalRepair.phase || "external-registration",
        skipped: true,
        version,
        installKey,
        payloadInfo,
        state,
        externalRepair
      };
    }

    return {
      ok: true,
      phase: "prepared",
      skipped: true,
      maintenanceRequired: true,
      version,
      installKey,
      payloadInfo,
      state
    };
  }

  await reportProgress(
    progressOptions,
    force ? "Repairing StackOS..." : "Preparing StackOS...",
    24
  );
  const result = await installOrRepairFn({
    runDoctor,
    onProgress
  });
  if (result.ok) {
    writeInstallState(userDataPath, {
      version,
      installKey,
      payloadInfo,
      prepared: true,
      preparedAt: new Date().toISOString()
    });
  }
  return {
    ...result,
    skipped: false,
    version,
    installKey,
    payloadInfo
  };
}

async function runDoctor() {
  const result = await runStackos(["doctor", "--json"], {
    timeoutMs: 60000
  });
  const parsed = parseDoctorPayload(result);
  return {
    ...result,
    parsed,
    readiness: readinessFromDoctor({ ...result, parsed })
  };
}

function daemonLogPath() {
  return path.join(os.homedir(), ".local", "state", "stackos", "daemon.log");
}

module.exports = {
  DAEMON_URL,
  HEALTH_URL,
  authenticatedJsonGet,
  authTokenPath,
  checkHealth,
  daemonLogPath,
  daemonJsonGet,
  autostartStatus,
  ensureDaemonReady,
  installOrRepair,
  installKeyFor,
  installStatePath,
  prepareInstalledVersion,
  parseDoctorPayload,
  readinessFromDoctor,
  readAuthToken,
  readInstallState,
  readPackagedBuildInfo,
  packagedLaunchdIsCurrent,
  repairMcpRegistrations,
  repairPreparedInstall,
  resolveStackosCommand,
  restartDaemon,
  runDoctor,
  runStackos,
  setPackagedRuntime,
  startDaemon
};
