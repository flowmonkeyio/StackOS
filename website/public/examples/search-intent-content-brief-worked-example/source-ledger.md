# Technical facts cleared for the worked brief

Checked **October 6, 2026** by opening the primary documents below. The source links are real; the cases in the brief are authored examples. Search snippets and competitor assertions are observations in the separate sheet, not authority for technical instructions.

| Source | Exact place to inspect | Bounded fact available to the writer | Use and limit |
| --- | --- | --- | --- |
| **S1 — [Google sitemap documentation](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)** | XML sitemap section, additional notes; the bullet about `lastmod`. Page reports an update date of July 8, 2026. | Google expects dependable dates for significant page changes. Its examples include substantive content, structured data and link changes; copyright-date maintenance is excluded. | Supports the change-classification rule. Does not supply a word-count threshold or a promised search outcome. |
| **S2 — [Sitemaps protocol](https://www.sitemaps.org/protocol.html)** | XML tag definitions, the page-level `lastmod` row. | The field is optional, accepts a date such as `YYYY-MM-DD`, and describes the linked page's modification rather than sitemap generation. | Supports the date choice and compact examples. Page entries are this assignment's scope; index-file timestamps have another meaning. |
| **S3 — [Google's lastmod explanation](https://developers.google.com/search/blog/2023/06/sitemaps-lastmod-ping)** | Section headed “The `lastmod` element,” especially the paragraph about uncertain dates. Published June 26, 2023; checked against current S1. | Google permits omitting the field where the page's change date cannot be determined reliably. | Supports the unknown-date branch. It does not authorize inventing a plausible recent date. |

## Claim-to-assignment map

| Claim ID | Source | Where the writer needs it |
| --- | --- | --- |
| CL1 | S1 | Opening answer; distinguish a meaningful change from routine maintenance. |
| CL2 | S2 | Choose the page-change date and show date-only output. |
| CL3 | S3 | Explain the unknown-date outcome. |
| CL4 | S1 | Interpret the small pricing correction in C4. Mark the classification as our reasoning applied to fictional inputs. |

No primary source promises faster indexing, higher rankings, a preferred publication cadence or traffic growth from these choices. Those claims are outside the assignment and have no approved evidence here.

## Editorial applications and open questions

The case classifications are editorial reasoning from explicit inputs. For a real page, the writer would need the actual diff, its publication history and the source used by the sitemap generator. The packet does not inspect any real site's generator, file timestamps or deployment behavior.

The protocol is used for its field definition only. Do not treat every historical instruction elsewhere on that page as current Google implementation advice. Full submission, sitemap-index behavior, plugin configuration and indexing diagnosis are outside this brief.

## Source use

Paraphrase briefly and link near the supported claim. The only competitor quotation retained in this packet is the short clause in R03. Avoid recopying source passages or reproducing a competitor's heading sequence. The writer can complete this assignment from the three primary passages and the original fictional cases without borrowing a competitor's examples.
