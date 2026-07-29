"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function verifyStackosCli(cliPath, expectedVersion, options = {}) {
  const resolvedCliPath = path.resolve(cliPath);
  const spawn = options.spawnSync || spawnSync;

  if (!fs.existsSync(resolvedCliPath)) {
    throw new Error(`packaged StackOS CLI is missing: ${resolvedCliPath}`);
  }
  if ((fs.statSync(resolvedCliPath).mode & 0o111) === 0) {
    throw new Error(`packaged StackOS CLI is not executable: ${resolvedCliPath}`);
  }
  if (!expectedVersion) {
    throw new Error("expected StackOS version is required");
  }

  const smokeCwd = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-cli-smoke-"));
  const shadowPackage = path.join(smokeCwd, "stackos");
  fs.mkdirSync(shadowPackage);
  fs.writeFileSync(
    path.join(shadowPackage, "__init__.py"),
    'raise RuntimeError("ambient StackOS package was imported")\n',
    "utf8"
  );

  try {
    const result = spawn(resolvedCliPath, ["--version"], {
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
      throw new Error(`packaged StackOS CLI could not start: ${result.error.message}`);
    }
    if (result.status !== 0) {
      const detail = String(result.stderr || result.stdout || "").trim();
      throw new Error(
        `packaged StackOS CLI failed with exit code ${result.status}: ${detail || "no output"}`
      );
    }

    const output = String(result.stdout || "").trim();
    const expectedPrefix = `stackos ${expectedVersion} (`;
    if (!output.startsWith(expectedPrefix)) {
      throw new Error(
        `packaged StackOS CLI reported an unexpected version: expected prefix ` +
          `${JSON.stringify(expectedPrefix)}, received ${JSON.stringify(output)}`
      );
    }
    return output;
  } finally {
    fs.rmSync(smokeCwd, { recursive: true, force: true });
  }
}

function main(argv) {
  if (argv.length !== 2) {
    console.error("usage: node scripts/verify-stackos-cli.cjs <cli-path> <expected-version>");
    return 2;
  }

  try {
    const output = verifyStackosCli(argv[0], argv[1]);
    console.log(`packaged StackOS CLI smoke ok: ${output}`);
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
  verifyStackosCli
};
