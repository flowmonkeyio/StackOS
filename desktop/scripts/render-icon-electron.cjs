"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");

const [sourceSvgPath, iconsetDir] = process.argv.slice(2);

const sizes = [
  ["icon_16x16.png", 16],
  ["icon_16x16@2x.png", 32],
  ["icon_32x32.png", 32],
  ["icon_32x32@2x.png", 64],
  ["icon_128x128.png", 128],
  ["icon_128x128@2x.png", 256],
  ["icon_256x256.png", 256],
  ["icon_256x256@2x.png", 512],
  ["icon_512x512.png", 512],
  ["icon_512x512@2x.png", 1024]
];

function fail(message) {
  console.error(message);
  app.exit(1);
}

async function main() {
  if (!sourceSvgPath || !iconsetDir) {
    fail("usage: electron scripts/render-icon-electron.cjs <source-svg> <iconset-dir>");
    return;
  }

  const svg = fs.readFileSync(sourceSvgPath, "utf8");
  fs.mkdirSync(iconsetDir, { recursive: true });

  app.disableHardwareAcceleration();
  await app.whenReady();

  const window = new BrowserWindow({
    show: false,
    width: 1024,
    height: 1024,
    transparent: true,
    frame: false,
    resizable: false,
    webPreferences: {
      backgroundThrottling: false,
      offscreen: true,
      sandbox: true
    }
  });

  const html = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <style>
      html,
      body {
        width: 100%;
        height: 100%;
        margin: 0;
        overflow: hidden;
        background: transparent;
      }

      svg {
        display: block;
        width: 100%;
        height: 100%;
      }
    </style>
  </head>
  <body>
    ${svg}
  </body>
</html>`;

  await window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  const image = await window.webContents.capturePage({
    x: 0,
    y: 0,
    width: 1024,
    height: 1024
  });

  for (const [fileName, size] of sizes) {
    const resized = image.resize({ width: size, height: size, quality: "best" });
    fs.writeFileSync(path.join(iconsetDir, fileName), resized.toPNG());
  }

  window.destroy();
  app.quit();
}

main().catch((error) => {
  fail(error.stack || error.message);
});
