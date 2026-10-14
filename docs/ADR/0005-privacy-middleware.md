# 0005 – Privacy middleware: allowlist, pseudonymisation, scrubbing

- Status: accepted (legal clearance pending, see "Open questions")
- Date: 2026-10-14

## Context

Tickets contain personal data of customers and colleagues: names, mail addresses,
phone numbers, signatures, quoted mail history, attachments. The 6-month deletion in
Zammad is most likely a GDPR retention rule. A long-term archive needs a reason, and it
should hold as little personal data as possible. The value we want to keep is the
**technical knowledge** ("how did we solve X"), not who asked.

## Decision

Every ticket passes through a middleware pipeline **before** anything is written to the
archive (`zammad_archive/middleware.py`). Steps, in order, each configurable under
`privacy:` in `config.yaml`:

1. **Allowlist fields** (`ticket_fields`, `article_fields`). Only listed fields are kept;
   new fields that Zammad adds later are dropped automatically. An allowlist is safer
   than a blocklist.
2. **Drop articles** by rule: `skip_internal_articles`, `skip_article_types` (e.g. `phone`
   notes), `skip_system_articles`.
3. **HTML → plain text**, then **strip quoted replies** (`> …`, "-----Original Message-----",
   "Am … schrieb …:", "On … wrote:", `<blockquote>`). The quoted mails are already in the
   archive as their own articles.
4. **Cut signatures** after configurable markers (`-- `, "Mit freundlichen Grüßen", …),
   optionally keeping a few lines.
5. **Pseudonymise people.** Every address in `from`/`to`/`cc`, the ticket customer and the
   organization is replaced by a stable pseudonym `Person-<8 hex>` / `Org-<8 hex>`.
   The pseudonym is an HMAC-SHA256 of the normalised mail address with a secret key
   (`ZAMMAD_ARCHIVE_SECRET`). The same person always gets the same pseudonym, so
   "all tickets of this customer" still works, but the name cannot be read back without
   the secret.
   Names and addresses found in the headers are also replaced **inside subject and body**,
   including single name parts (e.g. the surname alone) of a minimum length. The mail
   domain of a customer is replaced as well (`intranet.kunde.de` → `intranet.Domain-1a2b3c4d`),
   except for freemail domains (`scrub.customer_domains`, `scrub.public_domains`).
6. **Colleagues** (addresses in `internal_domains`) are shown according to `agent_display`:
   `login` (unchanged), `localpart` (`j.doe`), or `pseudonym`.
7. **Regex scrubbing** in title/subject/body: mail addresses, phone numbers, IBANs, plus
   custom patterns (e.g. customer numbers). Each has its own replacement text, e.g. `[PHONE]`.
8. **Trim**: `max_body_chars` per article.
9. **Attachments**: off, or limited by extension, size, and "skip inline images"
   (logos in signatures). Attachment *content* cannot be pseudonymised. This is the
   biggest privacy risk, so the default allowlist is narrow (logs, configs, text, PDF).
10. **Custom hook** (`custom_middleware: "module:function"`): your own Python function gets
    the processed bundle last and can change it or return `None` to drop the whole ticket.

## Consequences

- **Pseudonymised data is still personal data under GDPR** (Art. 4(5), Recital 26),
  because whoever has the secret can link it again. Only anonymisation (e.g. a random
  secret that is deleted, or `agent_display: pseudonym` + no secret kept) would take the
  data out of scope. This must be discussed with the data protection officer.
- Free-text names that do not appear in any header of any exported ticket (e.g. "please
  call Mrs. Schmidt from accounting") are **not** detected. We do not use NER/ML for this. Custom regex patterns
  or the custom hook can catch known cases. The inspect preview (ADR 0006) shows what slips through.
- The secret must be backed up with the archive. If it is lost, new exports get
  different pseudonyms than old ones (linking breaks, nothing else).
- Changing rules later only affects new exports (ADR 0002). So start strict.

## Open questions

- Is a privacy-reduced archive allowed at all? Who must approve it (DPO, works council)?
- How long may the archive keep tickets (e.g. 3 years)? A purge step is not built yet.
- Are attachments allowed at all, and which types?

## Decision updates

_Add the final field/attachment choice here after phase 1 (inspect), with date._

### 2026-10-14 – people known from other tickets (`privacy.cross_ticket_names`)

**Problem.** Step 5 only knew the people in the headers of the *same* ticket. A test against
a local Zammad showed "Schreibrechte für Thomas Becker und mich" in the archive: Thomas Becker
is a customer, but only in the headers of another ticket.

**Decision.** A persistent "known people" registry, on by default
(`privacy.cross_ticket_names: true`):

- Every name, name part, mail address, customer domain and organisation that step 5 registers
  for an **external** person is also added to `<archive_dir>/.people-registry.json`.
  Colleagues are not added (they are shown per `agent_display`).
- The file stores **no clear text**: key = HMAC-SHA256 (same `ZAMMAD_ARCHIVE_SECRET`, separate
  domain prefix, truncated to 128 bit) of the normalised (lowercase, single spaces) term,
  value = the pseudonym (`Person-…`, `Org-…`, `Domain-…`, or `[NAME]` if a name part belongs
  to two people). Without the secret it shows only how many terms there are; with the secret
  someone can *test* a guessed name, which they could do with the pseudonyms anyway.
  It lives outside `tickets/`, so the web front does not serve it.
- Scrubbing (after the per-ticket names, before the regexes) hashes every word and every
  sequence of up to 4 words joined by space, `, `, `-`, `.` or `_` (as written and
  space-joined), longest first, and replaces hits with the stored pseudonym. Single words
  shorter than `name_part_min_length` or listed in `name_part_ignore` are never replaced,
  and existing pseudonyms are skipped. Cost: a few HMACs per word, local only.
- Order does not matter: after an export run in which the registry gained entries, the
  exporter **re-scrubs the whole archive** (ticket.json strings, attachment file names and
  extracted `.txt`) with the final registry. Changed `ticket.json` files get a new mtime,
  so the next `index` picks them up. A `pending_rescrub` flag in the file makes an
  interrupted re-scrub run again next time.
- `inspect` samples all tickets first, then processes them, so its previews match the
  export; its leftovers check also reports names known from other sampled tickets or from
  the registry (with the real secret), even if `cross_ticket_names` is off.

**Consequences.**

- After upgrading, run `export --full` once: unchanged tickets are not fetched again, so their
  people would otherwise never reach the registry.
- More false positives: a customer called "Wagner" or "Neumann" makes that word disappear in
  every ticket (as before, but now archive-wide). Common words that are also names go into
  `name_part_ignore`.
- The registry is personal data in the same sense as the pseudonyms (linkable with the
  secret). Back it up with the archive; deleting it only weakens scrubbing until the next
  `export --full`. Turning the option off leaves the file unused.
