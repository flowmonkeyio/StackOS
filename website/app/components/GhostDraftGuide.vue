<template>
  <section class="ghost-guide" aria-labelledby="ghost-guide-title">
    <div class="shell ghost-guide__content">
      <p class="eyebrow">Your first draft</p>
      <h2 id="ghost-guide-title">Put reviewed copy into a Ghost draft.</h2>
      <p>
        Send the finished HTML to Ghost with an explicit draft status. Then open the draft in
        Ghost Admin to check the content and decide when to publish it.
      </p>

      <h3>Connect the intended Ghost site</h3>
      <p>
        Create or select a custom integration in Ghost Admin and save its Admin API key in your
        StackOS connection. Enter the site/admin domain root, such as <code>https://example.com</code>,
        without <code>/ghost/api/admin/</code>. Keep the key in StackOS.
        <a href="https://docs.ghost.org/admin-api" target="_blank" rel="noopener noreferrer">Ghost's Admin API guide</a>
        explains the connection.
      </p>

      <h3>Ask for one draft</h3>
      <p>This is an illustrative request. Replace the site, title and HTML with your own reviewed copy.</p>
      <blockquote>
        In the connected Ghost site https://example.com, create a draft titled “September product
        notes” using the HTML below. Set its status to draft. Return the post ID and status from
        Ghost's response.
      </blockquote>
      <p>The <strong>Create Ghost Post</strong> action accepts this input:</p>
      <pre><code>{
  "source": "html",
  "post": {
    "title": "September product notes",
    "html": "&lt;p&gt;A reviewed summary of the changes in this release.&lt;/p&gt;",
    "status": "draft"
  }
}</code></pre>
      <p>
        Set <code>status</code> explicitly. StackOS passes the supplied status to Ghost; it does
        not always choose draft for you.
      </p>

      <h3>Check and finish the same draft</h3>
      <ol>
        <li>
          Look for the created post in the returned <code>posts</code> array. Save its
          <code>id</code> and check that <code>status</code> is <code>draft</code>. If those fields
          are missing or unclear, inspect Ghost Admin before treating creation as complete.
        </li>
        <li>
          Open that draft in Ghost Admin. Check its title, body, links, author, tags and preview.
          <a href="https://docs.ghost.org/admin-api/posts/creating-a-post" target="_blank" rel="noopener noreferrer">Ghost converts the submitted HTML</a>,
          so inspect the formatting there.
        </li>
        <li>
          Make your edits and publish that existing draft in Ghost Admin when it is ready.
          StackOS 2.1.31 exposes a create-post action for Ghost. It does not expose an action to
          update or publish an existing draft; another create request asks for a new post.
        </li>
      </ol>

      <h3>If the result is uncertain</h3>
      <p>
        A missing response does not prove that Ghost created nothing. The connector can retry
        failed requests, so duplicate posts are possible. Inspect Ghost Admin for the intended
        draft and any duplicates before deciding whether to send another create request.
      </p>
    </div>
  </section>
</template>

<style scoped>
.ghost-guide { padding: 64px 0; color: var(--ink); background: var(--paper); border-bottom: 1px solid rgb(7 10 15 / 10%); }
.ghost-guide__content { max-width: 880px; }
.ghost-guide h2 { margin: 10px 0 24px; font-size: clamp(30px, 4vw, 48px); line-height: 1.08; letter-spacing: -.04em; }
.ghost-guide h3 { margin: 32px 0 12px; font-size: 23px; line-height: 1.3; letter-spacing: -.02em; }
.ghost-guide p, .ghost-guide li { font-size: 16px; line-height: 1.7; }
.ghost-guide ol { display: grid; gap: 14px; padding-left: 24px; }
.ghost-guide a { color: var(--cobalt); text-underline-offset: 3px; }
.ghost-guide code { overflow-wrap: anywhere; font-size: .9em; }
.ghost-guide blockquote { margin: 20px 0; padding: 20px 24px; border-left: 3px solid var(--cobalt); background: #eceae2; font-size: 16px; line-height: 1.7; overflow-wrap: anywhere; }
.ghost-guide pre { margin: 20px 0; padding: 20px 24px; overflow-x: auto; background: #eceae2; font-size: 14px; line-height: 1.6; }
@media (max-width: 640px) { .ghost-guide { padding: 44px 0; } .ghost-guide blockquote, .ghost-guide pre { padding: 16px; } }
</style>
