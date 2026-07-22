import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(resolve(projectRoot, "public/index.html"), "utf8");
const headers = readFileSync(resolve(projectRoot, "public/_headers"), "utf8");
const scriptMatch = html.match(/<script data-oauth-relay>([\s\S]*?)<\/script>/u);

assert.ok(scriptMatch, "index.html must contain one inline OAuth relay script");

const relayScript = scriptMatch[1];
const callbackPath = "/api/v1/auth/oauth/callback";
const publicOrigin = "https://auth.stackos.flowmonkey.io";
const localOrigin = "http://127.0.0.1:5180";

function runRelay(href) {
  const elements = new Map([
    ["status", { textContent: "" }],
    ["detail", { hidden: true, textContent: "" }],
    ["local-home", { hidden: true }],
  ]);
  const historyUrls = [];
  const navigations = [];
  const sandbox = {
    URL,
    URLSearchParams,
    document: {
      documentElement: { dataset: {} },
      getElementById(id) {
        return elements.get(id) ?? null;
      },
    },
    window: {
      history: {
        replaceState(_state, _title, url) {
          historyUrls.push(url);
        },
      },
      location: {
        href,
        replace(url) {
          navigations.push(url);
        },
      },
    },
  };

  vm.runInNewContext(relayScript, sandbox, {
    filename: "oauth-callback-relay/index.html",
  });

  return {
    detail: elements.get("detail"),
    historyUrls,
    localHome: elements.get("local-home"),
    navigations,
    pageState: sandbox.document.documentElement.dataset.state,
    status: elements.get("status").textContent,
  };
}

test("success forwards only bounded state and code to the fixed loopback callback", () => {
  const result = runRelay(
    `${publicOrigin}${callbackPath}?state=state-value&code=code%2Bvalue&scope=ignored&return_url=https%3A%2F%2Fevil.example`,
  );

  assert.equal(result.navigations.length, 1);
  const destination = new URL(result.navigations[0]);
  assert.equal(destination.origin, localOrigin);
  assert.equal(destination.pathname, callbackPath);
  assert.deepEqual([...destination.searchParams.keys()], ["state", "code"]);
  assert.equal(destination.searchParams.get("state"), "state-value");
  assert.equal(destination.searchParams.get("code"), "code+value");
  assert.equal(result.pageState, "redirecting");
  assert.deepEqual(result.historyUrls, [callbackPath]);
});

test("denial forwards state and error while ignoring provider metadata", () => {
  const result = runRelay(
    `${publicOrigin}${callbackPath}?state=state-value&error=access_denied&error_description=private-detail&error_uri=https%3A%2F%2Fprovider.example%2Ferrors%2F1`,
  );

  assert.equal(result.navigations.length, 1);
  const destination = new URL(result.navigations[0]);
  assert.deepEqual([...destination.searchParams.keys()], ["state", "error"]);
  assert.equal(destination.searchParams.get("error"), "access_denied");
  assert.equal(destination.searchParams.has("error_description"), false);
  assert.equal(destination.searchParams.has("error_uri"), false);
});

const invalidCallbacks = [
  ["requires HTTPS", `http://auth.stackos.flowmonkey.io${callbackPath}?state=s&code=c`],
  ["requires the configured host", `https://other.example${callbackPath}?state=s&code=c`],
  ["requires the exact callback path", `${publicOrigin}/other?state=s&code=c`],
  ["requires state", `${publicOrigin}${callbackPath}?code=c`],
  ["rejects empty state", `${publicOrigin}${callbackPath}?state=&code=c`],
  ["requires one outcome", `${publicOrigin}${callbackPath}?state=s`],
  ["rejects conflicting outcomes", `${publicOrigin}${callbackPath}?state=s&code=c&error=denied`],
  ["rejects duplicate state", `${publicOrigin}${callbackPath}?state=s&state=t&code=c`],
  ["rejects duplicate code", `${publicOrigin}${callbackPath}?state=s&code=c&code=d`],
  ["rejects duplicate error", `${publicOrigin}${callbackPath}?state=s&error=a&error=b`],
  ["bounds state", `${publicOrigin}${callbackPath}?state=${"s".repeat(513)}&code=c`],
  ["bounds code", `${publicOrigin}${callbackPath}?state=s&code=${"c".repeat(4097)}`],
  ["bounds error", `${publicOrigin}${callbackPath}?state=s&error=${"e".repeat(201)}`],
];

for (const [name, href] of invalidCallbacks) {
  test(name, () => {
    const result = runRelay(href);

    assert.deepEqual(result.navigations, []);
    assert.equal(result.pageState, "error");
    assert.equal(result.status, "Could not return to StackOS");
    assert.equal(result.detail.hidden, false);
    assert.equal(result.localHome.hidden, false);
    assert.deepEqual(result.historyUrls, [new URL(href).pathname]);
    assert.equal(result.detail.textContent.includes("state=s"), false);
    assert.equal(result.detail.textContent.includes("code=c"), false);
  });
}

test("open-redirect-shaped input cannot change any destination component", () => {
  const result = runRelay(
    `${publicOrigin}${callbackPath}?state=s&code=c&destination=https%3A%2F%2Fevil.example&host=evil.example&port=9999&path=%2Fsteal`,
  );

  assert.equal(result.navigations.length, 1);
  assert.equal(
    result.navigations[0],
    `${localOrigin}${callbackPath}?state=s&code=c`,
  );
});

test("the static response policy binds CSP to the exact inline script", () => {
  const digest = createHash("sha256").update(relayScript).digest("base64");

  assert.match(headers, /^\/\*$/mu);
  assert.match(headers, /Cache-Control: no-store, max-age=0/u);
  assert.match(headers, /Pragma: no-cache/u);
  assert.match(headers, /Referrer-Policy: no-referrer/u);
  assert.match(headers, /X-Content-Type-Options: nosniff/u);
  assert.match(headers, /X-Frame-Options: DENY/u);
  assert.match(headers, /X-Robots-Tag: noindex, nofollow, noarchive/u);
  assert.equal(headers.includes(`script-src 'sha256-${digest}'`), true);
  assert.equal(html.includes(`script-src 'sha256-${digest}'`), true);
});

test("the upload artifact has no external script, form, or network dependency", () => {
  assert.doesNotMatch(html, /<script[^>]+src=/iu);
  assert.doesNotMatch(html, /<form\b/iu);
  assert.doesNotMatch(html, /\bfetch\s*\(/u);
  assert.doesNotMatch(html, /XMLHttpRequest/u);
});

test("the exact callback path remains available through Pages SPA fallback", () => {
  assert.equal(existsSync(resolve(projectRoot, "public/404.html")), false);
});
