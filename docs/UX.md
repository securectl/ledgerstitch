# LedgerStitch — UX spec (v1.1, "Evergreen v2")

Owner: Frontend / UX. Source of truth for screen flows, states, copy and accessibility.
Code: `ledgerstitch/web/static/` (vanilla ES modules, no build step, no network assets).

## 0a. Evergreen v2 (current design)

The UI follows the founder-approved **Evergreen v2** direction: picture first, minimal text.
Numbers, charts, rings, gauges, bars, icons and status dots carry the meaning; card titles are
1–3 words, captions about six, and paragraphs live behind "Why?" / "Details" disclosures.
Legal copy is one small line ("Not tax advice").

**Information architecture** (sidebar at ≥ 1100px, icon rail 720–1099px, bottom tab bar < 720px):

| Group | Route | Screen |
|---|---|---|
| — | `#/overview` (home after unlock) | KPI strip with sparklines and deltas, profit chart, Next up checklist; `overview` add-on cards below |
| Transactions | `#/ledger` | Clean rows, month dividers with subtotals, docked bulk bar, detail panel (bottom sheet on narrow screens); `ledger-toolbar` add-on cards |
| | `#/review` | One-card triage (A / D / B / P / T / I) or a by-merchant list |
| | `#/receipts` | Stored receipts and the transactions they're linked to; `receipts` add-on cards |
| | `#/import` | Drop statements, per-file balance ✓/✗, files, accounts; "Connect banks" is a disabled Coming-soon tile |
| Reports | `#/report` | Lane totals, Schedule C, top categories, month by month, by account |
| (add-ons) | `#/<id>` | Screens registered with `ui_screen` appear in their `group`, sorted by `order` |
| bottom | `#/settings` | Workspace · Privacy & AI · Security (recovery key, backup) · Your data · Plan · Rules, plus add-on sections |

The top bar holds the page title, at most one screen action and any `topbar` add-on cards; every
screen's exports are in the single **Export** sheet (`export-menu.js`), which also renders `export`
add-on sections.
The sidebar footer carries the privacy line, the AGPL source link, **Theme** (dark default, light
theme persisted in `localStorage`, OS preference as the fallback) and **Lock**.

**Design tokens** live at the top of `styles.css` (`:root`, `:root:not([data-theme="light"])`,
`:root[data-theme="light"]`): colour (`--bg`, `--surface*`, `--line*`, `--ink*`, `--primary*`,
`--pos/--warn/--danger/--info`, `--lane-*`, `--chart-*`, `--nav-*`), type scale (`--fs-12` … `--fs-48`,
Manrope display / Inter UI / JetBrains Mono, vendored under `fonts/` with their OFL licence), 8-pt
spacing (`--s-1` … `--s-9`), radii (`--r-card` 16 · `--r-ctl` 10 · `--r-chip` 999), control sizes,
shadows, motion (disabled under `prefers-reduced-motion`) and the focus ring. `theme-init.js` sets
`data-theme` before the stylesheet loads. Chart helpers (sparkline, combo chart, ring, gauge, range
bar) are in `charts.js`; shared states (first run, all caught up) in `states.js`; the shell in `shell.js`.
Sections 1–7 below describe behaviour and copy that still apply; where they mention the old top
navigation or export tiles, the table above wins.

## 0. Principles

1. **Calm over clever.** Our users are anxious about taxes and privacy. Use plain words, one primary action per view, and generous whitespace. Never show a stack trace or an HTTP code.
2. **The privacy promise is shown everywhere.** "Runs on your computer · Encrypted with your passphrase · AI sees merchant names only" appears on the welcome screen and in the app footer.
3. **Decide once.** Review is grouped by merchant. One decision covers all of that merchant's rows and, by default, becomes a rule.
4. **Keyboard first for Bree, mouse-friendly for Sam.** Every action is reachable both ways.
5. **Money is legible.** Values use `Intl.NumberFormat('en-US', USD)` and `tabular-nums`, right-aligned. Money out is shown as "−$45.00" in a muted rust color and money in as "+$45.00" in green. The sign is always present, so color is never the only signal.

## 1. Architecture of the UI

| File | Role |
|---|---|
| `index.html` | Shell: token `<meta name="ll-token">`, nav, `<main>`, toast region, live announcer. No inline script. |
| `app.js` | Boot, hash router (`#/welcome`, `#/import`, `#/review`, `#/ledger`, `#/report`, `#/settings`, plus add-on routes from `status.ui`), nav, lock, 402 handling, session-expired banner. |
| `shell.js` | Layout, navigation built from core screens plus `status.ui.screens`, slot rendering for add-on cards, the neutral locked card. |
| `api.js` | `fetch` wrapper; adds `X-LedgerStitch-Token`; maps errors; `download()` (fetch → blob → `a[download]`); switches to `mock.js` when `?mock=1`. |
| `state.js` | Cached status, categories, accounts, settings; category `<select>` builder; review badge. |
| `ui.js` | DOM helper `h()` (text nodes only, never `innerHTML` with data), icons, logo, formatting, toasts, modal (focus trap, Esc, focus restore). |
| `screen-*.js` | One module per screen. |
| `mock.js` | In-browser mock of the core API with synthetic data; loads `mock.js` of installed add-ons listed in `/addons/index.json`. |
| `styles.css` | Tokens on `:root`; light and dark themes; responsive. |
| `logo-mark.svg` | Favicon / app mark. |
| `disclaimer.txt` | Shown at the foot of the Report screen. |

Error mapping (api.js):
- **401**: on `/api/unlock` and `/api/passphrase` it means the passphrase was wrong, and the form shows it inline. Anywhere else it means a bad or expired token, and a red banner says "Session expired — reload LedgerStitch to reconnect."
- **409 `{locked:true}`**: the vault is locked (for example by idle auto-lock). The app shows a toast and returns to the Unlock screen.
- **402**: the feature is locked. The shell shows the neutral locked card (title, **Get the full product**, **Have a key?**).
- **0 / network**: "Can't reach LedgerStitch on this computer. Is the app still running?"
- **Everything else**: shows `{"error": ...}` verbatim (the backend writes human copy).

Demo / screenshot switches: `?mock=1`, `&unlocked=1`, `&localai=running|error` (background local AI pill), `&empty=1` (to exercise onboarding), `&theme=dark|light`. A plain public checkout's demo shows only Community features; installed add-ons bring their own demo routes (docs/PLUGINS.md §7.4).

## 2. Screens

### 2.1 Welcome / Unlock (`#/welcome`)
**Layout:** split screen. On the left, a green hero with the wordmark, headline "Every statement, one tidy report your CPA will love.", and three privacy bullets. On the right, a panel.

**Unlock flow:** `GET /api/vaults` lists workspaces as cards (initials avatar, name, path). Clicking a card expands an inline passphrase field (Show/Hide toggle) and an **Unlock** button. If there is only one vault, it auto-expands and focuses. Submitting calls `POST /api/unlock {path, passphrase}`.
- If the passphrase is wrong (401), an inline message appears: "That passphrase didn't unlock this workspace. Check Caps Lock and try again." The field is marked `aria-invalid` and its text is selected.
- After unlocking, the app goes to Review if accounts exist, otherwise to Import.

**Create flow:** fields for workspace name, passphrase (with a 4-step strength meter and label) and confirm. A warning box reads "**We can't recover this passphrase — there's no account and no server.**" with a required checkbox: "I've saved my passphrase somewhere safe".
- Client rules: at least 10 characters (the server minimum is 8), a strength score of 2 or more, both entries match, and the box is ticked.
- Submitting calls `POST /api/vaults`, shows the toast "Workspace created — Encrypted and saved on this computer.", and goes to Import.

**Empty state:** with no vaults, the panel opens directly in Create mode with the title "Set up your first workspace".

**Footer badges:** Runs on your computer · Encrypted with your passphrase · AI sees merchant names only.

### 2.2 Import (`#/import`)
- **Drop zone:** a large dashed card that is a `role=button` (Enter or Space opens the picker). Copy: "Drop your statements here — CSV or PDF, from any bank or card. Add as many files as you like — a whole year at once is fine." Dragging over turns it green; drops outside the zone are swallowed so the browser doesn't navigate away.
- **Optional "Account name":** sent as `account_label`.
- **Upload:** files go one per request to `POST /api/imports` (field `file`), so each file gets its own card and progress. Card states:
  - *Reading:* spinner and "Reading and encrypting…".
  - *Imported:* bank, account label, Rows, New (with "(already imported)" when 0), and a warnings list. If confidence is below 0.85 it shows a "Check this one" chip and a **Fix columns** button.
  - *Needs mapping* (`needs_mapping` item, or a 4xx with `needs_mapping`): "We don't recognise this layout yet. Tell us which columns are which — it takes about 20 seconds." with a **Map columns** button that is auto-focused.
  - *Error:* the message, plus **Try again** for transient errors.
  - Categorization warnings returned by the import (`categorize.warnings`) appear as toasts.
- **Column mapping dialog:** uses `POST /api/imports/preview`. It offers selects for Date, Description and Date format; a segmented control for "One amount column" vs "Separate debit & credit"; and a sign toggle for "Negative = money out" vs "Positive = money out", with hint copy for bank vs card. It also asks for the account name. A live preview table highlights the chosen columns with role labels, and "How we'll read it" shows three parsed sample rows ("−$4.50 out"). Columns are pre-guessed from header names or `detected.mapping`. Submitting calls `POST /api/imports/mapped` with `file`, `mapping` (JSON) and `account_label`.
- **Next step card:** "N transactions imported. Card payments, transfers and duplicates have been set aside." with buttons **Categorize again** and **Review transactions →**.
- **Side panel:** "Works with your bank" (Chase, Amex, Capital One, Citi, Bank of America, Wells Fargo, Discover, U.S. Bank, Apple Card, Gemini), the note "PDF statements work too…", and "Where do I get these?" help text.
- The accounts in the workspace are listed below the results.

### 2.3 Review (`#/review`) — the heart of the product
- **Data:** `GET /api/transactions?status=review&limit=5000`, excluding duplicates, grouped client-side by `merchant` (falling back to the description). Groups are sorted by absolute total, biggest first. The progress total comes from `GET /api/transactions?limit=0&include_duplicates=false`.
- **Header:** "Decide once per merchant — we apply it to every matching transaction and remember it for next time." It has **Run categorization** and **Shortcuts** buttons, a progress bar labelled "**252** of 345 reviewed · 15 merchants left", and a search field (`/`).
- **Group card (collapsed):** merchant, an "N transactions" chip, accounts · date range, a suggestion chip (category, colored by lane) with a confidence bar and %, and the total.
- **Group card (active, expanded):**
  - Lane radio chips: Business / Personal / Transfer / Income, each with its key hint. Arrow keys move within the group.
  - A category `<select>` grouped by lane.
  - A "Remember for this merchant" switch, **on by default**.
  - **Skip** and **Approve N ↵** buttons.
  - A "why" line, e.g. "AI suggested Rent & Lease (71% confident). Your choice applies to all 6 transactions."
  - A mini table of up to 5 rows plus "+ N more — see them in the ledger" (a deep link with `q` and `status`).
- **Smart coupling:** choosing a Transfer/Income category snaps the lane to match. Choosing the Transfer or Income lane swaps a non-matching category to "Transfer Between Accounts" or "Income & Deposits". Moving back to Business/Personal from a transfer or income category resets it to Uncategorized.
- **Approve:** if no lane has been chosen, the toast "Choose a lane first — Press B, P, T or I". Otherwise:
  - With remember on, `PATCH /api/transactions/{first} {category, lane, schedule_c, status:'approved', remember:true}`; without it, nothing is sent here.
  - Then `POST /api/transactions/bulk` for the remaining ids (or all ids when remember is off).
  - The card slides out and focus moves to the next merchant. A toast reads "WEWORK → Business · 6 transactions approved as Rent & Lease · rule saved" with **Undo** (bulk-restores the previous category and lane with status review). If the rule changed other rows (`changed`), the queue reloads quietly.
- **Empty states:**
  - Queue empty but transactions exist: confetti (skipped under reduced motion) and "**All caught up!** Every one of your N transactions has a lane. Your report is ready for your CPA." with **Browse the ledger** and **See your report**.
  - No transactions at all: "Nothing to review yet" with **Import statements**.
  - Search with no match: "No merchants match" with **Clear search**.

### 2.4 Ledger (`#/ledger`)
- **Filters row:** search (debounced), Lane, Category, Account, Status, Year (defaults to the tax year), and **Clear filters**. Filters are mirrored into the hash, so `#/ledger?q=…&status=review` deep links work.
- **Table columns:** select box, Date, Description (merchant in bold plus the raw descriptor in mono; chips for *Duplicate*, *Matched transfer*, *Refund*), Account, Category (inline select), Lane (inline pill select), Amount (right-aligned, signed, colored), Status (dot plus "Needs review / Auto / Approved", with a tooltip "Sorted by AI · 92%"), and Note (inline input that saves on blur or Enter; Esc cancels).
- Duplicate rows are dimmed and struck through.
- **Inline edits** call `PATCH /api/transactions/{id}` with `status:'approved'`. The row re-renders from the returned `transaction` and a toast confirms.
- **Sorting:** clickable headers with `aria-sort`. The current page is sorted client-side and `sort`/`order` are also sent to the server (see API notes).
- **Multi-select:** row checkboxes, shift-click to select a range, and a header checkbox for the page. Selecting rows reveals a floating dark **bulk bar** with "N selected", Set lane…, Set category…, **Approve**, Mark for review, and Clear. It calls `POST /api/transactions/bulk`.
- **Pagination:** 25/50/100/250 per page, "Showing 1–50 of 346", Previous/Next.
- **Keys:** `/` focuses search; Esc clears the selection.

### 2.5 Report (`#/report`)
- **Header:** "2025 expense report", business name, and a year select.
- **Lane total cards** (each with a colored left edge): Business expenses, Personal, Transfers excluded (`totals.excluded`), Income, and Needs review (links to Review, or shows "All reviewed").
- **Warning banner** when `review_count > 0`.
- **Export card:**
  - **Excel for your CPA** (primary), CSV, QuickBooks Online, QuickBooks Desktop (IIF).
  - Each button shows "Preparing…" while it runs, uses the filename from `Content-Disposition`, and toasts "Download ready — … Keep it somewhere safe: it isn't encrypted."
- **Schedule C, Part II table:** line, name, count, and gross. A Deductible column (with an "N% limit" chip) appears only when a line is limited, as meals are. It has a total row from `schedule_c_total`, and the `meals_note` appears under it when relevant.
- **Top categories:** a horizontal bar chart (inline SVG), top 8 plus "Other (n)", with a Business / All spending toggle, a hover tooltip, and an `aria-label` that lists every value.
- **Month by month:** a 12-month column chart (inline SVG, rounded tops, gridlines, $ axis) with a Business / Personal / Income toggle and hover tooltips.
- **By account table:** for reconciling against statements.
- **Footer:** the text of `disclaimer.txt` (falls back to `summary.disclaimer`).
- **Empty state:** "No transactions in 2025" with **Import statements**.

### 2.6 Settings (`#/settings`, `?section=business|ai|rules|license|security`)
A left section nav (sticky). Each section is a card with a footer action bar. Add-on sections
registered with `ui_settings` are added to the nav in their `order`.
- **Business:** Business name, Tax year (blank = automatic), and Business profile (500 chars, with counter). The hint reads "Describe your business in a sentence — helps the AI pick lanes. Don't include names or account numbers."
- **AI & privacy:**
  - Toggle "Use AI to suggest categories".
  - **Local AI** card: status chips (Ollama reachable, model installed), **Set up local AI** (consent dialog, docs/AI.md), progress, **Remove model** and **Withdraw consent**.
  - Ollama address field (loopback only; in server mode also the operator's `LEDGERSTITCH_OLLAMA_URL`).
  - A live status sentence, e.g. "When you run categorization, redacted merchant names (only) go to the model on this computer."
  - "The AI never receives" checklist: amounts or balances, dates, account or card numbers, names of people, addresses or phone numbers, your files.
  - Buttons: **See exactly what the AI receives**, **Run categorization**, **Save**.
- **AI preview modal:**
  - It shows the merchant-name count, where the model runs, and a "No amounts · no dates · no account numbers" chip.
  - The descriptor list appears in two columns.
  - A `<details>` labelled **Show full request** reveals the full request in a `<pre>`, with a Copy button.
  - If AI is off, a note explains that nothing is sent.
- **Rules:** a table with "When merchant contains" (mono pattern), Category, Lane chip, Account, and Delete (with confirmation: "Transactions already sorted keep their category"). Empty copy: "No rules yet. They'll appear here as you review transactions."
- **License:** a plan box (Community, or the plan of an entered key, with email and expiry; plan names come from `plan_labels`). A key textarea with **Activate** calls `POST /api/license` and updates the status. A valid key also gets **Remove license**. Community has no usage bars: there are no caps. A plain **Get the full product** link goes to the website.
- **Security:** change passphrase (current, new with strength meter, confirm) via `POST /api/passphrase`; a 401 shows "Your current passphrase didn't match." An auto-lock select (5 min to 4 h) saves `auto_lock_minutes`. A **Lock now** button is included.

### 2.7 Global
- **Top nav:** wordmark; Import · Review (orange count badge) · Ledger · Report · Settings; a workspace pill; and **Lock**.
- **Toasts:** bottom-right, at most 4, in an `aria-live="polite"` region (errors use `role=alert`). Success lasts 4 s, actions 7 s, errors 8 s, and each has a dismiss button.
- **Locked card (402 or `state: "locked"`):**
  - The registered title of the screen, card or settings section, and a lock icon.
  - **Get the full product** (opens the website) and **Have a key?** (opens license entry).
  - The core never describes the locked feature, and never pops it up unasked.
- **Session-expired banner (401):** a red bar with **Reload**.
- **Auto-lock (409):** the toast "Your workspace was locked", then the Unlock screen.
- **Loading:** spinner and copy per screen ("Adding everything up…", "Loading transactions…"). Busy buttons swap to a spinner and verb ("Encrypting…", "Saving…", "Categorizing…", "Re-encrypting…").

## 3. Keyboard shortcuts

| Where | Keys | Action |
|---|---|---|
| Review | `J` / `↓` | Next merchant |
| Review | `K` / `↑` | Previous merchant |
| Review | `B` `P` `T` `I` | Set lane Business / Personal / Transfer / Income |
| Review | `C` | Focus category |
| Review | `R` | Toggle "Remember for this merchant" |
| Review | `Enter` | Approve merchant |
| Review | `S` | Skip |
| Review | `Z` (or `U`) | Undo last approval |
| Review, Ledger | `/` | Search |
| Review | `?` | Shortcut sheet |
| Ledger | `Esc` | Clear selection |
| Dialogs | `Esc` | Close; Tab is trapped; focus returns to the opener |

Shortcuts are ignored while typing in inputs or selects, while a dialog is open, and when ⌘/Ctrl/Alt is held.

## 4. Copy deck (key strings)

- Privacy row: "Runs on your computer · Encrypted with your passphrase · AI sees merchant names only"
- Passphrase warning: "We can't recover this passphrase — there's no account and no server."
- Wrong passphrase: "That passphrase didn't unlock this workspace. Check Caps Lock and try again."
- Import drop: "Drop your statements here"
- Import subhead: "Your files are read and encrypted on this computer. Nothing is uploaded anywhere."
- Mapping title: "Match your columns" — "pick what each column means. We'll remember this layout."
- Review subhead: "Decide once per merchant — we apply it to every matching transaction and remember it for next time."
- Celebrate: "All caught up!"
- Export toast: "Download ready — … Keep it somewhere safe: it isn't encrypted."
- Locked: "Get the full product" · "Have a key?"
- Network: "Can't reach LedgerStitch on this computer. Is the app still running?"
- Locked: "Your workspace was locked — Unlock it with your passphrase to continue."
- Session: "Session expired — reload LedgerStitch to reconnect. Your data is safe on this computer."

## 5. Accessibility notes

- **Semantics:** a skip link; a `<main>` landmark that gets focus (its h1) on route change; `nav[aria-label]`; tables with `caption` / `scope` / `aria-sort`; the lane picker uses `role=radiogroup` and `role=radio` with `aria-checked`; progress bars use `role=progressbar` with value text.
- **Live regions:** toasts are polite and errors are alerts. A separate assertive announcer reads lane changes and the active merchant while you use the keyboard in Review.
- **Focus:** a 2px focus ring (`--focus`) via `:focus-visible` on every interactive element. Modals trap focus, close on Esc, and restore focus to the opener.
- **Color:** lanes always carry a text label. Money always carries a sign, plus an `aria-label` ("money out $45.00"). Confidence shows as a bar plus a %. Status colors are never the only cue. Text tokens were chosen for AA contrast on both themes (ink #17201C on #F6F7F5, ink-3 #6A746F ≥ 4.5:1 on white; the dark theme uses #E8EEEB / #8B9893 on #161D1A).
- **Charts:** the SVG has `role=img` with an `aria-label` that lists every value. The same numbers also appear in the Schedule C and By account tables.
- **Motion:** `prefers-reduced-motion` turns off transitions and decorative animation (confetti, pop, slide), keeps a slow spinner, and switches JS scrolling to instant. The rule uses `transition: none`, not a tiny duration.
- **Forced colors:** chips and lane pills get borders; bars use `Highlight`.
- **Forms:** every input has a `<label for>`. Errors are inline, set `aria-invalid`, and move focus to the first invalid field.
- **Responsive:** designed for 1024px+, degrades to tablet (stacked report and settings, 2-column review decision) and down to phone width (the nav wraps and tables scroll horizontally).

## 6. Security notes for the frontend

- No third-party scripts, fonts, images or CDNs. The CSP is `default-src 'self'` with no inline scripts or handlers; all listeners are attached in JS.
- Every bank or user string is inserted as a text node (`h()`); there is no `innerHTML` with data.
- The token comes only from the meta tag and is sent only as a header. Downloads go through fetch → blob, so the token never appears in a URL.
- API keys are write-only from the UI: the UI never receives them back, only `has_*` flags.
- Exported files are plaintext. The UI says so in the download toast.

## 7. Open API requests (for backend reconciliation)

1. `GET /api/transactions` — accept `sort` (date|description|account_id|category|lane|amount|status) and `order` (asc|desc). The UI already sends them. Until then it sorts within the current page only.
2. `POST /api/transactions/bulk` — optionally accept `remember: true` (one rule per distinct merchant among `ids`). Today the UI does PATCH-with-remember on the first id plus bulk for the rest.
3. Import results — a per-file `needs_mapping` (already present) plus, if possible, `detected.confidence` in all cases, which drives the "Check this one" chip.
4. Optional: `GET /api/transactions` could return `merchant_groups` for the review queue to avoid pulling 5,000 rows on very large vaults.
