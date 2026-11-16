"use strict";

const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const { createRequire } = require("node:module");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const builderRequire = createRequire(require.resolve("electron-builder"));
const { getConfig, validateConfiguration } = builderRequire("app-builder-lib/out/util/config/config");
const { getFileMatchers, copyFiles } = builderRequire("app-builder-lib/out/fileMatcher");

async function main() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-build-isolation-test-"));
  const desktopDir = path.join(root, "desktop");
  const scriptsDir = path.join(desktopDir, "scripts");
  const configRoot = path.join(root, "configs");
  const binDir = path.join(root, "bin");
  const builds = [];
  fs.mkdirSync(scriptsDir, { recursive: true });
  fs.mkdirSync(configRoot);
  fs.mkdirSync(binDir);
  const pkg = require("../package.json");
  fs.writeFileSync(path.join(desktopDir, "package.json"), JSON.stringify(pkg));
  for (const name of ["build-mac.mjs", "release-artifacts.cjs", "test-build-mac-config.cjs"]) {
    fs.copyFileSync(path.join(__dirname, name), path.join(scriptsDir, name));
  }
  // Only the config producer and its subprocess lifetime are under test.
  fs.writeFileSync(path.join(scriptsDir, "build-stackos-payload.sh"), "exit 0\n");
  for (const name of ["build-icons.mjs", "verify-stackos-cli.cjs", "verify-stackos-browser-cli.cjs"]) {
    fs.writeFileSync(path.join(scriptsDir, name), "");
  }
  fs.mkdirSync(path.join(desktopDir, "dist"));
  fs.writeFileSync(path.join(desktopDir, "dist", `stackos-${pkg.version}-mac-arm64.dmg`), "fixture dmg");
  const builderPath = path.join(binDir, "electron-builder");
  fs.writeFileSync(builderPath, `#!${process.execPath}
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const configPath = process.argv[process.argv.indexOf("--config") + 1];
const configText = fs.readFileSync(configPath, "utf8");
const config = JSON.parse(configText);
const resource = config.extraResources.find(entry => entry.to === "update-config.json");
const updatePath = path.resolve(resource.from);
const updateText = fs.readFileSync(updatePath, "utf8");
assert.equal(JSON.parse(updateText).updateUrl, process.env.STACKOS_UPDATE_URL);
const deadline = setTimeout(() => process.exit(95), 15000);
process.stdin.once("data", () => {
  assert.equal(fs.readFileSync(configPath, "utf8"), configText);
  assert.equal(fs.readFileSync(updatePath, "utf8"), updateText);
  clearTimeout(deadline);
  process.exit(Number(process.env.TEST_PACKAGING_EXIT || 0));
});
console.log("PACKAGING_READY " + JSON.stringify({ configPath, updatePath, configText, updateText }));
`);
  fs.chmodSync(builderPath, 0o755);
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (/^(CSC_|APPLE_|STACKOS_(?:DESKTOP_|UPDATE_URL|REQUIRE_|ALLOW_|UNSIGNED_DEV|SKIP_NOTARIZATION))/.test(key)) {
      delete env[key];
    }
  }
  Object.assign(env, {
    TDLIB_INPUT_VARIANT: "",
    STACKOS_UNSIGNED_DEV: "1",
    STACKOS_DESKTOP_CONFIG_ROOT: configRoot,
    PATH: `${binDir}${path.delimiter}${process.env.PATH}`
  });

  function startBuild(updateUrl, exitCode = 0) {
    const child = spawn(process.execPath, ["scripts/build-mac.mjs"], {
      cwd: desktopDir,
      env: { ...env, STACKOS_UPDATE_URL: updateUrl, TEST_PACKAGING_EXIT: String(exitCode) },
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.stderr.on("data", chunk => { stderr += chunk; });
    const done = new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", code => resolve({ code, stdout, stderr }));
    });
    const ready = new Promise((resolve, reject) => {
      readline.createInterface({ input: child.stdout }).on("line", line => {
        if (line.startsWith("PACKAGING_READY ")) {
          resolve(JSON.parse(line.slice("PACKAGING_READY ".length)));
        }
      });
      done.then(result => reject(new Error(`packaging exited before readiness: ${JSON.stringify(result)}`)), reject);
    });
    const build = { child, done, ready };
    builds.push(build);
    return build;
  }

  function assertIntact(snapshot) {
    assert.equal(fs.readFileSync(snapshot.configPath, "utf8"), snapshot.configText);
    assert.equal(fs.readFileSync(snapshot.updatePath, "utf8"), snapshot.updateText);
  }

  try {
    const first = startBuild("http://127.0.0.1:8765/first");
    const firstConfig = await first.ready;
    const loadedConfig = await getConfig(desktopDir, firstConfig.configPath);
    await validateConfiguration(loadedConfig, { isEnabled: false });
    const resourceDir = path.join(root, "packaged-resources");
    const matchers = getFileMatchers(loadedConfig, "extraResources", resourceDir, {
      defaultSrc: desktopDir,
      macroExpander: value => value,
      customBuildOptions: {},
      globalOutDir: path.join(desktopDir, "dist")
    });
    assert.ok(matchers.some(matcher => matcher.from === path.join(desktopDir, "payload", "stackos")));
    const updateMatchers = matchers.filter(matcher => matcher.to === path.join(resourceDir, "update-config.json"));
    assert.equal(updateMatchers.length, 1);
    assert.equal(updateMatchers[0].from, firstConfig.updatePath);
    fs.mkdirSync(resourceDir);
    await copyFiles(updateMatchers, null, false);
    assert.equal(fs.readFileSync(path.join(resourceDir, "update-config.json"), "utf8"), firstConfig.updateText);
    console.log("Electron Builder loads the external config and copies the update resource under its packaged name");

    const configTest = spawnSync(process.execPath, ["scripts/test-build-mac-config.cjs"], {
      cwd: desktopDir, env, encoding: "utf8", timeout: 15000
    });
    assert.equal(configTest.status, 0, configTest.stderr);
    assertIntact(firstConfig);
    console.log("config test preserves the paused packaging child's generated files");

    const second = startBuild("http://127.0.0.1:8765/second", 23);
    const secondConfig = await second.ready;
    assert.notEqual(firstConfig.configPath, secondConfig.configPath);
    assert.notEqual(firstConfig.updatePath, secondConfig.updatePath);
    assertIntact(firstConfig);
    second.child.stdin.end("complete\n");
    const secondResult = await second.done;
    assert.equal(secondResult.code, 23, secondResult.stderr);
    assert.equal(fs.existsSync(secondConfig.configPath), false);
    assert.equal(fs.existsSync(secondConfig.updatePath), false);
    assertIntact(firstConfig);
    console.log("failed overlapping build cleans only its own generated files");

    first.child.stdin.end("complete\n");
    const firstResult = await first.done;
    assert.equal(firstResult.code, 0, firstResult.stderr);
    assert.equal(fs.existsSync(firstConfig.configPath), false);
    assert.equal(fs.existsSync(firstConfig.updatePath), false);
    assert.deepEqual(fs.readdirSync(configRoot), []);
    assert.equal(fs.readFileSync(path.join(desktopDir, "dist", "stackos-latest-mac-arm64.dmg"), "utf8"), "fixture dmg");
    console.log("successful packaging retains config through child completion and then cleans it");
  } finally {
    for (const build of builds) {
      if (!build.child.stdin.writableEnded) build.child.stdin.end("complete\n");
    }
    await Promise.allSettled(builds.map(build => build.done));
    fs.rmSync(root, { recursive: true, force: true });
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
