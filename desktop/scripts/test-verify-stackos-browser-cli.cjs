"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { verifyStackosBrowserCli } = require("./verify-stackos-browser-cli.cjs");

const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-browser-cli-verifier-test-"));
const cliPath = path.join(fixtureRoot, "stackos.browser");
fs.writeFileSync(cliPath, "#!/bin/sh\nexit 0\n", { encoding: "utf8", mode: 0o755 });

try {
  let observedCwd;
  verifyStackosBrowserCli(cliPath, {
    spawnSync(command, args, options) {
      assert.equal(command, cliPath);
      assert.deepEqual(args, ["--help"]);
      assert.notEqual(options.cwd, fixtureRoot);
      assert.equal(options.env.PYTHONPATH, options.cwd);
      assert.equal(options.env.PYTHONHOME, path.join(options.cwd, "invalid-python-home"));
      assert.equal(options.env.VIRTUAL_ENV, path.join(options.cwd, "invalid-venv"));
      assert.equal(fs.existsSync(path.join(options.cwd, "stackos", "__init__.py")), true);
      observedCwd = options.cwd;
      return {
        status: 2,
        stdout: "",
        stderr: "Usage: stackos.browser --session <full-session-ref> [native browse arguments...]\n"
      };
    }
  });
  assert.equal(fs.existsSync(observedCwd), false);

  assert.throws(
    () =>
      verifyStackosBrowserCli(cliPath, {
        spawnSync() {
          return { status: 0, stdout: "", stderr: "" };
        }
      }),
    /expected usage exit 2/
  );

  assert.throws(
    () =>
      verifyStackosBrowserCli(cliPath, {
        spawnSync() {
          return { status: 2, stdout: "", stderr: "unexpected" };
        }
      }),
    /unexpected usage/
  );
} finally {
  fs.rmSync(fixtureRoot, { recursive: true, force: true });
}

console.log("packaged StackOS browser launcher verifier tests ok");
