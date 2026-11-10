"use strict";
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const servicePath = require.resolve("../src/service");
const selected = "http://127.0.0.1:19940";
process.env.STACKOS_SERVER_URL = selected;
const service = require(servicePath);
assert.equal(service.APP_URL, `${selected}/`);
assert.equal(service.isAppUrl(`${selected}/api/v1/auth/callback?type=signup`), true);
for (const target of ["http://localhost:19940/", "http://127.0.0.1:19941/", "https://127.0.0.1:19940/", "http://user@127.0.0.1:19940/"]) {
  assert.equal(service.isAppUrl(target), false, target);
}
for (const target of ["", "http://example.test", "https://example.test/path", "https://user@example.test/", "https://example.test/?x=1"]) {
  const result = spawnSync(process.execPath, ["-e", `require(${JSON.stringify(servicePath)})`], {
    env: { ...process.env, STACKOS_SERVER_URL: target }, encoding: "utf8"
  });
  assert.notEqual(result.status, 0, target);
}
async function checkAdmission() {
  const original = global.fetch;
  try {
    for (const [status, body, expected] of [
      [404, {}, false], [503, {}, false],
      [401, { detail: { code: "session_expired_or_invalid" } }, true]
    ]) {
      global.fetch = async (url, init) => {
        assert.equal(url.toString(), `${selected}/api/v1/auth/session`);
        assert.equal(init.credentials, "omit");
        assert.equal(init.redirect, "error");
        return new Response(JSON.stringify(body), { status });
      };
      assert.equal(await service.checkSelectedServer(), expected);
    }
  } finally {
    global.fetch = original;
  }
  console.log("Desktop selected-origin auth checks passed");
}
checkAdmission().catch((error) => { console.error(error); process.exitCode = 1; });
