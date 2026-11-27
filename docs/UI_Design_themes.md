# EasyQuery UI themes: research and proposal

*Status: research and proposal only (no code changes). Date: 2026-11-26.*
*Update 2026-11-26: §4.1 is now in a working dry run as the appearances **Studio** and **Studio Dark** ([frontend/app/themes/studio.css](../frontend/app/themes/studio.css)). They are additive only: Elegant, Code Dark and Code Light are unchanged, and Elegant stays the default.*
*Audience: whoever decides the next theme work. Read with [UI_Design_note.md](./UI_Design_note.md) §5 (Theming).*

## 1. Summary

The [Vue ecosystem themes page](https://vuejs.org/ecosystem/themes) lists 9 vendors and about 60 templates. Almost all are **Vue component kits and admin dashboards**. EasyQuery is React/Next.js with CopilotKit, so none of them can be dropped in. What we *can* take is their **design language** and, more usefully, their **token architecture**.

Three sources are worth borrowing from:

| Source | What to take | Why it fits EasyQuery |
| --- | --- | --- |
| **Nuxt UI**: Chat and Dashboard templates (MIT) | Calm neutral shell plus **one** accent colour. A small, well-named semantic token set (`bg / muted / elevated / accented`, `text dimmed→highlighted`, `border muted/accented`). Light and dark built in. | Its Chat template is the closest product to ours (AI chat, history sidebar, model picker in the composer). This is exactly the "blue only where it matters" lesson we just learned in Code Dark. |
| **PrimeVue**: Sakai template (MIT), presets Aura / Lara / Nora | A **3-tier token model** (primitive → semantic → component). Themes split into independent axes: **Primary × Surface × Preset × Menu mode**. A small theme popover in the top bar. | It scales to many themes without hand-writing about 60 hex values each time, which is how ours work today. |
| **ThemeSelection**: Materio / Sneat (free versions MIT) | Layout **skins** as their own axis: Default, Bordered, **Semi-dark**, Dark, Horizontal. | Our Elegant theme already *is* "semi-dark + bordered". Naming it that way clarifies what a "dark" version of the brand should be. |

**Recommendation, in order:**

1. **Token architecture v2 (P0).** Introduce primitive colour ramps and a slightly richer semantic layer, keeping today's token names as aliases. Every theme after that is a short mapping instead of a full palette. This removes the ad-hoc tokens Code Dark needed (`--btn-secondary-*`, `--bubble-*`).
2. **Theme = Mode × Accent × Neutral, plus named presets (P1).** Add **System** mode (follow the OS) and a quick theme switcher in the brand bar, in Sakai's style.
3. **New presets (P2), in priority order:**
   - **Elegant Dark**: a brand-true dark mode in warm ink and coral. This is the biggest gap today: choosing a dark theme now loses the brand.
   - **Graphite**: a Nuxt-style neutral zinc dark with a coral accent.
   - **Slate Light**: Sakai-style cool cards, for clients who want a conventional SaaS look.
4. **Don't** adopt a Vue kit, buy an admin template, or move to Tailwind just for theming.

## 2. Where we are today

Details are in [UI_Design_note.md](./UI_Design_note.md) §5 and the theme files in [frontend/app/themes/](../frontend/app/themes/).

- **Brand theme** (`<html data-theme>`, per tenant): brand copy plus about 60 semantic tokens. The only brand is Cotality, in [cotality.css](../frontend/app/themes/cotality.css).
- **Appearance** (`<html data-appearance>`, per browser): Elegant (the brand theme itself), Code Dark and Code Light. Each re-sets *every* token by hand in [appearance.css](../frontend/app/themes/appearance.css).
- **SQL code style** (`<html data-code-theme>`): Match app, Elegant, Code Dark or Code Light ([code.css](../frontend/app/themes/code.css)).
- **What works well:**
  - Components use semantic tokens only.
  - The choice is applied before first paint, so there's no flash of the wrong theme.
  - Preview cards render each option with its real tokens.
  - Every text pair has been checked to WCAG AA.

**Pain points we hit:**

1. **Flat tokens.** Each appearance hand-specifies about 60 hex values. A 4th or 5th theme multiplies the review and contrast-check work.
2. **Missing semantic roles.** We had no "secondary/neutral button" role or "chat bubble" role. So `--primary` drove filled buttons, outline buttons and user bubbles alike, and Code Dark turned the whole UI blue. We patched it with optional tokens, but the roles should be first-class.
3. **Brand and mode are fused.** "Dark" currently means "VS Code", and there's no dark version of the brand. Also, nothing follows the OS setting.
4. **Only a few surface levels.** We have `surface` and `surface-alt` only. Cards, popovers, the workbench and the chat input each need a slightly different elevation, especially in dark mode.

## 3. Survey of the Vue themes page

| Vendor | Notable items | Design language | Licence / price | Fit for EasyQuery |
| --- | --- | --- | --- | --- |
| **Nuxt UI** | Chat, Dashboard (Vue), plus Nuxt templates | Neutral, minimal, one accent; light and dark | MIT, free | ★★★ Closest product shape; best token naming |
| **PrimeVue** | Sakai (free), Apollo, Atlantis, Freya, Ultima, Diamond | Configurable presets (Aura, Lara, Nora, Material) | Sakai MIT; others $59–79 | ★★★ Best theming *architecture* and configurator UX |
| **ThemeSelection** | Materio, Sneat (free and pro) | Material-inspired; skins: Default, Bordered, Semi-dark, Dark | Free versions MIT; pro $69–499 | ★★ Useful skin and layout vocabulary |
| Creative Tim | Vue Black Dashboard, Argon, Material Kit, Now UI | Gradients, glow, heavy shadows | Free and $59–79 | ★ Too decorative for an analyst tool |
| MDBootstrap | Material Bootstrap kits and admin | Material + Bootstrap | Free and $199 | ★ Generic |
| Flatlogic | Sing App, Light Blue | Glassy dark dashboards | Free and $99–149 | ★ Generic, dated |
| WrapPixel | Spike, MaterialPro, AdminPro | Vuetify admin | Free and $49 | ★ Generic |
| CodedThemes | Berry, Mantis, Able Pro, Datta Able | Vuetify / Bootstrap admin | Free and $69 | ★ Generic |
| AdminMart | Matdash, Modernize | Vuetify admin | Free and $49 | ★ Generic |

Most entries are "admin dashboard" kits built for CRUD back-offices: KPI cards, sales charts, e-commerce tables. EasyQuery is a **chat-first analyst tool with an IDE-like workbench**, so their visual density and decoration don't carry over. Nuxt UI and PrimeVue are the two worth studying closely. ThemeSelection adds useful names for layout skins.

## 4. Deep dives

### 4.1 Nuxt UI: Chat and Dashboard templates

From live screenshots of [chat-template.nuxt.dev](https://chat-template.nuxt.dev) and [dashboard-template.nuxt.dev](https://dashboard-template.nuxt.dev), in light and dark:

- **Shell.**
  - Chat: a slightly tinted page (`bg-muted`), a borderless sidebar, and the main area as one rounded, bordered card. The empty state is centred, with a large greeting, a big composer, and prompt chips with source icons.
  - Dashboard: a bordered sidebar with icon + label nav and nested groups, plus a top bar with a page title.
- **Colour discipline.** One accent (green or blue) used only for the active nav item, the primary action, chart strokes and status chips. Everything else is neutral. Dark mode is near-black zinc (`#0f0f10` page, `#18181b` card) with the same single accent.
- **Composer.** The model picker lives *inside* the input (for example "Claude Haiku 5.5 ▾"), together with attach (+) and mic. That's cleaner than our Settings-only model choice.
- **Status chips** (`+19%` in a green tinted pill, `-1%` in red) are a good pattern for our result metadata, such as "scanned 6.1 MB", "cached", or "free preview".
- **Tokens** ([docs](https://ui.nuxt.com/docs/getting-started/theme/css-variables)), where each semantic token maps to a neutral ramp step per mode:

  | Group | Tokens | Light → dark mapping (neutral ramp) |
  | --- | --- | --- |
  | Background | `--ui-bg`, `-muted`, `-elevated`, `-accented`, `-inverted` | white / 50 / 100 / 200 / 900 → 900 / 800 / 800 / 700 / white |
  | Text | `--ui-text-dimmed`, `-muted`, `-toned`, (default), `-highlighted`, `-inverted` | 400 / 500 / 600 / 700 / 900 → 500 / 400 / 300 / 200 / white |
  | Border | `--ui-border`, `-muted`, `-accented`, `-inverted` | 200 / 200 / 300 / 900 → 800 / 700 / 700 / white |
  | Colour roles | `primary`, `secondary`, `success`, `info`, `warning`, `error`, `neutral` | `primary` uses shade 500 in light and 400 in dark |
  | Other | `--ui-chart-1…6` (chart-1 = primary), `--ui-radius`, focus outline = primary at 25% | |

  The key idea is **choose a neutral ramp (slate, zinc, stone…) plus one primary ramp, and everything is derived.** Switching neutral from `zinc` to `stone` warms the whole app, which is essentially our Elegant versus Code Light difference, expressed with one setting.

### 4.2 PrimeVue: Sakai and the Aura / Lara / Nora presets

From [sakai.primevue.org](https://sakai.primevue.org) and the [styled-mode docs](https://primevue.dev/theming/styled/):

- **3-tier tokens.** *Primitive* (colour ramps such as `blue-50…950`) → *Semantic* (`primary.color`, `surface.*`, `focus.ring`, plus a `colorScheme.light / dark` group) → *Component* (`button.background` and so on, used sparingly). A preset is just a token map, and the "base" CSS never changes.
- **Configurator** (the palette icon in the top bar, opening a small popover):
  - **Primary**: 17 swatches (noir, emerald, green, lime, orange, amber, yellow, teal, cyan, sky, blue, indigo, violet, purple, fuchsia, pink, rose).
  - **Surface**: 8 neutral ramps (slate, gray, zinc, neutral, stone, soho, viva, ocean).
  - **Preset**: Aura, Lara or Nora.
  - **Menu mode**: Static or Overlay.
  - Plus a separate light/dark toggle.
- **Look.** A cool grey-blue page (`#eef2f7`), white cards with generous radius and soft shadows, coloured icon tiles, and a green accent. Friendly, but more "SaaS template" than "data tool".
- **Dark mode** is a class on the document root that you can toggle (`darkModeSelector: '.app-dark'`). This is the same approach as our `data-appearance`.

**Takeaway:** splitting a theme into *independent axes* (accent × neutral × mode) gives users and tenants a lot of choice with very little new CSS, and contrast checks run per axis rather than per combination.

### 4.3 ThemeSelection: Materio and Sneat skins

The [Materio product page](https://themeselection.com/item/materio-vuetify-vuejs-admin-template/) offers six layouts: Vertical, **Bordered** (flat with hairlines, no shadows), **Semi-dark** (dark menu, light content), Dark, Horizontal, and Horizontal Dark.

**Takeaway:** EasyQuery's Elegant is *Semi-dark + Bordered + square corners*. Its natural dark sibling is "Dark + Bordered" in the **same brand colours**, not a switch to VS Code. That's the **Elegant Dark** preset proposed below.

### 4.4 The rest

Creative Tim, MDB, Flatlogic, WrapPixel, CodedThemes and AdminMart are generic Material or Bootstrap admin kits with gradients, glow and heavy shadows. Nothing in them improves on what we have, and their decoration would compete with data tables and SQL. Skip them.

### 4.5 React-side equivalents (outside the Vue list, for reference)

If we later want ready-made primitives in our own stack, two libraries follow the same semantic-token idea:

- **shadcn/ui** themes (MIT): CSS variables `--background`, `--foreground`, `--muted`, `--accent`, `--border`, `--ring` and `--chart-1…5`, plus a choice of base neutral (neutral, stone, zinc, gray, slate).
- **Radix Colors** (MIT): 12-step scales with defined uses (1–2 app backgrounds, 3–5 component backgrounds, 6–8 borders, 9–10 solid fills, 11–12 text) and matching dark scales. This is a strong source for our primitive tier.

## 5. Proposal

### 5.1 Token architecture v2 (P0, no visual change)

Restructure the theme CSS into three tiers. Components keep using today's names, which become aliases, so the migration is invisible.

```text
primitives   --ink-50…950  --coral-50…950  --amber-…  --zinc-…  --slate-…  --stone-…  (brand + neutral ramps)
semantic     --bg --bg-muted --bg-elevated --bg-accented --bg-inverted
             --text --text-muted --text-dimmed --text-highlighted --text-inverted
             --border --border-muted --border-accented
             --primary --primary-fg  --secondary --secondary-fg --secondary-border   (filled vs neutral actions)
             --accent (rules, active markers)  --focus  --selected
             --info --success --warning --danger (+ -bg)  --chart-1…8  --radius  --font --font-mono
             --code-* (unchanged)
component    --bubble-user-bg/fg  --chip-*  (only where a role differs from the semantic default)
aliases      --surface → --bg, --surface-alt → --bg-muted, --fg → --text, --fg-muted → --text-muted, …
```

**Rules:**
- A preset sets **semantic tokens from primitives only** (no raw hex outside the primitive tier).
- Each semantic pair carries a contrast target that is checked automatically (see §5.5).

### 5.2 Theme axes and the switcher (P1)

| Axis | Options | Stored | Who chooses |
| --- | --- | --- | --- |
| **Mode** | Light, Dark, **System** (new: follows `prefers-color-scheme`) | per browser | user |
| **Preset** | Elegant, Elegant Dark, Code Dark, Code Light, Graphite, Slate Light | per browser (default per tenant) | user; admin sets the tenant default |
| **Accent** (optional, later) | Brand (coral), Blue, Teal, Violet, Emerald | per tenant | admin |
| **Density** (optional, later) | Comfortable, Compact (tighter tables and editor) | per browser | user |

- In the UI, presets are what people pick, as today. Mode = System picks the light or dark sibling of the chosen family:
  - Elegant ↔ Elegant Dark
  - Code Light ↔ Code Dark
  - Slate Light ↔ Graphite
- **Quick switcher**: a small palette/sun icon in the brand bar, Sakai-style, opening a popover with Mode, preset swatches and code style. Settings → Appearance stays as the full page with previews.
- The SQL code style keeps "Match app", which follows the preset's code palette.

### 5.3 New presets

All text pairs below were checked against WCAG AA (body text ≥ 4.5:1). Values are a starting point to refine on real screens.

**A. Elegant Dark** (brand-true dark mode, highest priority). Same identity as Elegant (ink, coral, amber, square corners, bordered), inverted.

| Role | Value | Contrast |
| --- | --- | --- |
| Page / panel | `#120d06` (deep ink) | text 16.1:1 |
| Surface (chat, workbench) | `#1c150c` | text `#f3e9da` 15.0:1; muted `#b8a88f` 7.8:1 |
| Surface alt (cards, input) | `#251c11` | muted 7.2:1 |
| Border | `#3a2d1d` (hairline) | n/a |
| Primary action | coral `#f2735e` fill with ink `#1e1405` text | 6.4:1 |
| Accent / focus | coral `#f2735e` on surface; amber `#f4a259` for secondary marks | 6.4:1 / 8.7:1 |
| Code (warm dark) | bg `#18120a`; keyword `#f2836f`, string `#a9c27a`, number `#f0b36b`, comment `#9c8b75`, identifier `#7fc4c9` | 7.3 / 9.5 / 10.1 / 5.6 / 9.4 |

*Design intent:* "Elegant at night". Coral is used sparingly (primary action, active tab, focus). Secondary buttons and the user's chat bubbles are warm grey, not coral, which is the same lesson as Code Dark.

**B. Graphite** (Nuxt-style neutral dark with the brand accent):
- **Neutrals:** zinc. Page `#0f0f10`, surface `#18181b`, surface alt `#27272a`, border `#27272a` / `#3f3f46`.
- **Text:** `#e4e4e7` (14.0:1), muted `#a1a1aa` (6.9:1 on surface, 5.8:1 on alt).
- **Accent:** coral `#f2735e` with ink text (6.2:1) for primary action and focus only. Charts start with coral.
- **Corners:** 6px radius, rounded main card in the Nuxt Chat style.
- *Who it's for:* people who want a modern, neutral dark UI that is still recognisably EasyQuery (coral) rather than VS Code (blue).

**C. Slate Light** (Sakai-style cool light):
- **Surfaces:** page `#eef2f7`, cards white with a soft shadow, 10px radius.
- **Text:** `#1e293b` (14.6:1), muted `#556274` (5.5:1 on page, 6.2:1 on cards).
- **Accent:** emerald `#047857` (white text 5.5:1), or indigo `#4f46e5` (6.3:1) as a tenant option.
- *Who it's for:* client tenants that want a conventional SaaS look, and a good demo of the "Accent" axis.

**Keep as they are:** Elegant (default), Code Dark (just tuned), Code Light. Optionally apply Code Dark's "blue only for primary and focus" rule to Code Light too.

### 5.4 Non-colour ideas borrowed from the templates (backlog)

| Idea | Source | Value for EasyQuery |
| --- | --- | --- |
| Model picker inside the chat composer | Nuxt Chat | Switch the Copilot or Gemini model per question without opening Settings |
| Centred empty state: greeting, big composer, prompt chips with source logos | Nuxt Chat | Better first-run experience; chips can show the database logo of the connection they query |
| Status chips for numbers and metadata | Nuxt Dashboard | "scanned 6.1 MB", "free preview", "cached", "+12% vs last run" next to results |
| Theme popover in the top bar | Sakai | One-click theme change; also surfaces the feature |
| Collapsible icon rail for the side panel ("Overlay" menu mode) | Sakai, Nuxt Dashboard | Better than Hide/Show on medium screens: tabs stay reachable as icons |
| Nested nav with a vertical guide line | Nuxt Dashboard | Explorer tree (dataset → table) could adopt the same visual language |

### 5.5 Delivery plan (when we decide to build)

| Phase | Scope | Effort | Notes |
| --- | --- | --- | --- |
| **P0** | Token v2: primitive ramps, semantic layer, aliases; port Elegant, Code Dark and Code Light 1:1 (no visual change); add an **automated contrast test** that checks every semantic text pair per preset | M | The guard rail for everything after; closes UI_Design_note backlog items "contrast check per theme" and "token documentation" |
| **P1** | Mode = System; brand-bar quick switcher; tenant-default preset (admin setting, needs an API/DB field) | S–M | Pre-paint script extended to read `prefers-color-scheme` |
| **P2** | Elegant Dark preset; then Graphite | S each | Mostly mappings once P0 is done |
| **P3** | Accent axis (tenant), Density (user), Slate Light | M | Accent changes charts and focus too; re-run the contrast test per accent |

**Risks and watch-outs:**
- **CopilotKit inline styles.** Some of its elements bypass our tokens (we already map `--copilot-kit-*`; keep a visual check for chat bubbles, inline code and the input).
- **Server-rendered images.** The public share image route ([app/s/[id]/image/route.tsx](../frontend/app/s/%5Bid%5D/image/route.tsx)) hard-codes Cotality chart colours, which is fine for link previews but should be documented.
- **Logos.** Database logos assume a white tile; keep `settings-logo` white in dark presets.
- **Combinations.** The number of preset × code style combinations grows quickly. Keep code styles to the existing four and let presets carry their own code palette.

### 5.6 Explicitly not recommended

- **Adopting Nuxt UI, PrimeVue, Vuetify or other Vue kits.** They are Vue-only and would mean a rewrite.
- **Buying admin templates** (Creative Tim, ThemeSelection pro, etc.). They are the wrong product shape and their decorative styles would hurt data density.
- **Moving to Tailwind only for theming.** Our CSS-variable tokens already give the same runtime switching. If we adopt Tailwind later for other reasons, the v2 semantic tokens map one-to-one onto `@theme` variables.

## 6. Sources

- Vue ecosystem themes: <https://vuejs.org/ecosystem/themes>
- Nuxt UI design system: <https://ui.nuxt.com/docs/getting-started/theme/design-system> · CSS variables: <https://ui.nuxt.com/docs/getting-started/theme/css-variables> · Templates: <https://ui.nuxt.com/templates> · Live: <https://chat-template.nuxt.dev>, <https://dashboard-template.nuxt.dev>
- PrimeVue styled mode and presets: <https://primevue.dev/theming/styled/> · Sakai: <https://sakai.primevue.org/>
- ThemeSelection Materio (layout skins): <https://themeselection.com/item/materio-vuetify-vuejs-admin-template/>
- Design Tokens Format Module (W3C community group): <https://www.designtokens.org/tr/drafts/format/>
- Our current system: [UI_Design_note.md](./UI_Design_note.md) §3.3b and §5, [frontend/app/themes/](../frontend/app/themes/)
