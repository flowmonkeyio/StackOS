"use strict";

const fs = require("node:fs");
const path = require("node:path");

const VERSIONED_DMG_PATTERN = /^stackos-(?!latest-)(.+)-mac-([a-z0-9_]+)\.dmg$/i;

function latestDmgAliasPath(artifactPath) {
  const match = path.basename(artifactPath).match(VERSIONED_DMG_PATTERN);
  if (!match) {
    throw new Error(`cannot derive stable DMG alias from ${path.basename(artifactPath)}`);
  }
  return path.join(path.dirname(artifactPath), `stackos-latest-mac-${match[2]}.dmg`);
}

function createLatestDmgAliases(artifactPaths) {
  return artifactPaths.map((artifactPath) => {
    if (!fs.statSync(artifactPath).isFile()) {
      throw new Error(`DMG artifact is not a file: ${artifactPath}`);
    }

    const aliasPath = latestDmgAliasPath(artifactPath);
    const temporaryPath = `${aliasPath}.tmp-${process.pid}`;
    try {
      fs.copyFileSync(artifactPath, temporaryPath);
      fs.renameSync(temporaryPath, aliasPath);
    } finally {
      fs.rmSync(temporaryPath, { force: true });
    }
    return aliasPath;
  });
}

module.exports = {
  createLatestDmgAliases,
  latestDmgAliasPath
};
