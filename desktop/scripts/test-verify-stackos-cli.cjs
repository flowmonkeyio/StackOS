"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { verifyStackosCli } = require("./verify-stackos-cli.cjs");

const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-cli-verifier-test-"));
const cliPath = path.join(fixtureRoot, "stackos");
fs.writeFileSync(cliPath, "#!/bin/sh\nexit 0\n", { encoding: "utf8", mode: 0o755 });

try {
  let observedCwd;
  const output = verifyStackosCli(cliPath, "9.9.9", {
    spawnSync(command, args, options) {
      assert.equal(command, cliPath);
      assert.deepEqual(args, ["--version"]);
      assert.notEqual(options.cwd, fixtureRoot);
      assert.equal(options.env.PYTHONPATH, options.cwd);
      assert.equal(options.env.PYTHONHOME, path.join(options.cwd, "invalid-python-home"));
      assert.equal(options.env.VIRTUAL_ENV, path.join(options.cwd, "invalid-venv"));
      assert.equal(fs.existsSync(path.join(options.cwd, "stackos", "__init__.py")), true);
      observedCwd = options.cwd;
      return { status: 0, stdout: "stackos 9.9.9 (test)\n", stderr: "" };
    }
  });
  assert.equal(output, "stackos 9.9.9 (test)");
  assert.equal(fs.existsSync(observedCwd), false);

  assert.throws(
    () =>
      verifyStackosCli(cliPath, "9.9.9", {
        spawnSync() {
          return { status: 1, stdout: "", stderr: "ImportError: incompatible dependency" };
        }
      }),
    /ImportError: incompatible dependency/
  );

  assert.throws(
    () =>
      verifyStackosCli(cliPath, "9.9.9", {
        spawnSync() {
          return { status: 0, stdout: "stackos 2.1.17 (M10)\n", stderr: "" };
        }
      }),
    /unexpected version/
  );
} finally {
  fs.rmSync(fixtureRoot, { recursive: true, force: true });
}

console.log("packaged StackOS CLI verifier tests ok");
