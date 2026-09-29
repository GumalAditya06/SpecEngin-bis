# SpecENGINE-BIS frontend and backend handoff

## Scope

The manufacturer MVP is an interactive frontend prototype and starts in the Assistant: ask a question → identify the standards that apply → read the evidence behind them → find a laboratory that can test against them. It preserves the existing dark design. The product is a research tool, not a case-management platform: workspace, compliance cases, case creation, task checklists, evidence capture and case statistics are not part of the MVP. Admin ingestion screens, accounts, official applications, payment, lab booking, file uploads, pricing and automatic regulatory monitoring are outside this prototype.

Run `npm run dev -- --webpack` from this directory. `.env.example` explicitly enables the local demo. Set `NEXT_PUBLIC_DEMO_MODE=false` and restart/rebuild to use `NEXT_PUBLIC_API_URL` instead. Only the exact value `true` enables demo mode; an absent value defaults to the live API. There is no automatic fallback from a failed live API to sample data.

Sample data is adapted from the project's existing seed database, not independently verified legal information. Orphaned sample relationships were removed in the frontend fixture only. The assistant in demo mode is a deterministic scripted preview, not Gemini. Supported examples: steel wire ropes, fire extinguishers, and water heaters; unknown products show an explicit no-evidence/clarification state. Missing documents or laboratories stay missing.

Navigation is five areas: Assistant, Standards, Services, Sources, Laboratories. `/` is the landing page; every primary action on it opens the Assistant.

### Removed from the MVP

The compliance workspace, its case board, case creation, the case detail screen (status, tasks, owner, target date, evidence links, notes, activity, export, selected laboratory) and the demo banner are gone. `/compliance`, `/compliance/new` and `/compliance/:id` are removed routes; nothing links to them and they answer with the 404 page. What was kept is the data layer, because it is reusable and the backend still serves it: `lib/api/client.ts` (`createComplianceCase`, `listComplianceCases`, `getComplianceCase`, `updateComplianceCase`), `lib/api/workspace-types.ts`, and the browser-local store `lib/demo/workspace.ts` with its `specengine-demo-cases-v1` key. No request reaches any of them, so the localStorage key is never written. Restoring case work is a routing change, not a rewrite.

Two dead files went with them: `app/compliance/*` and `components/landing/ProductJourney.tsx`, an unused product tour whose chapters described the case checklist. `components/Evidence.tsx` stays — it is the assistant's citation chip, not workspace evidence capture. Real corpus counts from `GET /api/v1/stats` also stay on `/standards`; they are API data, not operational metrics. Nothing in the MVP shows invented or operational figures, and no page offers pricing, plans, payment or a sales contact.

## Screen acceptance criteria and data needs

| Screen                  | User can                                                                          | Backend data                                                                |
| ----------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `/`                     | Read what the product does; open the Assistant or explore a catalogue              | None (static)                                                               |
| `/standards`            | Search, filter sector/status, sort, paginate, open result                         | Standard identity, title, description, sector, year, status, document count |
| `/standards/:id`        | Review sources/amendments/related standards/labs; ask a question about it       | Standard detail and linked entities                                         |
| `/sources`              | Search, filter document type/year/organization/access, paginate, open source  | Document summary, publisher, version, snippet, access policy                |
| `/sources/:id`          | Browse clause contents, read clause/full text where permitted, inspect metadata | Clause hierarchy, clause text, source URL, retrieval date, licence          |
| `/assistant`            | Submit product query, inspect response/citations, open a source, follow up     | Matched standards with IDs, answer, confidence, clauses, route, labs        |
| `/laboratories`         | Search by name; filter city, recognition status, testing capability, standard; open a laboratory | Lab identity, location, recognition, standard associations and listed tests |
| `/laboratories/:id`     | Read the standard → capability → laboratory relationship, recognition information and relevant sources | Linked standards, tests, contact information, documents behind those standards |
| `/services`             | Read what covers a BIS service route and open the screen that performs each step  | None (static)                                                               |

Every data screen has loading/error/empty handling. Unknown routes and unknown server-side catalogue IDs use a 404 page. Search/filter/page parameters are in catalogue URLs, so reload and browser navigation preserve them.

## Existing API contracts reused

Base prefix: `/api/v1`. Types: `lib/api/types.ts`. Transport: `lib/api/client.ts`.

| Method | Endpoint                  | Notes                                                                  |
| ------ | ------------------------- | ---------------------------------------------------------------------- |
| GET    | `/health`, `/stats`       | Liveness and catalogue counts                                          |
| GET    | `/standards`              | `q, sector, status, sort, order, limit, offset`                        |
| GET    | `/standards/:id`          | Includes documents, amendments, related, labs, schemes                 |
| GET    | `/search`                 | `q, source_type, licence, limit, offset`                               |
| GET    | `/documents/:id`          | Enforce metadata-only restrictions on the server                       |
| GET    | `/laboratories`           | `q, city, state, test, recognition_status, standard_id, limit, offset` |
| GET    | `/laboratories/:id`       | Tests and associated standards                                         |
| POST   | `/assistant/query`        | Structured answer; existing plain transport                            |
| POST   | `/assistant/query/stream` | SSE stages followed by final structured result                         |
| GET    | `/compliance-cases`       | Paginated summaries — retained, no screen calls it                     |
| POST   | `/compliance-cases`       | `{ product, standard_ids, query? }` — retained, unused                 |
| GET    | `/compliance-cases/:id`   | Basic detail — retained, unused                                        |
| PATCH  | `/compliance-cases/:id`   | Status only — retained, unused                                         |

Catalogue list responses: `{ total, limit, offset, results }`. Case list uses `cases` instead of `results`. IDs must remain stable across all endpoints. Return 404 for missing records, 422 for invalid inputs, and meaningful error responses for failed writes.

The case endpoints are the one part of this table with no consumer left in the MVP. The frontend keeps a typed client for them, so the contract should be treated as retained rather than deprecated; extending it is only worth doing if case management is brought back into scope.

The removed case board fetched every case-summary page to calculate local search and counts, and its selectors requested the first 100 options. Both were client-side shortcuts. If the board returns, it needs server-side search, status filters, aggregation and pagination, and searchable paginated selectors rather than a first-100 list.

## Sources

`/sources` is the primary source library. `/documents` and `/documents/:id` remain as thin redirects that preserve the query string, so existing links and bookmarks keep working; there is no second list and no second viewer.

The backend splits provenance (`sources`) from the parsed artefact (`documents`). The product presents one concept, so `lib/sources/types.ts` defines a single `SourceRecord`/`SourceReading` contract and `lib/sources/records.ts` adapts `/search` and `/documents/:id` into it. Every surface — library, detail page, Assistant, Standards, Services, Laboratories — renders the same `SourceCard`, `SourceMetadata` and `SourceDrawer`. Standards, Services and Laboratories resolve their sources through `GET /standards/:id`, so no new endpoint was added.

Filters: `q`, `source_type` and `licence` are server-side `/search` parameters. `year` and `publisher` are not, so the library reads one 100-item page and refines those two facets client-side; option lists are derived from the loaded records. The year is the edition year parsed from the reference (`IS 8921:2024` → 2024) because documents carry no separate publication year.

Gaps that the frontend works around rather than invents:

- `GET /search` does not return `standard`/`standard_id`, so a source opened from the library has no related-standard link.
- `GET /documents/:id` omits `standard`, `standard_id` and `source.title` in live mode; the adapters treat them as optional.
- "Referenced by Specengine" needs a persisted citation/answer index. Assistant citations are produced per response and never stored, so `specengineReferences()` returns `undefined` and the section stays hidden. Return a `SourceReference[]` from that function once `GET /documents/:id/references` exists and the section appears on its own.
- A cross-source search by year and publisher is wanted server-side; add them as `/search` parameters rather than widening the client-side refinement.

## Laboratories

The directory answers one question: which laboratory can test this product against this standard. `lib/laboratories/types.ts` defines a single `LaboratoryRecord` contract, `records.ts` adapts both laboratory endpoints into it, and `load.ts` resolves the data. `components/laboratories/LaboratoryCard.tsx` is the result card, `LaboratoryStandardCard.tsx` is the standard → capability relationship card, and `LaboratoryCapabilities.tsx` is the capability inventory grouped by standard. Sources are the shared `SourceRecord`/`SourceCard`/`SourceDrawer` from the Sources section — there is no laboratory-specific document viewer.

`GET /laboratories` returns no `tests` and no standard numbers, only `standard_count`, so the directory resolves `GET /laboratories/:id` for the page it is about to render. A single failed detail degrades that one card: `LaboratoryRecord.detailed` is `false` and the card says the capabilities are still loading rather than implying the laboratory has none. `loadLaboratoryRecords` is the place to delete when the list payload can carry capabilities inline.

Filters `q`, `city`, `recognition_status`, `test` and `standard_id` are all server-side parameters of `GET /laboratories`. The city and recognition-status options are derived from a 100-item corpus fetch, so a select only ever offers a value that exists. The standard filter needs the reverse mapping, which only exists per standard, so `loadLaboratoryStandardOptions` scans the first 25 standards through `GET /standards/:id/laboratories` and offers only those with at least one laboratory; a standard applied in the URL is kept in the list even with none, so the control never disagrees with the active filter. Capabilities stay a free-text field (`test`) with `datalist` suggestions drawn from the loaded records, because nothing enumerates every test name.

Removed from the MVP: the compare-laboratory checkbox, its sticky bar and `/laboratories/compare`. Comparison was self-contained — only that bar linked to the route — so nothing else was affected. It is not replaced by another feature. A standard with no indexed laboratory explains itself ("No laboratory for this standard") instead of showing a generic empty state.

Gaps that the frontend works around rather than invents:

- The `laboratories` table has no description, so the overview paragraph is omitted. `laboratoryOverview()` in `records.ts` is the integration point.
- The model has no recognition reference, approved scope or validity dates. `recognitionFacts()` states only status, standard association, location, address and contact; `laboratoryRecognitionDetail()` returns `undefined` and its reference/scope/validity rows appear by themselves once real columns exist. No recognition dates are invented.
- Facet endpoints are missing. City and status are read from a corpus page; the standard filter needs `N+1` calls. Add `GET /laboratories/facets` (or `standards?has_laboratory=true`) and the scan can be replaced with one request.
- No documents are attached to a laboratory. The relevant sources are the documents behind its associated standards, resolved through `GET /standards/:id` — the same route Standards and Services already use.
- `POST /assistant/query` accepts free text only, so the Assistant hand-off carries context in the question. `laboratoryContext()` already returns `{laboratory_id, laboratory, location, recognition_status, standards, capabilities}` and can travel as a structured field once the query payload accepts one.

## Extended case contract to build (out of MVP scope)

Authoritative frontend types: `lib/api/workspace-types.ts`.

`GET /compliance-cases/:id` should return the existing summary fields plus:

```ts
{
  id: string;
  product: string;
  status: "open" | "in_progress" | "closed";
  created_at: string; // ISO 8601 UTC
  standards: {
    id: string;
    is_number: string;
    title: string;
  }
  [];
  query: string; // original product context
  owner: string; // display name for MVP; user reference later
  due_date: string; // YYYY-MM-DD or empty
  laboratory_id: string | null;
  tasks: {
    id: string;
    title: string;
    done: boolean;
  }
  [];
  evidence: {
    id: string;
    name: string;
    url: string;
    added_at: string;
  }
  [];
  notes: {
    id: string;
    text: string;
    created_at: string;
  }
  [];
  activity: {
    id: string;
    text: string;
    created_at: string;
  }
  [];
}
```

The frontend sends one or more of these fields to `PATCH /compliance-cases/:id`:

```ts
{ status?: "open" | "in_progress" | "closed" }
{ owner?: string; due_date?: string }
{ laboratory_id?: string | null }
{ task?: { id: string; done: boolean } }
{ evidence?: { name: string; url: string } }
{ note?: string }
```

PATCH can return the complete case; the current client re-fetches GET after a successful write. Implement validation and persistence before returning success. Append activity in the same transaction as each mutation. Generate IDs/timestamps on the server, validate task and lab references, accept only HTTP(S) evidence links, constrain input lengths, and preserve notes on concurrent writes. Authentication/authorization must isolate users' cases before multi-user deployment.

This section documents a capability with no screen behind it. No MVP page creates, edits or stores a case, so nothing here is required to ship the current product. It is kept because the endpoint exists, the typed client is preserved, and the model is already derived.

Current live mode intentionally rejects extended mutations rather than pretending the backend supports them. Remove that capability guard in `updateComplianceCase` when the extended API is implemented and the feature is back in scope. Existing status changes still use the current endpoint. No seed case or localStorage content should be copied into a live database implicitly.

## Assistant requirements

- Return stable `standard_id` values so any saved record links to real records.
- Return citations with document/source IDs, clause/page when known, licence, and allowed excerpt.
- Match lab tests to the actual standard scope; do not list unrelated tests as applicable.
- Empty/ambiguous evidence must return `INSUFFICIENT_EVIDENCE` or `NEEDS_CLARIFICATION`.
- Emit real pipeline stage events as stages run; the demo shows sample-response wording.
- A displayed route is guidance backed by source data, not an official submission.

## Backend implementation order

1. Stabilize catalogue data, IDs, relationships, filters and pagination against these screens.
2. ~~Implement the extended case aggregate~~ — deferred; case management left the MVP. Keep `/compliance-cases` stable until then.
3. Connect real assistant retrieval/generation to the existing typed response contract.
4. Add identity/ownership, write validation, concurrency controls and production operations.
5. Add real source ingestion, update/version detection and trusted corpus expansion.

## Frontend validation

Run `npm run lint`, `npx tsc --noEmit`, and `npm run build -- --webpack`. Webpack is a supported local alternative where the execution environment blocks Turbopack's process/port operations. Fonts use the local system stack, so builds do not download Google Fonts.

Browser acceptance path: enter from the landing page into the Assistant → describe a product → open a recommended standard → inspect a cited source → filter to the laboratories testing that standard → open a laboratory → ask the Assistant about testing. Also check unmatched queries, filtered empty results, metadata-only documents, keyboard navigation, mobile overflow, and invalid or removed routes. For sources: filter by document type/year/organization/access → open a source → jump to a clause from the contents → open the shared drawer from Assistant, Standards and a laboratory → ask the Assistant from the drawer. For laboratories: search a name → filter city, recognition status, testing capability and standard together → open a laboratory → read the standard → capability → laboratory chain → open a relevant source in the shared drawer → ask the Assistant about testing.

### Verified on 2026-09-27 (product cleanup)

- `npx tsc --noEmit`, ESLint over `app`, `components` and `lib`, and `next build --webpack` passed. The route table is `/`, `/assistant`, `/services`, `/standards`, `/standards/:id`, `/sources`, `/sources/:id`, `/laboratories`, `/laboratories/:id`, `/documents` (redirect), `/documents/:id` (redirect), `/search` (redirect), `/brand`, `/compliance` is gone.
- 51 Chromium checks for this pass: primary and footer navigation are exactly Assistant, Standards, Services, Sources, Laboratories, and the mobile menu matches; the app shell renders skip link, nav, main and footer only; `/compliance`, `/compliance/new`, `/compliance/:id` and `/laboratories/compare` all resolve to the 404 page; no page on `/`, `/assistant`, `/standards`, `/sources`, `/laboratories` or `/services` contains workspace, case, in-progress/closed, pricing, plan, subscription, payment, checkout, billing, upgrade, trial or sales-contact copy, and none links to a removed route; every primary action on the landing page opens `/assistant`; the landing journey ends at testing with no workspace step; `localStorage` stays empty, so the retained case store is never written; all 18 internal links reachable from the six main pages resolve.
- Services page: one `h1`, the five stages, the explicit "what this prototype does not do" limits, a working preset question that prefills the Assistant composer, and `h1 → h2` heading order.
- Design language preserved: the services cards compute to the same surface, 1px border, 20px radius and 24px padding as the standards card, and `--page`, `--surface`, `--line`, `--chroma` and `--r-card` are unchanged. No CSS was added to `globals.css`.
- Responsive: no horizontal overflow at 360px or 768px on `/services`, and every control on it is at least 36px tall.
- The earlier suites were re-run after the change: 89 laboratory checks and 124 sources checks pass, so no other section regressed.
- Fixed during this pass: the services call to action was a 20px text link, below the tap-target floor used elsewhere, and now uses the shared `.button`.
- Deliberately kept: the `/stats` corpus counts on `/standards` (real API data, not operational metrics), the `Demo`/`Live` mode badge in the header (an honest mode indicator, not a banner), the sample-data line in the footer and the scripted-preview note on assistant answers, and the typed case client, types and demo store, which no screen calls.
- Live backend integration, live Gemini calls, and non-Chromium browsers were not tested in this frontend pass.

### Verified on 2026-09-27 (laboratories)

- `npx tsc --noEmit`, ESLint over `app`, `components` and `lib`, and `next build --webpack` passed. The route table no longer contains `/laboratories/compare`.
- 88 Chromium checks: the eyebrow, heading, supporting text and search placeholder; all four filters as data-derived options; each filter alone and all three combined; the empty and clear-filters states; reset; the standard deep link from a standard page; a standard with no laboratory; card hierarchy order; the accessible status badge; every detail section; the standard → capability → laboratory chain; the Assistant hand-off URL; the shared drawer opening, taking focus, closing on Escape and rendering as the 360px bottom sheet; keyboard operation of a filter and focus returning to it; nav, landing, `/`, `/standards`, `/sources`, `/assistant` and `/compliance` still working; no runtime or console errors.
- Responsive: no horizontal overflow at 360px on `/laboratories` and `/laboratories/:id`, nor at 768px on `/laboratories`. Filters stack, detail sections stack, and every control in the section is at least 36px tall.
- The existing laboratory card design is preserved: the result card computes to the same surface, 1px border, 20px radius and 24px padding as the standards card, and no CSS was added to `globals.css` for this feature.
- The 117 sources checks were re-run unchanged and pass, so the other sections are unaffected.
- Fixed during this pass: filter changes updated the URL without refetching, because the directory was not remounted with the new parameters; the Assistant call to action carried an `aria-label` that hid its visible label (WCAG label-in-name); an `aria-label` was missing on the testing-capability field; a standard filtered by id but holding no laboratory disappeared from its own select, so the control disagreed with the active filter.
- Live backend integration, live Gemini calls, and non-Chromium browsers were not tested in this frontend pass.

### Verified on 2026-09-27 (sources)

- `npx tsc --noEmit`, ESLint over `app`, `components` and `lib`, and `next build --webpack` passed. The only ESLint errors in the repository are inside the committed `.vercel/output` build artifacts.
- 28 source-layer checks against the demo fixture: list and detail adapters, licence gating, clause anchors, facets, filter intersection, `editionYear`, `sourceHref`, `sourceQuestion`/`assistantHref`, and citation adaptation.
- 117 Chromium checks (superseded count; this suite now runs 124 checks): library cards and access badges; each of the four filters in isolation and combined, including the empty state and clear-filters; server-side search; clause contents navigation, `aria-current` and `?clause=` deep links; the three reading tabs; the drawer opening and closing from the detail page, Assistant, Standards, a case and a laboratory; the Assistant CTA prefilling the composer; `/documents` and `/documents/:id` redirecting with the query string preserved; nav, footer and landing links; unknown ids rendering the not-found page.
- Drawer keyboard behaviour: focus moves to the close control, stays inside the dialog on Tab, Escape closes it, and focus returns to the trigger.
- Responsive: no horizontal overflow at 360px on `/sources`, `/sources/:id`, `/standards`, `/assistant`, `/compliance`, `/laboratories` and `/`, nor at 768px. The drawer is a full-width bottom sheet at 360px and a 480px side panel at 768px. No runtime or console errors.
- Fixed during this pass: `editionYear` read the standard number instead of the edition suffix (`IS 12034:2025` reported 2034); filter `<select>` elements had no stable accessible name; `page=0` was left in catalogue URLs; drawer focus relied on `autoFocus`, which the dialog did not apply.

### Verified on 2026-09-27 (parts 1–4)

Parts of this pass verified the compliance workspace, which the product cleanup later removed. The record is left as it was written.

- ESLint, TypeScript, and production build (`--webpack`) passed.
- Chromium journey checks passed: catalogue filtering/pagination, metadata-only access, laboratory comparison, assistant citation dialog and unmatched products, case creation, tasks, notes, evidence links, owner/date/lab changes, status, reload persistence, and JSON export.
- Direct case creation with a preselected standard passed. Simulated browser storage failure displayed an error, retained the unsaved note, and left persisted data unchanged.
- Eleven routes passed a 360px phone-width overflow check; the home page also passed at 768px. Mobile navigation opens and closes with Escape.
- No backend requests were made in demo mode. No runtime errors were observed; the additional creation/failure checks also reported no hydration errors.
- Live backend integration, live Gemini calls, actual BIS source validity, and non-Chromium browsers were not tested in this frontend pass.
