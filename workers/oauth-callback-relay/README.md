# StackOS OAuth Callback Page

This is a static Cloudflare Pages upload. It is not a Worker or Pages Function.
The browser validates the provider response, removes it from the visible public
URL, and navigates to the existing StackOS callback on the same machine:

```text
https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback
  -> browser location.replace()
  -> http://127.0.0.1:5180/api/v1/auth/oauth/callback
```

The local StackOS daemon remains the only owner of OAuth state validation,
provider binding, token exchange, persistence, replay protection, and the final
Connections UI redirect.

## Upload

1. Run `npm test` and `npm run bundle` in this directory.
2. In Cloudflare, open **Workers & Pages** -> **Create** -> **Pages** ->
   **Direct Upload**.
3. Create a project such as `stackos-oauth-callback`.
4. Upload the generated `dist/stackos-oauth-callback-pages.zip`, or upload the
   contents of [`public`](./public), including `_headers`. Upload the contents,
   not this repository directory with its tests and package file.
5. In the Pages project, open **Custom domains** -> **Set up a domain** and add
   `auth.stackos.flowmonkey.io`.
6. Cloudflare will show a target such as `stackos-oauth-callback.pages.dev`.
7. In Namecheap **Advanced DNS**, add a `CNAME Record` with host `auth`, value
   set to that exact `pages.dev` target, and TTL `Automatic`. Do not use a URL
   Redirect Record.
8. Wait until Cloudflare reports the custom domain and certificate as active.

The provider callback and StackOS configuration are then:

```text
https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback
STACKOS_OAUTH_CALLBACK_BASE_URL=https://auth.stackos.flowmonkey.io
```

Add the custom domain in Cloudflare before creating the Namecheap CNAME.
Cloudflare Pages requires that association to provision and route the hostname
correctly.

## Important behavior

- Cloudflare Pages serves `public/index.html` at the exact callback path through
  its static single-page fallback. The page itself rejects every other origin
  or path.
- The page forwards only one bounded `state` plus one bounded `code` or `error`.
  It ignores all other query fields.
- The local destination, port, and path are constants. Callback input cannot
  change them.
- The query and fragment are removed from the public browser history before
  validation or local navigation.
- `_headers` disables browser caching and referrer propagation and binds the
  Content Security Policy to the exact inline relay script.
- JavaScript is required. Because this is static hosting, the public response
  is an HTML `200`; `location.replace()` performs the handoff instead of a
  server-generated HTTP `303`.
- The flow must run in a browser on the same machine as StackOS, with the daemon
  listening on `127.0.0.1:5180`.

Do not enable analytics, browser recording, request logging, or log export that
persists callback query strings. Do not upload provider secrets: this page has
no configuration secrets and should never contain any.

If the inline script or public hostname changes, run the tests. The response
header test will fail until `_headers` contains the new exact script hash.
