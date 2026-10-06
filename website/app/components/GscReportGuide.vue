<template>
  <section class="gsc-guide" aria-labelledby="gsc-guide-title">
    <div class="shell gsc-guide__content">
      <p class="eyebrow">Your first report</p>
      <h2 id="gsc-guide-title">Find a page worth improving.</h2>
      <p>
        Start with a page people already find in Google. Ask your agent to read its search data,
        then compare the queries with what the page actually helps a visitor do.
      </p>

      <h3>Connect the property you want to measure</h3>
      <ol>
        <li>
          Enable the <strong>Search Console API</strong> in the Google Cloud project used for your
          credentials. StackOS supports Google OAuth and service-account connections.
          <a href="https://developers.google.com/webmaster-tools/v1/how-tos/authorizing" target="_blank" rel="noopener noreferrer">Google's authorization guide</a>
          covers the API setup.
        </li>
        <li>
          In StackOS, choose <strong>Read only</strong> for reports or <strong>Read and submit sitemaps</strong>
          for submission access. These request the <code>webmasters.readonly</code> and
          <code>webmasters</code> scopes respectively. Changing an existing read-only connection needs
          fresh Google consent for OAuth, or a new token with the full scope for a service account.
          StackOS handles the service-account token.
        </li>
        <li>
          Give the connected account access to the site in Search Console. Restricted users can read
          performance data; submitting a sitemap requires an owner or full user. For a service account,
          add the <code>client_email</code> from its JSON key under
          <strong>Settings → Users and permissions</strong>.
          <a href="https://support.google.com/webmasters/answer/7687615" target="_blank" rel="noopener noreferrer">Google's property permissions</a>
          are separate from the connection's scope.
        </li>
        <li>
          Add the account through your StackOS project's Connections page and ask your agent to
          confirm the available properties. Keep credentials in StackOS. If your AI client is not
          connected yet, follow the <NuxtLink to="/library/articles/use-codex-claude-gemini-with-existing-tools/">client setup guide</NuxtLink> first.
        </li>
      </ol>

      <h3>Ask for one bounded report</h3>
      <p>Replace the example property and dates with a property you can access and a complete reporting period.</p>
      <blockquote>
        For https://example.com/, show Web search performance from August 29 through September 25,
        2026, inclusive, using final data. List pages with clicks, impressions, CTR and average
        position. Then show query rows for one page we could improve. State the filters and any
        limits on the returned rows. Read only; do not change the site.
      </blockquote>
      <p>
        Search Console reports these dates in Pacific Time. Its
        <a href="https://developers.google.com/webmaster-tools/v1/searchanalytics/query" target="_blank" rel="noopener noreferrer">Search Analytics API</a>
        returns top rows and may omit other data, so an absent row is not proof of zero traffic.
      </p>

      <div class="gsc-guide__table">
        <table>
          <caption>Illustrative page result for the example period above. These are made-up numbers, not StackOS traffic.</caption>
          <thead>
            <tr><th scope="col">Page</th><th scope="col">Clicks</th><th scope="col">Impressions</th><th scope="col">CTR</th><th scope="col">Average position</th></tr>
          </thead>
          <tbody>
            <tr><th scope="row"><code>/guides/client-setup/</code></th><td>4</td><td>200</td><td>2.0%</td><td>9.4</td></tr>
          </tbody>
        </table>
      </div>
      <p>
        Four clicks from 200 impressions gives a 2% CTR. That alone does not tell you what to change.
        If the query rows concern connecting a client, read the page for a missing setup step or
        an unclear success check. Improve that gap if you find it. Keep the URL and useful content,
        then compare the same page and reporting scope after enough new data has accumulated.
      </p>
      <p>
        The <NuxtLink to="/library/workflows/seo-content-refresh/">content refresh workflow</NuxtLink>
        can carry that diagnosis into reviewed changes and later measurement. A small report is a
        starting point for investigation; it does not establish a cause or promise better rankings.
      </p>

      <h3>Submit the sitemap after a site update</h3>
      <p>
        StackOS 2.1.31 can submit or resubmit an existing sitemap URL. First publish your changes
        and check that the sitemap includes the pages you want Google to discover. Then replace
        the property and sitemap URLs in this request:
      </p>
      <blockquote>
        For https://example.com/, submit https://example.com/sitemap.xml to Search Console.
        After submission succeeds, read its sitemap record and report when Google last downloaded
        it, plus any warnings or errors.
      </blockquote>
      <p>
        <a href="https://developers.google.com/webmaster-tools/v1/sitemaps/submit" target="_blank" rel="noopener noreferrer">Sitemap submission</a>
        tells Google where to find the sitemap; it does not edit the file. A successful result
        confirms submission. The follow-up
        <a href="https://developers.google.com/webmaster-tools/v1/sitemaps/list" target="_blank" rel="noopener noreferrer">sitemap record</a>
        can show that Google fetched it. Neither result proves that Google indexed the listed pages.
      </p>

      <h3>Batch independent requests</h3>
      <p>
        When you have several checks, ask your agent to group property listings, performance queries,
        sitemap records or URL inspections into a read batch using one connected account. Sitemap
        submissions use a separate write batch. Each batch supports up to 1,000 requests.
      </p>
      <p>
        <a href="https://developers.google.com/webmaster-tools/v1/how-tos/batch" target="_blank" rel="noopener noreferrer">Google counts each request against quota</a>
        and may run them in any order. Keep dependent work separate, such as submitting a sitemap
        and then checking its new record. Ask for each item's result. If part of a batch fails,
        keep the successful results and inspect uncertain submissions before deciding what to retry.
      </p>

      <h3>Check what Google has indexed</h3>
      <p>
        Google's
        <a href="https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect" target="_blank" rel="noopener noreferrer">URL Inspection API</a>
        reports on the indexed version of a URL. Use it to investigate Google's recorded status
        after an update. It does not test the live page or request indexing for an individual article.
      </p>
    </div>
  </section>
</template>

<style scoped>
.gsc-guide { padding: 64px 0; color: var(--ink); background: var(--paper); border-bottom: 1px solid rgb(7 10 15 / 10%); }
.gsc-guide__content { max-width: 880px; }
.gsc-guide h2 { margin: 10px 0 24px; font-size: clamp(30px, 4vw, 48px); line-height: 1.08; letter-spacing: -.04em; }
.gsc-guide h3 { margin: 32px 0 12px; font-size: 23px; line-height: 1.3; letter-spacing: -.02em; }
.gsc-guide p, .gsc-guide li { font-size: 16px; line-height: 1.7; }
.gsc-guide ol { display: grid; gap: 14px; padding-left: 24px; }
.gsc-guide a { color: var(--cobalt); text-underline-offset: 3px; }
.gsc-guide code { overflow-wrap: anywhere; font-size: .9em; }
.gsc-guide blockquote { margin: 20px 0; padding: 20px 24px; border-left: 3px solid var(--cobalt); background: #eceae2; font-size: 16px; line-height: 1.7; overflow-wrap: anywhere; }
.gsc-guide__table { overflow-x: auto; margin: 26px 0; }
.gsc-guide table { width: 100%; border-collapse: collapse; font-size: 14px; text-align: left; }
.gsc-guide caption { padding-bottom: 12px; text-align: left; color: #50535a; line-height: 1.5; }
.gsc-guide th, .gsc-guide td { padding: 12px; border: 1px solid rgb(7 10 15 / 15%); vertical-align: top; }
.gsc-guide thead { background: #eceae2; }
@media (max-width: 640px) { .gsc-guide { padding: 44px 0; } .gsc-guide blockquote { padding: 16px; } }
</style>
