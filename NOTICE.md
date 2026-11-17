# Notice

Elementary Particles
Copyright © 2026 Rob Carroll

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, version 3.
It is distributed WITHOUT ANY WARRANTY; see [LICENSE](LICENSE) for details.

## Dual licensing

The copyright holder may also offer this code under separate commercial terms, for
organizations that cannot meet the AGPL's obligations. To make this possible, contributors
agree to the terms in [CONTRIBUTING.md](CONTRIBUTING.md#contributor-license-agreement).

## Sanitized edition

This repository is generated from a private repository by an export script. Personal names,
domains, addresses and credentials were replaced or removed; host names and private network
ranges were kept as they are. If you find something that looks like a real secret or personal
detail, please report it as described in [SECURITY.md](SECURITY.md).

## Third-party components

Some files bundle third-party code under their own licenses, for example the minified
JavaScript libraries in `integration/music_studio_ui/static/` (ChordSheetJS, wavesurfer.js).
Their licenses apply to those files.

**Not included: ChordSheetJS.** It is licensed GPL-2.0-only, which cannot be combined with this project's
license, so it is not part of this repository. The music studio UI loads it from
`integration/music_studio_ui/static/`; run `scripts/fetch_vendor.sh` once to download the exact release
(checksum-verified) before deploying that part. Do not commit the downloaded file.
