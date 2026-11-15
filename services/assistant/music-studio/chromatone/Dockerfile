# Chromatone (github.com/chromatone/chromatone.center, MIT): visual music theory --
# circle of fifths, chord and scale explorers, progressions, MIDI piano roll, tuner.
# Served at theory.home.example.org next to the practice page (2026-10-31). Static site,
# runs in the browser (Web MIDI works from Brave/Chrome over https).
#   - pinned to a commit; bump CHROMATONE_COMMIT to update (the role rebuilds on change)
#   - the visitor-stats script (stats.chromatone.center) is removed at build time
#   - cover images still load from db.chromatone.center (cosmetic only)
ARG CHROMATONE_COMMIT=6ff4256380281ae0c3985e81c4d9ad939f88b90b

FROM node:24-bookworm AS build
ARG CHROMATONE_COMMIT
WORKDIR /src
RUN git init -q . && git remote add origin https://github.com/chromatone/chromatone.center.git \
 && git fetch -q --depth 1 origin "${CHROMATONE_COMMIT}" && git checkout -q FETCH_HEAD
RUN sed -i '/stats\.chromatone\.center/d' content/.vitepress/config.js \
 && ! grep -q 'stats.chromatone.center' content/.vitepress/config.js
RUN corepack enable && CI=1 pnpm install --frozen-lockfile
# gpu-01 has ~3 GB free: the role caps the build container at 2 GB; keep the heap under it.
ENV NODE_OPTIONS=--max-old-space-size=1536
RUN pnpm build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
