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
  const installArgs =
    process.platform === "darwin" ? ["install", "--launchd", "--force"] : ["install"];
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
  const start = await restartDaemon(20);
  const doctor = start.ok ? await runDoctor() : null;
  const readiness = doctor
    ? readinessFromDoctor(doctor)
    : {
        ok: false,
        status: "restart-failed",
        code: null,
        repair: "restart the StackOS service, then run doctor"
      };
  return {
    ok: start.ok && (!doctor || doctor.ok),
    phase: start.ok ? (doctor && !doctor.ok ? "doctor" : "ready") : "restart",
    install,
    start,
    doctor,
    readiness
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

function installKeyFor({ version, payloadInfo }) {
  if (!payloadInfo) {
    return version;
  }
  return [version, payloadInfo.version || "unknown", payloadInfo.buildId || "no-build-id"].join(":");
}

async function prepareInstalledVersion({
  version,
  userDataPath,
  force = false,
  payloadInfo = readPackagedBuildInfo(),
  installOrRepairFn = installOrRepair
}) {
  const state = readInstallState(userDataPath);
  const installKey = installKeyFor({ version, payloadInfo });
  const preparedCurrentInstall =
    state.prepared === true &&
    (state.installKey === installKey || (!payloadInfo && !state.installKey && state.version === version));
  if (!force && preparedCurrentInstall) {
    return {
      ok: true,
      skipped: true,
      version,
      installKey,
      payloadInfo,
      state
    };
  }

  const result = await installOrRepairFn();
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
  checkHealth,
  daemonLogPath,
  ensureDaemonReady,
  installOrRepair,
  installKeyFor,
  installStatePath,
  prepareInstalledVersion,
  parseDoctorPayload,
  readinessFromDoctor,
  readInstallState,
  readPackagedBuildInfo,
  resolveStackosCommand,
  restartDaemon,
  runDoctor,
  runStackos,
  startDaemon
};
