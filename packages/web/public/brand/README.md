# Brand assets — Open Persona

The logo is the product made visual: the four **typed-memory stores** (identity·teal, self_facts·green, worldview·indigo, episodic·rose) wired into a glowing **vermilion identity core**, inside a depth-shaded **memory sphere** — the same structure as the landing's memory-graph moment. Concept: presence · depth · continuity.

## Vector (SVG — scale freely, ship these)

| File | Use |
|---|---|
| `logo-mark-ondark.svg` | Mark, transparent — over the warm-deep brand canvas (default). |
| `logo-mark-dark.svg` | Mark with opaque deep fill — over photos / arbitrary dark surfaces. |
| `logo-mark-light.svg` | Mark for light backgrounds (paper). |
| `logo-lockup-horizontal-dark.svg` / `…-light.svg` | Mark + Fraunces wordmark, side by side. |
| `logo-lockup-stacked-dark.svg` / `…-light.svg` | Mark above wordmark — square-ish placements. |
| `logo-mono-ink.svg` | Single-colour ink — print, stamps, one-colour contexts on light. |
| `logo-mono-paper.svg` | Single-colour warm-paper — one-colour contexts on dark. |
| `logo-mono-vermilion.svg` | Single-colour vermilion — accent stamp / merch. |

## Raster (generated from the vector)

| File | Size | Use |
|---|---|---|
| `favicon.ico` | 16·32·48 | Classic favicon (multi-resolution). |
| `favicon-16/32/48.png` | — | PNG favicons. |
| `apple-touch-icon.png` | 180 | iOS home screen. |
| `icon-192.png` / `icon-512.png` | — | PWA / web-app manifest. |
| `og-image.png` | 1200×630 | Open Graph / Twitter social card. |

## Wiring (already done on the landing page)

```html
<link rel="icon" href="favicon.ico" sizes="any" />
<link rel="icon" type="image/svg+xml" href="logo-mark.svg" />
<link rel="apple-touch-icon" href="apple-touch-icon.png" />
<meta property="og:image" content="og-image.png" />
<meta name="twitter:card" content="summary_large_image" />
```

## Clear space & don'ts
- **Clear space:** keep at least the radius of the core (≈ ⅛ of the mark) clear on all sides.
- **Minimum size:** mark 20px; below that use `favicon.ico`.
- **Don't** recolour the core (vermilion is fixed), restyle the store-node hues, add a drop shadow, or set the wordmark in anything but Fraunces 600.
- Regenerate rasters from the SVGs if the mark changes — geometry lives in the SVG builders, not hand-edited PNGs.
