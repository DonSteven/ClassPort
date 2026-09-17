# ClassPort brand assets

This folder contains the ClassPort logo and icon exports. The wordmark spells
**ClassPort** using outlined Inter 700 lettering, so SVG viewers do not need
to download or install a font. The mark combines overlapping course cards with
a bookmark cutout, representing course organization and study progress.

| Color | Hex | Use |
| --- | --- | --- |
| Ink | `#20222c` | Lettering and back card on light surfaces |
| Off-white | `#f4f2ed` | Lettering and back card on dark surfaces |
| Teal | `#147d72` | Front card on light surfaces |
| Mint | `#6de0c5` | Front card on dark surfaces |
| Light canvas | `#f5f7f6` | Filled light cards and app icon background |
| Dark canvas | `#171b25` | Filled dark cards and avatar background |

- `mark/on-{light,dark}.svg`: transparent mark.
- `wordmark/on-{light,dark}.svg`: transparent mark and ClassPort lettering.
- `mark/card-{light,dark}.svg` and
  `wordmark/card-{light,dark}.svg`: artwork with a filled canvas.
- `raster/source.png`: 2048 × 2048 light-background raster master.
- `raster/mark-{64,180,256,512}.png`: transparent mark exports.
- `raster/favicon-{16,32}.png`: small transparent mark exports.
- `raster/org-avatar.png`: 512 × 512 dark-background avatar.

Public copies live beside their source counterparts in `web/public/brand/`.
The app uses `web/src/components/brand/wordmark.tsx` to select light/dark artwork.
Public app icons and the 1200 × 630 share card live in `web/public/` and use the
same artwork. Their dimensions match the HTML and web manifest.

SVGs contain the mark geometry and outlined Inter lettering. PNGs were
rendered from those SVGs in Chromium and downsampled with Lanczos filtering.
The artwork is self-contained and does not require font downloads at runtime.
The favicon SVG follows the browser's light/dark preference.

