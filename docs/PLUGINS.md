# LedgerStitch plugin API (core side)

Status: contract version 2 (`ledgerstitch/plugins.py`, `ledgerstitch/editions.py`).
Audience: authors of add-ons. The core is AGPL-3.0-only. Add-ons are separate works that use
only the interface below. Paid add-ons extend LedgerStitch through this same API; what they do
is described on the website ({{SITE_URL}}), not here.

The core knows nothing about any particular add-on. It offers generic hooks: AI categorization
on redacted descriptors, API routes, settings, storage, CLI commands, license plans, named
services between add-ons, and UI slots (screens, cards and settings sections) that load the
add-on's own browser modules. An add-on that isn't installed leaves no trace in the UI.

## 1. Discovery

- An add-on is an installed Python distribution. It declares an entry point in the group
  `ledgerstitch.plugins`:

  ```toml
  [project.entry-points."ledgerstitch.plugins"]
  hello = "ledgerstitch_hello.plugin"     # name = namespace, value = module with register()
  ```

- The entry point resolves to a module with `register(registry)`, or to that callable itself.
- The namespace is the module's `NAMESPACE` attribute, or the entry-point name if it has none.
  It must match `^[a-z][a-z0-9_]{0,31}$`.
- **When add-ons load:** `plugins.load()` runs once, from `api.create_app()`, the CLI and
  `Workspace()`. It's idempotent.
- **Community edition:** with `LEDGERSTITCH_EDITION=community` (or a Community build constant),
  discovery is skipped entirely.
- **Broken add-ons:** a plugin that fails to import or register never breaks the core. The
  failure is logged and recorded in `plugins.registry().errors`. Registration is transactional:
  either everything a plugin registers is applied, or nothing is.
- **Tests and embedding:** `plugins.register_local(module_or_callable_or_import_path, namespace=None)`.
- **Clearing the registry:** `plugins.reset()`, or the test fixtures `community` and
  `fresh_registry` in `tests/conftest.py`.

Several add-ons can be loaded at once. Each gets its own namespace, its own storage, its own
settings keys and its own static folder.

## 2. The registry API (namespace-bound, passed to `register`)

### 2.1 Core hooks

| Call | Purpose |
|---|---|
| `feature(name, *, tier="pro", label="")` | Declares a feature the add-on provides, e.g. `hello.greeting`. `tier` says which license tier unlocks it (section 4) |
| `license_plans({plan: [tiers]}, *, labels={plan: "Display name"}, aliases={old: new})` | Declares which license plans unlock this add-on's feature tiers (section 4) |
| `settings(defaults, editable, validators, secrets=())` | Adds settings keys. `validators[key](value) -> cleaned` raises `ValueError` to reject (the API returns 422). `secrets` are never sent to the browser: the API exposes `has_<key>` instead |
| `vault_migrations({key: legacy_vault_key})` | Read-through migration for this add-on's `PluginStore` (section 5) |
| `ai_categorizer(fn, *, feature=None)` | The AI hook (section 3). The first one registered is used; while `feature` is locked it is not called |
| `ai_preview(fn)` | `fn(descriptors, categories, settings, plugin_store) -> dict`, merged into `GET /api/ai/preview` (for example to show what the add-on would send) |
| `ai_feedback(fn)` | `fn(plugin_store, model_key: str, agreed: bool)`, called when the user confirms or corrects an AI suggestion |
| `ai_providers(*ids)` | Extra values allowed for the core `provider` setting (`off` and `ollama` are the core's own) |
| `api_routes(fn, *, gated={path: feature})` | `fn(app, ws_getter)` adds FastAPI routes. Add-on routes are installed **before** the core's routes. Each path in `gated` (exact match or prefix) answers 402 while its feature is locked |
| `cli_commands(fn)` | `fn(subparsers)` adds CLI subcommands |
| `data_purger(fn)` | `fn(workspace, scope)`: removes what this add-on keeps outside the vault when the user purges their data |
| `provide(name, fn)` | A named service (section 2.2) |
| `tax_due(fn)` | `fn(workspace, year, today=None) -> {"date": "YYYY-MM-DD", "amount": float, "label": str} \| None`: the next tax payment due on or after `today`, if an add-on can estimate it. Read it with `plugins.tax_due(workspace, year, today=...)`, which returns the first sane answer and skips hooks that raise or answer something malformed |
| `static_dir(path)` | A folder of browser files served at `/addons/<namespace>/…` (section 6) |
| `ui_screen(...)`, `ui_card(...)`, `ui_settings(...)` | UI registrations (section 7) |

**Settings rules:**
- An add-on cannot register a core settings key (`business_name`, `business_profile`,
  `tax_year`, `auto_lock_minutes`, `license_key`, …).
- An add-on cannot register a key another add-on already owns.
- Keys from an add-on that is not installed are kept in the vault untouched, but they are
  never exposed.
- Writing such a key through `PUT /api/settings` returns 422 with a clear message.

### 2.2 Named services: `provide(name, fn)`

`provide(name, fn)` publishes a callable under a name. Another add-on, or the core, looks it up
with `plugins.registry().provided(name)`, which returns every `(namespace, fn)` registered under
that name, in load order (an empty list when nobody provides it). This keeps add-ons
independent: neither imports the other, and either can be missing.

- Names are short identifiers (`hello.greet`, `selftest`). Use your namespace as a prefix for
  your own services.
- The core uses two names itself:
  - **`selftest`**: `fn() -> [(name, check_fn), ...]`; the desktop app's `--self-test` runs each
    `check_fn()` (it returns a short detail string or raises), so an add-on can check its own
    dependencies in a frozen build.
  - **`setup.check`**: `fn() -> {"id", "label", "value", "ok", "icon"?}`, an extra row on the
    first-run setup screen's system check.
- The caller treats a missing service as "not available" and carries on.

```python
for namespace, greet in plugins.registry().provided("hello.greet"):
    text = greet("Sam")
    break
```

## 3. The AI hook: the redaction boundary

```python
def ai_categorizer(descriptors: list[str], categories: list[dict], settings: dict,
                   plugin_store: PluginStore) -> dict[str, dict] | plugins.AIResponse: ...
```

**What the core does, in order** (`ledgerstitch/ai/categorizer.py`, auditable AGPL code):
1. Applies rules, then built-in heuristics.
2. Calls the hook only if **all** of these are true:
   - an `ai_categorizer` is registered;
   - the hook's feature, if it named one, is unlocked for the workspace's license;
   - `settings["ai_enabled"]` is true.

   Otherwise, with `provider` set to `ollama`, the core's own local model gets the same
   redacted payload instead.
3. Builds the payload with `build_ai_payload()`: unique descriptors that went through
   `security.redact` and then `scrub()`. These have no amounts, no dates, no digit runs and no
   person names (where the redactor catches them). They arrive most important first (by total
   spend, then frequency); the order is all the hook learns about amounts.
4. Hands the plugin only:
   - **`descriptors`:** that list of strings;
   - **`categories`:** a list of `{name, lane, schedule_c}`;
   - **`settings`:** the plugin's own registered keys plus `business_profile`. It never includes
     `business_name` (often a client's name), `license_key` or other core settings.
   - **`plugin_store`:** the plugin's namespaced store.

   The plugin **never** receives a `Transaction`, an amount, a date, an account id or a raw
   description.
5. Takes the plugin's return value: `{descriptor: {category, lane, schedule_c, confidence,
   model, new_category?}}`, or `plugins.AIResponse(results, warnings, report)`.
6. Validates it with `_validate`, exactly as it validates the built-in local model:
   - unknown descriptors are dropped;
   - lanes and Schedule C lines are checked;
   - confidence is clamped;
   - new category names are cleaned and capped at 10 per run;
   - near-duplicate names are merged.
7. Applies the validated results to transactions itself. A heuristic guess with higher
   confidence is kept.
8. Stores `model` (truncated to 120 characters) as the transaction's `ai_model`.

**Failure handling:** if the hook raises, the error becomes a warning ("AI add-on failed
(TypeName)"). Exception messages are not echoed, because they could carry data.
Categorization still finishes.

Tests that enforce all this live in `tests/test_plugins.py`:
- `test_plugin_receives_only_redacted_scrubbed_strings`
- `test_plugin_answers_are_validated_by_the_core`
- `test_broken_plugin_is_a_warning`

`GET /api/ai/preview` (core, every edition) returns exactly the `descriptors` the hook would
receive, plus `payload` (redact's `payload_preview`), so users can audit every byte.

## 4. Features, tiers and license plans (`ledgerstitch/editions.py`)

- **Community features** (`import`, `reconcile`, `rules`, `review`, `vault`, `reports`,
  `exports`, `ai.local`) are always unlocked. Nothing in the core has **usage caps**.
- **An add-on feature** is unlocked only when the add-on is installed (it declared the feature)
  **and** the license's plan unlocks the feature's tier. A feature whose add-on isn't installed
  doesn't exist for the core: it isn't listed anywhere.
- **Plans unlock a set of tiers.** Tiers are not a linear order. The core itself knows two plans:

  | Plan id | Unlocks tiers |
  |---|---|
  | (none / invalid / expired) | community |
  | `pro` | pro |
  | `enterprise` | every tier, of every add-on |

- **Add-ons declare their own plans** with `license_plans`:

  ```python
  registry.license_plans({"hello_plus": ["hello"]},
                         labels={"hello_plus": "Hello Plus"},
                         aliases={"hello_old": "hello_plus"})
  ```

  Declarations from several add-ons are merged as a union: if two add-ons say plan `x`
  unlocks tiers `a` and `b`, plan `x` unlocks both. `labels` give the display name the UI
  shows for a plan. `aliases` map an old plan id (from keys already issued) to the current one.
- **The functions:**
  - `feature_enabled(name, license, registry=None) -> bool`
  - `features_status(license, registry=None) -> {name: {available, unlocked, tier, label}}`
  - `locked_reason(name, license, registry=None)`: the 402 body, or `None` when usable
  - `plan_tiers()`, `plan_labels()`, `plans_for_tier(tier)`, `normalize_plan(plan)`
- **402 response:** `{"error", "feature", "upgrade": true, "installed": bool}`, for any `gated`
  path an add-on declares while its feature is locked. When the add-on is installed but the
  vault is locked, the route itself answers 409 as usual.
- **Licenses:** `security/license.py` verifies them offline (Ed25519). Issuing keys is a
  seller-side tool and not part of the core.
- **Developer unlock:** `LEDGERSTITCH_DEV_PLAN=<plan id>` works in source runs only, never in a
  frozen app, and is read only from the environment. `current_plan()` then returns a valid
  license with `reason="developer override"`, and `/api/status` reports `dev_override: true`.

**Edition identity:**
- `editions.EDITION` is the build constant: `ledgerstitch/_edition.py`, written by the build
  script, and `"community"` in a source checkout.
- The shells pass `LEDGERSTITCH_EDITION` to `editions.configure()`.
- `editions.current_edition()` returns the explicit edition, otherwise `pro` when any add-on is
  loaded, otherwise `community`.
- `/api/status` returns `edition` and `features`.

## 5. Storage: `workspace.plugin_store(namespace)`

- **Encrypted, namespaced JSON storage** in the workspace vault. Keys are `plugin:<ns>:<key>`,
  where a key matches `[A-Za-z0-9_.:-]{1,120}`.
- **API:** `get_json(key, default=None)`, `put_json(key, value)`, `delete(key)`, `keys()`.
  Writes are immediate, and the workspace lock is held.
- **Migrations:** with `vault_migrations({key: legacy})`, a read falls back to the legacy vault
  key while the new key is missing. The first `put_json` moves the value: it writes the new key
  and deletes the legacy one.
- **Pure calls:** `plugins.MemoryPluginStore(namespace)` is an in-memory store for pure function
  calls and tests. `categorize()` uses one when no `plugin_store` factory is given.

## 6. Routes, static files and the workspace

- **What `api_routes` receives:** `fn(app, ws_getter)`. `ws_getter()` returns the unlocked
  `Workspace`, or raises the core's 409.
- **What routes may use:** `ws.settings`, `ws.categories()`, `ws.plugin_store(ns)`, `ws.lock`
  and the public read APIs (`ws.list_transactions`, `ws.txns_for_year`, …).
- **Trust:** routes run in-process. Unlike the AI hook, they are trusted code under the add-on's
  own license. Anything they send off the machine must go through the same redaction functions
  (`ledgerstitch.ai.categorizer.build_ai_payload`, `ledgerstitch.security.redact`).
- **Errors:** raise `ledgerstitch.api.ApiError(status, message, **extra)` for JSON errors.

**`static_dir(path)`** registers one folder of browser files (JS modules, CSS, images). The core
serves it at `/addons/<namespace>/<file>`:
- with the same access rules as the app's own files (on the desktop, like the core's UI files,
  on 127.0.0.1 only; in server mode, only after sign-in: add-on files are never public), the same
  Content Security Policy and the same cache rules (revalidated on every load, so an upgrade takes
  effect at once);
- only browser file types (`.js`, `.mjs`, `.css`, `.json`, `.svg`, images, fonts, `.txt`);
- only for installed, loaded add-ons; anything else is a 404;
- path-traversal safe: `..`, absolute paths, symlinks out of the folder and hidden files are
  refused.

Keep secrets and business logic in your Python routes. Files under `static_dir` are delivered
to the browser as they are.

## 7. UI registrations

The web UI is a shell (`ledgerstitch/web/static/`) with no knowledge of any add-on. It builds
navigation, routes, cards and settings sections from what add-ons register.

| Call | What it adds |
|---|---|
| `ui_screen(id, title, group, icon, module, feature=None, order=100)` | A screen: a nav entry in `group` and the route `#/<id>`. The shell imports `/addons/<ns>/<module>` and calls `render(main, params, ctx)` |
| `ui_card(slot, id, module, feature=None, order=100, title=None)` | A card in a UI slot. The shell calls `renderCard(el, ctx)` |
| `ui_settings(id, title, module, feature=None, order=100)` | A section of the Settings screen. The shell calls `renderSettings(el, ctx)` |

`module` is a file name inside the add-on's `static_dir`. `feature` (optional) ties the entry to
a declared feature; `order` sorts entries within their place (lower first).

**Card slots:**

| Slot | Where | `ctx.data` |
|---|---|---|
| `overview` | Cards in the Overview grid (the slot element is `display: contents`, so your card is a grid item) | `{year}` |
| `topbar` | The top bar, drawn once per unlock | |
| `ledger-toolbar` | The Ledger screen's toolbar | `{screen, getSelection(), onApplied()}` |
| `export` | Sections in the Export sheet | `{year(), close(), item(spec)}`: `item([format, title, caption, icon, fileName, path?, query?])` returns a ready download row |
| `receipts` | The Receipts screen, above the list | `{added(records)}` to show receipts you just stored |
| `app` | Loaded once after unlock, with no visible place: for global listeners | |
| any other name | A custom slot an add-on screen renders with `ctx.slot(name, el, data)` | what the screen passes |

### 7.1 What the browser gets

`GET /api/status` includes:

```json
"ui": {
  "screens":  [{"ns": "hello", "id": "hello", "title": "Hello", "group": "Tools", "icon": "star",
                "module": "/addons/hello/screen.js", "feature": "hello.greeting", "order": 100,
                "state": "on"}],
  "cards":    [{"ns": "hello", "slot": "overview", "id": "hello-card",
                "module": "/addons/hello/card.js", "feature": null, "order": 100, "state": "on"}],
  "settings": []
}
```

- Only installed add-ons appear. `module` is an absolute URL (`/addons/<ns>/<file>`).
- `state` is `"on"` or `"locked"` (the entry's `feature` is declared but not unlocked).

`GET /addons/index.json` lists the installed add-ons (namespace, mock module if any, features,
plans and UI registrations). The demo mode uses it (section 7.4).

### 7.2 The module contract (JavaScript)

```js
// screen: /addons/<ns>/<module>
export async function render(main, params, ctx) {
  // draw into `main`; `params` are the route's query parameters
  return () => { /* optional cleanup when the user leaves the screen */ };
}

// card (any slot)
export async function renderCard(el, ctx) { /* draw into el */ }

// settings section
export async function renderSettings(el, ctx) { /* draw into el */ }
```

The shell loads a screen with `const m = await import('/addons/<ns>/<module>'); await m.render(main, params, ctx)`.

`ctx` is:

| Field | What it is |
|---|---|
| `api` | The core API client, `api(path, opts)` from `/api.js` (adds the token or session header, maps errors) |
| `store` | The cached app state (status, categories, accounts, settings) |
| `go(route, params)` | Navigate to a route |
| `isCurrent()` | Still the active screen? Check it after an `await` before drawing |
| `addStyle(href)` | Load a stylesheet once (for example `/addons/<ns>/hello.css`) |
| `slot(name, el, data)` | Render every card registered for slot `name` into `el` |
| `ns`, `id` | The add-on's namespace and the registration's id (one module may serve several cards) |
| `data` | Slot data (see the table above), else `null` |
| `setTopActions(...nodes)`, `setTopMeta(...nodes)` | Fill the top bar's action / meta area of a screen |
| `screenContext()` | The current core screen's context for add-ons (e.g. the Ledger's selection), or `null` |

The core also dispatches DOM events on `document` that an `app` module may listen to:
`ls:categorize-start` / `ls:categorize-end` (`detail.btn`), `ls:categorized` (`detail` = the
categorization result; `detail.report` is an AI add-on's run report) and `ls:ai-preview`
(`detail.data`, `detail.add(node)` to add a section to the "What the AI receives" dialog).

Add-on modules may import the core's helpers by absolute path: `'/ui.js'` (DOM helper `h()`,
icons, formatting, toasts, modals), `'/api.js'`, `'/charts.js'`, `'/state.js'`, `'/shell.js'`.
Insert user data as text nodes (`h()`), never with `innerHTML`; the CSP forbids inline scripts.

### 7.3 Locked state

When an entry's `state` is `"locked"`, the shell does **not** load the module. It renders a
neutral locked card with the registered title, a **Get the full product** link (to the website)
and **Have a key?** (opens license entry). The core never describes what the feature does.

### 7.4 Demo mode (`?mock=1`)

The demo runs the whole UI against an in-browser mock API (`web/static/mock.js`). An add-on may
ship a `mock.js` in its `static_dir`:

```js
export function mockRoutes(fixtures) {
  return [
    { method: 'GET', path: '/api/hello', handler: (req) => ({ text: 'Hello, demo' }) },
    { method: 'POST', path: /^\/api\/hello\/(\w+)$/, handler: (req) => ({ ok: true }) },
  ];
}
```

`path` is a string (exact match) or a `RegExp`. Add-on routes are tried before the core's; a
handler returns a `Response`, a JSON-able value, or `undefined` to let the core answer.
`handler(req)` gets `{method, path, url, query, body, form, match, init}`. `fixtures` is the
core's synthetic demo state and helpers (`state`, `json()`, `err()`, `gate(feature, handler)`,
`locked(feature)`, `plan()`, `unlocked(feature)`, `fileResponse()`, …; see `mock.js`).
Optional exports: `mockSettings(fixtures)` (extra demo settings) and `aiProviders` (demo
provider ids). The core demo loads `mock.js` only for add-ons listed in `/addons/index.json`, so
the demo of a plain public checkout shows only free features.

## 8. A toy add-on: "hello"

```
ledgerstitch_hello/
  pyproject.toml
  ledgerstitch_hello/
    __init__.py
    plugin.py
    static/
      card.js
      mock.js
```

```toml
# pyproject.toml
[project]
name = "ledgerstitch-hello"
version = "0.1.0"
dependencies = ["ledgerstitch"]   # pin the core range you tested with (section 11)

[project.entry-points."ledgerstitch.plugins"]
hello = "ledgerstitch_hello.plugin"

[tool.setuptools.package-data]
ledgerstitch_hello = ["static/*"]
```

```python
# ledgerstitch_hello/plugin.py
from pathlib import Path

NAMESPACE = "hello"


def register(registry):
    registry.feature("hello.greeting", tier="hello", label="Greeting card")
    registry.license_plans({"hello": ["hello"]}, labels={"hello": "Hello"})
    registry.settings({"hello_name": "friend"}, editable=["hello_name"])

    def routes(app, ws_getter):
        @app.get("/api/hello")
        def hello():
            ws = ws_getter()
            return {"text": f"Hello, {ws.settings.get('hello_name', 'friend')}"}

    registry.api_routes(routes, gated={"/api/hello": "hello.greeting"})
    registry.provide("hello.greet", lambda name: f"Hello, {name}")
    registry.provide("selftest", lambda: [("hello greeting", lambda: "ok")])
    registry.static_dir(Path(__file__).parent / "static")
    registry.ui_card("overview", "hello-card", "card.js", feature="hello.greeting", title="Hello")
```

```js
// static/card.js
import { h } from '/ui.js';

export async function renderCard(el, ctx) {
  const { text } = await ctx.api('/api/hello');
  el.append(h('p', {}, text));
}
```

Without a license whose plan unlocks tier `hello`, the Overview shows the neutral locked card
titled "Hello", and `/api/hello` answers 402.

## 9. Packaging an add-on

- Its own wheel and its own license file. SPDX headers on every file.
- It depends on `ledgerstitch` with a compatible range (section 10).
- The core must not import it. The test `test_core_never_imports_proprietary_packages`
  enforces this for the known add-on packages.
- Ship browser files as package data inside the wheel, and register their folder with
  `static_dir`.

## 10. Public helpers add-ons may import

| Helper | What it is |
|---|---|
| `ledgerstitch.ai.categorizer` | `CategorizeResult`, `validate_results` (= `_validate`), `category_dicts` (= `_cat_dicts`), `build_ai_payload`, `scrub` |
| `ledgerstitch.ai.errors.AIError` | Base class for user-presentable AI errors (the core turns them into warnings) |
| `ledgerstitch.plugins` | `AIResponse`, `PluginStore`, `MemoryPluginStore`, `registry()` |
| `ledgerstitch.editions` | `feature_enabled`, `features_status`, `locked_reason`, `plan_tiers` |
| `ledgerstitch.taxonomy` | The category model |
| `ledgerstitch.models` | The data model |

## 11. Versioning

This is contract version 2. Version 2 added `license_plans`, `provide`, `static_dir` and the UI
registrations, and removed three single-purpose hooks from version 1. Breaking changes to the
contract are announced in CHANGELOG.md. Add-ons should declare the range of core versions they
were tested with in their `dependencies`.
