# Five proposed tasks from twelve findings

**Illustrative only.** Every site detail and observation is invented. The queue is ready to explain and review, not a claim that any fix has shipped. Read [the evidence](evidence.md) for the exact premises and [the CSV](audit-findings.csv) for all twelve original rows.

## Why this order

The operator's stated priorities put reading pricing and starting a trial first. Those two journeys currently fail outright. Next come an unintended exclusion of three public integration pages and misleading canonical declarations on two unique guides. The last selected task removes a reproduced movement of the contact form. No visitor counts, ranking points or revenue estimates are supplied to turn that judgment into a numeric score.

| Order | Task | Findings | Repair unit | Reason |
| --- | --- | --- | --- | --- |
| 1 | T1: restore pricing access | F01 | One failing route; cause still unknown | A visitor cannot read the page. |
| 2 | T2: repair the trial link | F02 | Product CTA target | A stated primary journey ends at a 404. |
| 3 | T3: correct integration indexing instructions | F04–F06 | Shared integration metadata owner | Three intended public pages emit the same accidental exclusion. |
| 4 | T4: correct guide canonical declarations | F07–F08 | Shared guide metadata owner | Unique guides point their canonical preference at an index page. |
| 5 | T5: stop the contact form shifting | F10 | Contact booking-slot layout | A repeatable late insertion moves visible form fields. |

This order can change when new facts arrive. A broad outage, an upcoming launch that depends on the integration pages, or a confirmed inability to use the contact form would warrant reassessment. A separate developer can take an independent template repair while the pricing cause is investigated; the order is a priority decision, not a claim that work must run serially.

## T1 — Restore the pricing page

- **Affected URL:** `https://example.com/pricing/`.
- **Defect and evidence:** F01 / E01. Three repeated GETs return `503` with an unavailable page. The pricing link is part of the intended visitor journey.
- **Reader consequence:** A visitor cannot compare the displayed plans. The fixture does not establish whether they abandon, return or contact the team instead.
- **Repair scope:** Find the route failure and restore the intended pricing response. Capture the failing upstream or route condition before changing it. Do not replace the failure with a `200` response that still contains the error page.
- **Effort uncertainty:** High until the route logs and last deployment are checked. A configuration error and a failed dependency require different repairs; the status code alone does not choose one.
- **Reproduce:** In the authorized environment, follow the pricing link and make three unauthenticated GETs five minutes apart. Retain status, redirects, headers and body; confirm the issue is outside a planned maintenance window. The repeat interval is this exercise's check, not a reliability guarantee.
- **Accept:** The same link reaches the intended page, the GET returns `200` without a redirect loop or error body, and plan names, prices and the intended next action are visible. Repeat the baseline checks and inspect the corresponding route logs. Record what caused the failure and add a regression for that condition. Search indexing is a later observation, not this task's acceptance test.

## T2 — Point the product action at the trial route

- **Affected URLs:** source `https://example.com/product/`; broken target `https://example.com/start/`; intended target `https://example.com/signup/`.
- **Defect and evidence:** F02 / E02. The product CTA links to a missing route; the route manifest and operator identify `/signup/` as the intended form.
- **Reader consequence:** A visitor choosing “Start a trial” reaches a not-found page.
- **Repair scope:** Correct the product CTA's target. Search its shared component consumers to determine whether the same stale value appears elsewhere. Do not add a blanket redirect without checking what `/start/` used to mean and whether other callers need it.
- **Effort uncertainty:** The link edit is small if the fixture's isolated owner is accurate. Shared consumers or a legacy-route requirement would enlarge the scope; verify them first.
- **Reproduce:** From the product page, activate the CTA with a pointer and with keyboard navigation. Save the source href and final URL/status. Compare the intended route with the application's current route definition.
- **Accept:** Both activations reach `/signup/` with a `200` response and the expected usable trial form. The source HTML href and any client-side navigation agree. Check the affected component's other consumers. Reaching the form is the acceptance boundary; a separate authorized test is needed to create an account.

## T3 — Remove the accidental integration-page exclusion

- **Affected URLs:** `https://example.com/integrations/slack/`, `https://example.com/integrations/google-drive/`, and `https://example.com/integrations/notion/`.
- **Defect and evidence:** F04–F06 / E04. The shared integration metadata owner emits `noindex` for all three, contrary to the operator's public-content policy. The matching owner and output establish a common cause; similar-looking warnings alone would not.
- **Reader consequence:** The directive tells supporting search crawlers not to show these pages once it is fetched and processed. A visitor can still open them directly. This exercise contains no evidence that Google has already crawled the directive or removed a URL.
- **Repair scope:** Correct the integration template's production indexing configuration. Retain explicit exclusions belonging to other page purposes, including the public search page in F11.
- **Effort uncertainty:** The source change appears bounded, but inspect production environment overrides, response headers and metadata merges before estimating completion. A second `noindex` source would survive a template-only edit.
- **Reproduce:** GET every listed URL and inspect response headers, initial HTML and rendered metadata. Check crawl access and the template source. Record all applicable robots directives, including `googlebot` rules; do not infer the effective result from one tag.
- **Accept:** Each intended integration page returns its content with `200`, is crawlable, and has no applicable `noindex` in initial HTML, rendered HTML or headers. There is no contradictory metadata source. The public search-results URL still emits its intended `noindex` and remains crawlable. Recheck the remaining consumers of the changed integration owner. Requesting or observing recrawl is follow-up work; indexing is not guaranteed by this repair.

## T4 — Give each unique guide its intended canonical declaration

- **Affected URLs:** `https://example.com/guides/approval-routing/` and `https://example.com/guides/workflow-retries/`; incorrectly declared target `https://example.com/guides/`.
- **Defect and evidence:** F07–F08 / E05. The guide metadata owner uses a constant index URL even though the guides contain different complete answers and the index contains only summaries.
- **Reader consequence:** The declaration gives search engines a misleading canonical preference. Whether it changed Google's selected URL or a visitor's search result is unknown here.
- **Repair scope:** Use the reviewed canonical URL for each guide in the existing metadata owner. Inspect that owner's consumers and keep sitemap and internal-link choices consistent. Do not blindly turn every non-self canonical on the site into a self-reference; duplicates can legitimately point elsewhere.
- **Effort uncertainty:** The constant is simple to replace, but URL normalization, route aliases and metadata overrides may affect the correct value. Confirm those conventions before implementing.
- **Reproduce:** Fetch both guide URLs and the index, compare their content purpose, and inspect initial and rendered canonical declarations plus any HTTP `Link` header. Compare them with the source helper, inventory and sitemap.
- **Accept:** Each guide declares exactly its reviewed absolute canonical URL in the HTML head, without a conflicting header or rendered declaration. The declared target returns `200` and the intended complete guide. The sitemap and internal links use the same reviewed URLs. Recheck the shared owner's remaining consumers. A later URL Inspection result can assess Google's choice; it is not interchangeable with verifying our declaration.

## T5 — Reserve space for the contact booking widget

- **Affected URL:** `https://example.com/contact/`.
- **Defect and evidence:** F10 / E07. In three authored mobile replays, insertion into an initially empty slot moves the email field from 208 to 568 CSS pixels. The supplied diagnostic comparison isolates that slot's expansion as the cause.
- **Reader consequence:** A person trying to read or reach the email form would have to follow its changed position. The fixture has no measured failed click, lost enquiry or field CLS distribution.
- **Repair scope:** Give the booking slot a stable, responsive layout before late content arrives, or place the widget so it does not displace the form. Preserve the email fallback and avoid collapsing reserved space on a loading failure. Choose the actual layout from the widget's supported sizes; 360 pixels is only the example's measured mobile height.
- **Effort uncertainty:** The desktop layout, widget size changes, loading failure and interaction states have not been tested. Third-party content may need a different containment approach at different widths.
- **Reproduce:** Repeat E07's three cold mobile navigations and save a performance trace plus before/after positions. Use the same browser version and recorded throttling settings for comparisons; record the chosen version because the authored fixture has no executable browser receipt. Confirm the moved element and the element causing the movement separately.
- **Accept:** The specific booking-slot insertion no longer displaces the email form in three matching mobile runs. Check a 1440 × 900 desktop viewport, responsive resizing, a failed widget load and booking interactions for clipping or new movement. Confirm both booking controls and the email fallback remain usable without submitting a real enquiry. Keep the traces and element positions. Lab success closes this bounded layout repair; real-user performance needs its own later evidence.

## Four findings stay out of this repair queue

| Finding | Decision | Basis and next trigger |
| --- | --- | --- |
| F03: short meta description | **Reject the length-only defect** | The text is present and accurate. Reopen if a concrete mismatch, duplication or search-result problem is found; padding it to satisfy 120 characters is not an acceptance test. |
| F09: low aggregate lab score | **Investigate before choosing a fix** | One score lacks a trace, settings and metric breakdown. Repeat a controlled run and inspect the specific bottleneck; do not attach an arbitrary “score above 90” target to the developer's queue. |
| F11: public search URL has `noindex` | **Accept intentional exclusion** | The directive matches the stated policy and the URL is crawlable. Reopen only if that page's role or policy changes. Keep it as a regression check for T3. |
| F12: possible unused JavaScript | **Defer for interaction coverage** | The report covers initial load only. Test booking states and inspect the bundle before removing code. T5 does not establish that this warning will disappear. |

There are eight accepted finding rows, grouped into five repair tasks, plus four findings with explicit dispositions. Nothing is silently dropped, and no invented score substitutes for the reason a task belongs in the queue.
