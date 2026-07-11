"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

function loadBlockMapBuilder() {
  const requireFromElectronBuilder = createRequire(
    require.resolve("electron-builder/package.json")
  );
  return requireFromElectronBuilder(
    "app-builder-lib/out/targets/blockmap/blockmap.js"
  ).buildBlockMap;
}

function refreshDmgEntry(metadata, artifactName, sha512, size) {
  const lines = metadata.split("\n");
  const entryStart = lines.findIndex(
    (line) => line.trim() === `- url: ${artifactName}`
  );
  if (entryStart === -1) {
    throw new Error(`latest-mac.yml does not contain ${artifactName}`);
  }

  let sha512Index = -1;
  let sizeIndex = -1;
  for (let index = entryStart + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\s*- url: /.test(line) || /^\S/.test(line)) {
      break;
    }
    if (/^\s+sha512: /.test(line)) {
      sha512Index = index;
    } else if (/^\s+size: /.test(line)) {
      sizeIndex = index;
    }
  }

  if (sha512Index === -1 || sizeIndex === -1) {
    throw new Error(`latest-mac.yml has an incomplete entry for ${artifactName}`);
  }

  const indentation = lines[sha512Index].match(/^\s*/)[0];
  lines[sha512Index] = `${indentation}sha512: ${sha512}`;
  lines[sizeIndex] = `${indentation}size: ${size}`;
  return lines.join("\n");
}

async function refreshNotarizedUpdateMetadata(artifactPath, metadataPath) {
  const artifactName = path.basename(artifactPath);
  const blockMapPath = `${artifactPath}.blockmap`;
  const blockMapTempPath = `${blockMapPath}.tmp-${process.pid}`;
  const metadataTempPath = `${metadataPath}.tmp-${process.pid}`;
  const buildBlockMap = loadBlockMapBuilder();

  try {
    const updateInfo = await buildBlockMap(
      artifactPath,
      "gzip",
      blockMapTempPath
    );
    const metadata = fs.readFileSync(metadataPath, "utf8");
    const refreshed = refreshDmgEntry(
      metadata,
      artifactName,
      updateInfo.sha512,
      updateInfo.size
    );

    fs.renameSync(blockMapTempPath, blockMapPath);
    fs.writeFileSync(metadataTempPath, refreshed);
    fs.renameSync(metadataTempPath, metadataPath);
    return { artifactName, blockMapPath, ...updateInfo };
  } finally {
    fs.rmSync(blockMapTempPath, { force: true });
    fs.rmSync(metadataTempPath, { force: true });
  }
}

module.exports = {
  refreshDmgEntry,
  refreshNotarizedUpdateMetadata
};

if (require.main === module) {
  const [, , artifactPath, metadataPath] = process.argv;
  if (!artifactPath || !metadataPath) {
    console.error(
      "usage: node scripts/refresh-notarized-update-metadata.cjs <dmg> <latest-mac.yml>"
    );
    process.exit(1);
  }

  refreshNotarizedUpdateMetadata(artifactPath, metadataPath)
    .then(({ artifactName, size }) => {
      console.log(`refreshed updater metadata for ${artifactName} (${size} bytes)`);
    })
    .catch((error) => {
      console.error(error.message);
      process.exit(1);
    });
}
