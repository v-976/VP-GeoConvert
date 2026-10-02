# Third-Party Notices

This file will list every third-party component distributed with VP GeoConvert and the corresponding copyright and license notices.

## Release gate

Before the first distributable build, and again before each release where dependencies change:

1. Audit direct and transitive dependencies actually included in the distributed binaries.
2. Audit bundled DLLs, codecs, fonts, sample code, generated components, and other redistributed assets.
3. Prefer permissive licenses (MIT, BSD, Apache-2.0 or similarly suitable terms).
4. Treat GPL, LGPL, AGPL, MPL, non-standard, dual-licensed, or unclear dependencies as requiring explicit review before inclusion.
5. Generate/update this notice file from the components actually shipped, not merely from development dependencies.

## PDF engine

PDFium is currently the leading candidate for the PDF vector extraction engine. **No PDF engine or dependency is approved for distribution until the concrete version/build and its complete redistributed dependency set have passed the release license audit.**
