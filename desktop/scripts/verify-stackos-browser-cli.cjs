"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function verifyStackosBrowserCli(cliPath, options = {}) {
  const resolvedCliPath = path.resolve(cliPath);
  const spawn = options.spawnSync || spawnSync;
  if (!fs.existsSync(resolvedCliPath)) {
    throw new Error(`packaged StackOS browser launcher is missing: ${resolvedCliPath}`);
  }
  if ((fs.statSync(resolvedCliPath).mode & 0o111) === 0) {
    throw new Error(`packaged StackOS browser launcher is not executable: ${resolvedCliPath}`);
  }

  const smokeCwd = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-browser-cli-smoke-"));
  const shadowPackage = path.join(smokeCwd, "stackos");
  fs.mkdirSync(shadowPackage);
  fs.writeFileSync(
    path.join(shadowPackage, "__init__.py"),
    'raise RuntimeError("ambient StackOS package was imported")\n',
    "utf8"
  );
  try {
    const result = spawn(resolvedCliPath, ["--help"], {
      cwd: smokeCwd,
      env: {
        ...process.env,
        PYTHONHOME: path.join(smokeCwd, "invalid-python-home"),
        PYTHONPATH: smokeCwd,
        VIRTUAL_ENV: path.join(smokeCwd, "invalid-venv")
      },
      encoding: "utf8",
      shell: false
    });
    if (result.error) {
      throw new Error(`packaged StackOS browser launcher could not start: ${result.error.message}`);
    }
    if (result.status !== 2) {
      const detail = String(result.stderr || result.stdout || "").trim();
      throw new Error(
        `packaged StackOS browser launcher expected usage exit 2, received ${result.status}: ` +
          `${detail || "no output"}`
      );
    }
    const stderr = String(result.stderr || "");
    if (!stderr.includes("Usage: stackos.browser --session <full-session-ref>")) {
      throw new Error(`packaged StackOS browser launcher reported unexpected usage: ${stderr}`);
    }
  } finally {
    fs.rmSync(smokeCwd, { recursive: true, force: true });
  }
}

function main(argv) {
  if (argv.length !== 1) {
    console.error("usage: node scripts/verify-stackos-browser-cli.cjs <cli-path>");
    return 2;
  }
  try {
    verifyStackosBrowserCli(argv[0]);
    console.log("packaged StackOS browser launcher smoke ok");
    return 0;
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    return 1;
  }
}

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2));
}

module.exports = {
  main,
  verifyStackosBrowserCli
};
