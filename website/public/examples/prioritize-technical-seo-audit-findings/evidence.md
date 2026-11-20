# Evidence for the illustrative audit

**All snippets and records below are authored fiction.** They describe the premises of a small exercise; they are not downloaded responses, a runnable sample site or browser captures. The audit date is October 6, 2026, and the fictional release is `demo-release-a`.

## Shared fetch assumptions

Requests are unauthenticated GETs to the exact HTTPS URLs, with no query manipulation or special crawler user agent. Except where a failure is shown, the response is `200`, HTML is available, and the snippets describe the initial HTML and agree with the rendered head. No `X-Robots-Tag` restriction or HTTP canonical header is present unless stated. The exercise's `/robots.txt` allows crawling:

```text
User-agent: *
Disallow:
```

These assumptions make the examples inspectable. A real audit must verify headers, redirects, crawl rules and rendered output rather than assume them.

## E01 — F01: pricing is unavailable

URL: `https://example.com/pricing/`

Three authored checks at 09:00, 09:05 and 09:10 Pacific time return the same result; the product page links directly to this URL. There is no planned maintenance in the fictional operator's change record.

```http
HTTP/1.1 503 Service Unavailable
Content-Type: text/html; charset=utf-8

<h1>Pricing is temporarily unavailable</h1>
```

The route's upstream failure is not diagnosed in this fixture. An unavailable page is demonstrated; the precise infrastructure repair remains to be investigated.

## E02 — F02: a product-page action leads to a missing route

Inspected page: `https://example.com/product/`

```html
<h1>Review workflow runs and assign the next action</h1>
<p>See failed work, assign an owner, and keep an audit trail.</p>
<a class="primary-action" href="/start/">Start a trial</a>
```

Authored route checks:

```text
GET https://example.com/product/  -> 200
GET https://example.com/start/    -> 404; body: "Page not found"
GET https://example.com/signup/   -> 200; body contains the expected trial form
```

The fictional route manifest and operator both identify `/signup/` as the intended trial route. The product template's CTA alone contains the stale `/start/` value. There is no evidence here about historical inbound links to `/start/`; the fixture does not justify a global redirect policy.

## E03 — F03: description length fails a tool rule

URL: `https://example.com/product/`

```html
<meta name="description" content="Review failed workflows, assign owners, and keep an audit trail for your team.">
```

Invented tool rule: flag every description below 120 characters as an error. The description accurately summarizes the page's heading and features shown in E02. No missing description, duplicate text, incorrect promise or observed search-result defect is supplied. Its length is insufficient evidence for a repair ticket. This rejects the length-only diagnosis; it does not declare that the copy can never be improved.

## E04 — F04, F05, F06: a shared template excludes public integrations

URLs:

- `https://example.com/integrations/slack/`
- `https://example.com/integrations/google-drive/`
- `https://example.com/integrations/notion/`

Each has a `200` response, is crawlable under the supplied rules, and contains this head element:

```html
<meta name="robots" content="noindex, follow">
```

The fictional owner `IntegrationPage` supplies it unconditionally:

```js
// Authored source fragment from the fictional template.
const integrationHead = {
  robots: 'noindex, follow'
};
```

The exercise's source history says the value came from a preview page copied into this release. All three routes use that owner; the operator's content policy identifies all three as public pages intended for search. The utility search template has its own explicit exclusion and does not use this owner. No Google crawl or current index status is asserted.

## E05 — F07, F08: two unique guides declare the index as canonical

URLs:

- `https://example.com/guides/approval-routing/`
- `https://example.com/guides/workflow-retries/`

Both initial and rendered heads contain exactly this declaration:

```html
<link rel="canonical" href="https://example.com/guides/">
```

The fictional guide metadata owner produces it:

```js
// Authored source fragment: routePath is available but unused.
const canonical = new URL('/guides/', 'https://example.com').href;
```

The approval guide explains approver selection and reassignment; the retry guide explains checking completed side effects before a retry. The target `/guides/` is a `200` listing page containing links and summaries, not either full article. The two guide URLs are distinct intended canonical pages in the operator's inventory and sitemap. The declared value conflicts with that intent. Google's actual canonical choices are not supplied.

## E06 — F09: one low lab score, no diagnosis

URL: `https://example.com/guides/workflow-retries/`

Invented record: one mobile lab navigation returned an aggregate performance score of 43/100. The export omitted its browser version, throttling settings, trace and metric breakdown. It includes no URL-level field data. That record can prompt a controlled measurement; it does not name a code defect or an acceptance condition. It does not prove that F08's canonical error caused the score.

## E07 — F10: a late booking widget moves the contact form

URL: `https://example.com/contact/`

Authored initial body:

```html
<h1>Contact the team</h1>
<div id="booking-slot"></div>
<form id="email-form">... email fields and send button ...</form>
```

The fictional `ContactPage` fills `booking-slot` after its asynchronous widget response. The slot has no initial reserved space. The widget is inserted above the email form, and its height at the tested width is 360 CSS pixels.

Authored diagnostic record: three cold navigations in a Chromium-based browser, viewport 390 × 844 CSS pixels, default zoom, 1.6 Mbps download / 750 Kbps upload / 150 ms latency, no CPU throttling, no scroll, click or keypress. Observe for five seconds after navigation.

| Replay | Widget inserted | Slot height before → after | Email field top before → after | Shift entry |
| --- | --- | --- | --- | --- |
| 1 | 1.8 s | 0 → 360 px | 208 → 568 px | Source `#email-form`; `hadRecentInput: false` |
| 2 | 1.9 s | 0 → 360 px | 208 → 568 px | Source `#email-form`; `hadRecentInput: false` |
| 3 | 1.8 s | 0 → 360 px | 208 → 568 px | Source `#email-form`; `hadRecentInput: false` |

In the exercise's diagnostic comparison, reserving the slot's 360-pixel height before insertion keeps that field at 568 pixels throughout the same replay. This isolates the insertion as the cause within the invented example. It is not a responsive design prescription: required slot height at other widths still needs investigation.

The premise supports a repair for this specific movement. It supplies no field CLS percentile, visitor failure count, lost leads or ranking effect. Movement seen in this lab scenario should not be relabelled as measured impact on real users.

## E08 — F11: search results are deliberately excluded

URL: `https://example.com/search/?q=retry`

```http
HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8
```

```html
<head><meta name="robots" content="noindex, follow"></head>
<body><h1>Search results for retry</h1>... public guide links ...</body>
```

The fictional content policy explicitly keeps internal search-result combinations out of search indexes while leaving the underlying public guides eligible. The URL is crawlable so the directive can be read. This is intentional exclusion of a public utility page, not protection for private content. The integration repair must preserve it.

## E09 — F12: possible unused JavaScript lacks interaction context

URL: `https://example.com/contact/`

Invented single-navigation tool output:

```text
Potential unused JavaScript: 90 KiB
Resource: /assets/booking-widget.js
Interaction coverage: initial navigation only
```

No booking interaction, date selection or fallback was exercised. This is an opportunity to inspect bundle use, not proof that deleting 90 KiB is safe or that these bytes cause the movement in E07. The layout task addresses the demonstrated insertion behavior. Bundle work waits for coverage of the interactions the page actually supports.
