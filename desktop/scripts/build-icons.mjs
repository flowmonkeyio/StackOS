import { spawnSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(desktopDir, "..");
const require = createRequire(import.meta.url);
const sourceSvgPath = path.join(repoRoot, "ui", "public", "favicon.svg");
const assetsDir = path.join(desktopDir, "assets");
const iconSvgPath = path.join(assetsDir, "stackos-icon.svg");
const iconIcnsPath = path.join(assetsDir, "stackos-icon.icns");

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: desktopDir,
    encoding: "utf8",
    stdio: "pipe",
    shell: false
  });
  if (result.error) {
    throw new Error(`${command}: ${result.error.message}`);
  }
  if (result.status !== 0) {
    const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    throw new Error(`${command} ${args.join(" ")} failed${output ? `:\n${output}` : ""}`);
  }
  return result.stdout.trim();
}

function copyFaviconSource() {
  fs.mkdirSync(assetsDir, { recursive: true });
  const svg = fs.readFileSync(sourceSvgPath, "utf8");
  fs.writeFileSync(iconSvgPath, svg.endsWith("\n") ? svg : `${svg}\n`);
}

function buildIcns() {
  if (process.platform !== "darwin") {
    if (!fs.existsSync(iconIcnsPath)) {
      console.warn("stackos icon: skipping .icns generation outside macOS");
    }
    return;
  }

  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-icon-"));
  try {
    const iconsetDir = path.join(tempDir, "stackos-icon.iconset");
    fs.mkdirSync(iconsetDir, { recursive: true });

    const electronPath = require("electron");
    run(electronPath, ["scripts/render-icon-electron.cjs", iconSvgPath, iconsetDir]);
    run("iconutil", ["-c", "icns", "-o", iconIcnsPath, iconsetDir]);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

copyFaviconSource();
buildIcns();
console.log(`stackos icon synced from ${path.relative(repoRoot, sourceSvgPath)}`);
