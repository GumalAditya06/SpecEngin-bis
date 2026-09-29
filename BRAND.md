# Specengine-BIS brand assets

Source: five SVGs generated in a prior session (`gemini-svg (1–5).svg`), ported into this
project's `public/` and `components/` folders. This file documents what each asset is, the
two defects found and fixed during the port, and how to use them.

## Naming

Written/prose form: **Specengine-BIS**. The lockup renders it as the "Specengine" wordmark plus
a separate "BIS" pill badge — that's two pieces by design, not a typo; don't weld them into one
fused word. (Earlier drafts of this file flagged a casing mismatch — "SpecEngine" vs.
"Specengine" — between the wordmark and the rest of the project's copy. Since the wordmark is
now real `<text>`, not hand-drawn letterforms, that was a one-line fix instead of a redraw;
both now read "Specengine".)

## File inventory

| File | Size / viewBox | Use for |
|---|---|---|
| `public/logo-mark.svg` | 24×24 | App icon, avatar, favicon source, anywhere the mark stands alone at ≥20px |
| `public/logo-mark-16.svg` | 16×16 | Browser chrome, dense UI (nav rail, table rows) — hand-tuned stroke weight for small sizes, not just a scaled-down `logo-mark.svg` |
| `public/logo-lockup.svg` | 220×32 | Primary horizontal lockup — site header, doc header, email signature |
| `public/logo-lockup-stacked.svg` | 160×118 | Vertical lockup — footer, share cards, anywhere a tall/narrow slot beats a wide one |
| `public/favicon.svg` | 32×32 | `<link rel="icon">` — colors are baked in, see below |
| `components/Logo.tsx` | — | React component wrapping all four layouts as inline SVG (see below) |

## Fixes made during the port

The raw generation output had two defects, both corrected in the files above:

1. **`logo-lockup-stacked.svg` had a corrupted path.** The second "e" in "Specengine" closed
   with `...34.2 -9.4 120.8 -9.4 Z` — the `120.8` is a stray value bled in from the *next*
   glyph's own coordinate space (that glyph legitimately uses `120.8` as an x-position). Left
   alone, that "e" would render with a spike shooting out to the far right instead of a closed
   curve. Fixed to `32.8`, matching the same glyph in `logo-lockup.svg`.
2. **`logo-lockup-stacked.svg` was missing its `[BIS]` pill.** The file's own comment says
   "Mark … centered above Wordmark + BIS Pill," but the badge group was never actually added to
   the markup. Added it centered below the wordmark, and grew the viewBox height from 84 to 118
   to fit it with breathing room.
3. **`favicon.svg` used `var(--bg, #0B0F17)` / `var(--text, #F1F5F9)`.** Those fallback hexes
   don't match this project's actual tokens (`--bg: #08090A`, `--text: #F5F6F7` — see
   `UI_STYLE_GUIDE.md` / `app/globals.css`). It also wouldn't have mattered: a favicon is loaded
   as its own standalone document, so it never resolves CSS custom properties from the page that
   links it — the fallback value is *always* what renders. Replaced both with the real tokens,
   hardcoded, so the tab icon actually matches the site instead of a slightly-different near-black.

## Color

The mark and lockups all use `stroke="currentColor"` / `fill="currentColor"` — they take
whatever text color they're placed in. Set color the same way you'd color an icon font:

```tsx
<Logo variant="lockup" className="h-8 text-[#F5F6F7]" />       // on dark surfaces
<Logo variant="mark" className="h-6 w-6 text-[#08090A]" />     // on light surfaces
```

This is why `public/logo-mark.svg` etc. are shipped as loose files *and* duplicated as inline
JSX in `Logo.tsx`: an `<img src="/logo-mark.svg">` is an opaque raster-like resource to the
browser and will **not** pick up `currentColor` from the page. Use the loose SVGs for contexts
that need a static file (email, `<link rel="icon">`, sharing, opening standalone) and the
`Logo` component everywhere inside the app.

The `favicon.svg` is the one exception — its colors are fixed on purpose (see fix #3 above), so
it looks correct as a standalone document regardless of what links to it.

## Using the component

```tsx
import { Logo } from "@/components/Logo";

<Logo variant="mark" className="h-6 w-6" />                 // icon only, 24px source
<Logo variant="mark-16" className="h-4 w-4" />               // icon only, 16px-tuned
<Logo variant="lockup" className="h-8" />                    // default — header lockup
<Logo variant="lockup-stacked" className="h-32" />            // footer lockup
<Logo variant="lockup" title="" />                            // decorative — hides from a11y tree
```

`title` (default `"Specengine-BIS"`) becomes `aria-label` / `role="img"`. Pass `title=""`
when the mark sits next to visible text that already names the product, so screen readers
don't announce it twice.

## Sizing & clear space

- Don't render `logo-mark.svg` below 20px — use `logo-mark-16.svg` instead; its stroke weight
  was hand-tuned for small sizes rather than just scaled down, and it will hold up where a
  naively-scaled 24px mark turns to mush.
- Keep clear space around the mark/lockup equal to the mark's own corner radius (i.e. don't let
  other UI crowd closer than roughly `3px` at 24px scale, scaling proportionally).
- Never stretch non-uniformly. Never rotate. Never recolor the mark and wordmark differently in
  the same lockup — they read as one unit.
- The `[BIS]` pill is a fixed badge, not a slot for other text — don't repurpose it for a
  version number, a beta tag, etc.

## Verification

Open `/brand` in the running app (`app/brand/page.tsx`) to see every variant rendered at
multiple sizes, on both a dark and a light ground (to sanity-check `currentColor`), plus the
raw files from `public/` loaded via `<img>` to confirm they're valid on their own.
