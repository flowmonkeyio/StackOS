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

function packagedStackosPath() {
  if (!process.resourcesPath) {
    return null;
  }
  return path.join(process.resourcesPath, "stackos", "bin", "stackos");
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

function trimOutput(value, limit = 12000) {
  if (!value) {
    return "";
  }
  if (value.length <= limit) {
    return value;
  }
  return `${value.slice(0, limit)}\n[trimmed ${value.length - limit} chars]`;
}

function runStackos(args, options = {}) {
  const resolved = resolveStackosCommand();
  const timeoutMs = options.timeoutMs || 120000;
  const cwd = options.cwd || repoRoot();
  return new Promise((resolve) => {
    const child = childProcess.spawn(resolved.command, [...resolved.baseArgs, ...args], {
      cwd,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1"
      },
      stdio: ["ignore", "pipe", "pipe"]
    });

    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
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

async function waitForHealth(timeoutMs = 20000) {
  const startedAt = Date.now();
  let last = await checkHealth();
  while (!last.ok && Date.now() - startedAt < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    last = await checkHealth();
  }
  return last;
}

async function startDaemon(timeoutSeconds = 20) {
  const result = await runStackos(["start", "--timeout", String(timeoutSeconds)], {
    timeoutMs: (timeoutSeconds + 5) * 1000
  });
  if (!result.ok) {
    return result;
  }
  const health = await waitForHealth(timeoutSeconds * 1000);
  return {
    ...result,
    health
  };
}

async function restartDaemon(timeoutSeconds = 20) {
  const result = await runStackos(["restart", "--timeout", String(timeoutSeconds)], {
    timeoutMs: (timeoutSeconds + 10) * 1000
  });
  if (!result.ok) {
    return result;
  }
  const health = await waitForHealth(timeoutSeconds * 1000);
  return {
    ...result,
    health
  };
}

async function ensureDaemonReady(timeoutSeconds = 20) {
  const health = await checkHealth();
  if (health.ok) {
    return {
      ok: true,
      alreadyRunning: true,
      health
    };
  }
  return startDaemon(timeoutSeconds);
}

async function installOrRepair(timeoutSeconds = 240) {
  const installArgs = process.platform === "darwin" ? ["install", "--launchd"] : ["install"];
  const install = await runStackos(installArgs, {
    timeoutMs: timeoutSeconds * 1000
  });
  if (!install.ok) {
    return {
      ok: false,
      phase: "install",
      install
    };
  }
  const start = await startDaemon(20);
  return {
    ok: start.ok,
    phase: start.ok ? "ready" : "start",
    install,
    start
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

async function prepareInstalledVersion({ version, userDataPath, force = false }) {
  const state = readInstallState(userDataPath);
  if (!force && state.version === version && state.prepared === true) {
    return {
      ok: true,
      skipped: true,
      version,
      state
    };
  }

  const result = await installOrRepair();
  if (result.ok) {
    writeInstallState(userDataPath, {
      version,
      prepared: true,
      preparedAt: new Date().toISOString()
    });
  }
  return {
    ...result,
    skipped: false,
    version
  };
}

async function runDoctor() {
  return runStackos(["doctor", "--json"], {
    timeoutMs: 60000
  });
}

function daemonLogPath() {
  return path.join(os.homedir(), ".local", "state", "stackos", "daemon.log");
}

module.exports = {
  DAEMON_URL,
  HEALTH_URL,
  checkHealth,
  daemonLogPath,
  ensureDaemonReady,
  installOrRepair,
  prepareInstalledVersion,
  resolveStackosCommand,
  restartDaemon,
  runDoctor,
  runStackos,
  startDaemon
};
