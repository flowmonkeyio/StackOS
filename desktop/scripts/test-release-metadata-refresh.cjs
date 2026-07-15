"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const zlib = require("node:zlib");
const {
  refreshNotarizedUpdateMetadata
} = require("./refresh-notarized-update-metadata.cjs");

async function main() {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stackos-release-metadata-"));
  const artifactName = "stackos-2.1.1-mac-arm64.dmg";
  const artifactPath = path.join(tempDir, artifactName);
  const metadataPath = path.join(tempDir, "latest-mac.yml");
  const artifact = Buffer.from("post-staple dmg bytes\n".repeat(1024));
  const expectedSha512 = crypto.createHash("sha512").update(artifact).digest("base64");
  const zipSha512 = "zip-sha512-must-not-change";

  fs.writeFileSync(artifactPath, artifact);
  fs.writeFileSync(
    metadataPath,
    [
      "version: 2.1.1",
      "files:",
      "  - url: stackos-2.1.1-mac-arm64.zip",
      `    sha512: ${zipSha512}`,
      "    size: 123",
      `  - url: ${artifactName}`,
      "    sha512: stale-dmg-sha512",
      "    size: 456",
      "path: stackos-2.1.1-mac-arm64.zip",
      `sha512: ${zipSha512}`,
      "releaseDate: '2026-07-11T00:00:00.000Z'",
      ""
    ].join("\n")
  );

  try {
    const result = await refreshNotarizedUpdateMetadata(artifactPath, metadataPath);
    const metadata = fs.readFileSync(metadataPath, "utf8");
    const blockMap = JSON.parse(
      zlib.gunzipSync(fs.readFileSync(`${artifactPath}.blockmap`)).toString("utf8")
    );

    assert.equal(result.sha512, expectedSha512);
    assert.equal(result.size, artifact.length);
    assert.match(metadata, new RegExp(`sha512: ${expectedSha512.replace(/\+/g, "\\+")}`));
    assert.match(metadata, new RegExp(`size: ${artifact.length}`));
    assert.equal(metadata.match(new RegExp(zipSha512, "g")).length, 2);
    assert.equal(blockMap.version, "2");
    assert.equal(blockMap.files[0].sizes.reduce((total, size) => total + size, 0), artifact.length);
    console.log("desktop notarized release metadata refresh test ok");
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
