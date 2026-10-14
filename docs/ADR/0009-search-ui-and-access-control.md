# 0009 – Search UI and access control

- Status: accepted
- Date: 2026-10-14

## Context

Colleagues need a simple web page. No build tool chain, no extra backend.

## Decision

- **Static page** `web/index.html` + `app.js` + `style.css` (vanilla JS, no CDN, no build
  step, German UI). Search-as-you-type, facet filters (topic, group, state, year, tags,
  customer pseudonym), sort by relevance/newest/oldest, detail view with all articles and
  attachment links, and a link to the original Zammad ticket (works only while it still
  exists there). The search state is kept in the URL hash, so links can be shared.
- Caddy serves the page and acts as a gatekeeper:
  - `/meili/…` only forwards `POST /indexes/tickets/search`. Everything else returns 403,
    even with the master key.
  - `/files/…` serves the archive read-only: `<number>/ticket.json` for the detail view, and
    the attachments with `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`
    and a sandbox CSP, so an uploaded HTML/SVG file cannot run script in our origin.
    Hidden paths (`.tmp-*`, `.old-*`) return 404, and there are no directory listings.
  - A strict `Content-Security-Policy` for the UI.
  - All text from tickets is HTML-escaped before display. Search highlights use
    sentinel markers that are replaced with `<mark>` after escaping.
- **Access control** (choose one in the setup guide):
  1. Caddy `basic_auth` (one shared or a few named users), or
  2. the company reverse proxy with SSO in front, Caddy only reachable from it.

## Consequences

- No user management in the app itself.
- The search key is visible in the browser. That is acceptable because it can only
  search/read this one index, and Caddy blocks every other path anyway.
