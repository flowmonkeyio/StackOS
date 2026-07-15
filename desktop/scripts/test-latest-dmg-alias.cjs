"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  createLatestDmgAliases,
  latestDmgAliasPath
} = require("./release-artifacts.cjs");

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-latest-dmg-"));
const artifactPath = path.join(tempDir, "stackos-2.1.0-mac-arm64.dmg");
const aliasPath = path.join(tempDir, "stackos-latest-mac-arm64.dmg");
const artifact = Buffer.from("signed and notarized dmg bytes\n");

try {
  fs.writeFileSync(artifactPath, artifact);
  fs.writeFileSync(aliasPath, "stale release");

  assert.equal(latestDmgAliasPath(artifactPath), aliasPath);
  assert.deepEqual(createLatestDmgAliases([artifactPath]), [aliasPath]);
  assert.deepEqual(fs.readFileSync(aliasPath), artifact);
  assert.deepEqual(fs.readFileSync(artifactPath), artifact);
  assert.throws(
    () => latestDmgAliasPath(aliasPath),
    /cannot derive stable DMG alias/
  );

  console.log("desktop latest DMG alias test ok");
} finally {
  fs.rmSync(tempDir, { recursive: true, force: true });
}
