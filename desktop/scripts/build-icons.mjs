import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

// Source of truth for the app icon is the high-resolution PNG (the matte
// stone-balance mark, with transparent rounded corners). The macOS .icns is
// built from it with sips + iconutil — no SVG render step.
const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(desktopDir, "..");
const assetsDir = path.join(desktopDir, "assets");
const sourcePngPath = path.join(assetsDir, "stackos-icon.png");
const iconIcnsPath = path.join(assetsDir, "stackos-icon.icns");

// Standard macOS iconset matrix (name, pixel size).
const ICONSET = [
  ["icon_16x16.png", 16],
  ["icon_16x16@2x.png", 32],
  ["icon_32x32.png", 32],
  ["icon_32x32@2x.png", 64],
  ["icon_128x128.png", 128],
  ["icon_128x128@2x.png", 256],
  ["icon_256x256.png", 256],
  ["icon_256x256@2x.png", 512],
  ["icon_512x512.png", 512],
  ["icon_512x512@2x.png", 1024],
];

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

function buildIcns() {
  if (!fs.existsSync(sourcePngPath)) {
    throw new Error(`stackos icon: source PNG missing at ${sourcePngPath}`);
  }
  if (fs.existsSync(iconIcnsPath)) {
    const sourceStat = fs.statSync(sourcePngPath);
    const iconStat = fs.statSync(iconIcnsPath);
    if (iconStat.size > 0 && iconStat.mtimeMs >= sourceStat.mtimeMs) {
      return "current";
    }
  }
  if (process.platform !== "darwin") {
    if (!fs.existsSync(iconIcnsPath)) {
      console.warn("stackos icon: skipping .icns generation outside macOS");
    }
    return "skipped";
  }

  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-icon-"));
  try {
    const iconsetDir = path.join(tempDir, "stackos-icon.iconset");
    fs.mkdirSync(iconsetDir, { recursive: true });
    for (const [name, size] of ICONSET) {
      run("sips", [
        "-s", "format", "png",
        "-z", String(size), String(size),
        sourcePngPath,
        "--out", path.join(iconsetDir, name)
      ]);
    }
    run("iconutil", ["-c", "icns", "-o", iconIcnsPath, iconsetDir]);
    return "synced";
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

const status = buildIcns();
if (status === "current") {
  console.log(`stackos icon current at ${path.relative(repoRoot, iconIcnsPath)}`);
} else if (status === "synced") {
  console.log(`stackos icon synced from ${path.relative(repoRoot, sourcePngPath)}`);
}
