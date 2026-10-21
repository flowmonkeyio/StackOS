// Read-only rendered owner status on the exact disposable daemon.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { once } = require('node:events');

const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const origin = new URL(input.base);
assert.equal(origin.hostname, '127.0.0.1');
assert.equal(origin.protocol, 'http:');
assert.equal(origin.username, '');
assert.equal(origin.password, '');
assert.equal(origin.pathname, '/');
assert.equal(origin.search, '');
assert.equal(origin.hash, '');
assert(Number.isSafeInteger(Number(origin.port)) && Number(origin.port) > 0);
assert(Number.isSafeInteger(input.project) && input.project > 0);
assert.match(input.item, /^task:[1-9][0-9]*$/);
assert(!Object.hasOwn(input, 'token') && !Object.hasOwn(input, 'run_token'));
const { chromium } = require(path.join(process.env.STACKOS_UI_NODE_MODULES, '@playwright/test'));
const withoutCapture = value => Array.isArray(value) ? value.map(withoutCapture)
  : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value)
    .filter(([key]) => key !== 'captured_at').map(([key, val]) => [key, withoutCapture(val)])) : value;

(async () => {
  let server, child, exited, browser, context, page, primary;
  const receipt = { status: 'started', browser_launches: 0, external_requests_blocked: [], owner_get_requests: [] };
  try {
    receipt.browser_launches = 1;
    server = await chromium.launchServer({
      executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      headless: true,
      env: { PATH: '/usr/bin:/bin', HOME: input.home, TMPDIR: input.tmp,
        XDG_CONFIG_HOME: input.config, XDG_CACHE_HOME: input.cache, LC_ALL: 'C', TZ: 'UTC' },
      args: ['--disable-background-networking', '--disable-component-update', '--no-first-run',
        '--disable-extensions', '--disable-sync', '--disable-default-apps',
        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
    });
    child = server.process();
    assert(child && Number.isSafeInteger(child.pid));
    exited = child.exitCode !== null || child.signalCode !== null ? Promise.resolve() : once(child, 'exit');
    browser = await chromium.connect(server.wsEndpoint());
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin === origin.origin) return route.continue();
      receipt.external_requests_blocked.push(url.origin);
      return route.abort();
    });
    page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.setDefaultNavigationTimeout(20000);
    page.on('request', request => {
      if (request.url() === `${origin.origin}/api/v1/operations/run.ownerGet/call` && request.method() === 'POST') {
        const args = request.postDataJSON().arguments;
        // Request body fields are public item identity only, never headers/auth.
        receipt.owner_get_requests.push({ project_id: args.project_id, canonical_item_id: args.canonical_item_id });
      }
    });
    // Tracker streaming can keep network connections open. Wait on the real
    // visible controls instead of requiring an artificial network-idle state.
    await page.goto(`${origin.origin}/projects/${input.project}/tasks?view=stories&focus=all`, { waitUntil: 'domcontentloaded' });
    const story = page.getByRole('button').filter({ has: page.getByText(input.title, { exact: true }) });
    await story.waitFor({ state: 'visible' });
    assert.equal(await story.count(), 1);
    await story.click();
    await page.getByRole('heading', { name: input.title, exact: true }).waitFor({ state: 'visible' });
    const [response] = await Promise.all([
      page.waitForResponse(response => {
        if (response.url() !== `${origin.origin}/api/v1/operations/run.ownerGet/call` || response.request().method() !== 'POST') return false;
        const args = response.request().postDataJSON().arguments;
        return args.project_id === input.project && args.canonical_item_id === input.item;
      }),
      page.getByRole('button', { name: 'Open task details', exact: true }).click(),
    ]);
    assert.equal(response.status(), 200);
    const actual = await response.json();
    assert.deepEqual(withoutCapture(actual), withoutCapture(input.expected_inspection));
    const status = actual.status ?? actual.data?.status;
    assert.equal(actual.ownership.canonical_item_id, input.item);
    const drawer = page.getByRole('dialog', { name: input.title, exact: true });
    await drawer.waitFor({ state: 'visible' });
    const text = drawer.getByText(/^\s*Owner status:/);
    await text.waitFor({ state: 'visible' });
    const rendered = (await text.innerText()).replace(/\s+/g, ' ').trim();
    assert.equal(rendered,
      `Owner status: ${status.state} · ${status.next_actor} / ${status.next_action} · acceptance ${status.acceptance_state}`);
    const ownerLine = (await drawer.getByText(/^\s*Current\s/).innerText()).replace(/\s+/g, ' ').trim();
    assert.equal(ownerLine,
      `Current ${status.owner.owner_ref} · epoch ${status.owner.epoch} · revision ${status.owner.revision}`);
    receipt.status = 'passed';
    receipt.canonical_item_id = input.item;
    receipt.owner_status = status;
    receipt.inspection = actual;
    receipt.rendered_status = rendered;
    receipt.rendered_owner = ownerLine;
    assert.equal(new URL(page.url()).origin, origin.origin);
    assert.deepEqual(receipt.external_requests_blocked, []);
  } catch (error) {
    primary = error;
    receipt.status = 'failed';
    receipt.error = String(error);
  } finally {
    try {
      if (page) {
        await page.screenshot({ path: path.join(input.output, 'owner-status.png'), fullPage: true });
        fs.writeFileSync(path.join(input.output, 'rendered-text.txt'), await page.locator('body').innerText());
      }
    } catch (error) { primary ??= error; }
    try {
      if (context) await context.close();
    } catch (error) { primary ??= error; }
    try { if (browser) await browser.close(); } catch (error) { primary ??= error; }
    try { if (server) await server.close(); } catch (error) { primary ??= error; }
    // Retain the original executing owner until the original child exits.
    // A cleanup exception never licenses a new signal or a success claim.
    if (child) await exited;
    receipt.browser_reaped = !!child && (child.exitCode !== null || child.signalCode !== null);
    receipt.browser_exit_code = child?.exitCode ?? null;
    receipt.browser_exit_signal = child?.signalCode ?? null;
    if (child && child.exitCode !== 0 && child.signalCode !== 'SIGTERM') {
      primary ??= new Error('Unexpected owned browser exit');
    }
    if (primary) receipt.status = 'failed';
    fs.writeFileSync(path.join(input.output, 'browser-receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
    if (primary) throw primary;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
