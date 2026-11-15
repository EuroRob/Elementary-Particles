# music-analysis — Musician Platform phase 2 (IaC/docs/MUSICIAN_PLATFORM.md)
#
# Vamp plugins for chord / beat / key / section analysis, built from source:
# Debian (trixie) packages the Vamp SDK and vamp-simple-host but NOT
# nnls-chroma (Chordino), qm-vamp-plugins or Segmentino, and the upstream
# build tool (repoint) pins Mercurial revisions on code.soundsoftware.ac.uk,
# which is offline. So the dependencies are cloned from the c4dm GitHub
# mirrors at pinned commits instead.
#
#   nnls-chroma     GPL-2.0   Chordino (timed chords), NNLS chroma, tuning
#   qm-vamp-plugins GPL-2.0   bar/beat tracker, key detector, tempo
#   segmentino      AGPL-3.0  structural segmentation (A/B/C... sections)
#
# Runtime host: vamp-simple-host (Debian vamp-examples) over a WAV that
# ffmpeg decodes first — so every input format ffmpeg reads (mp3, wma,
# aif, ...) works, not just libsndfile's.

FROM python:3.12-slim AS build
RUN apt-get update -qq && apt-get install -qq -y --no-install-recommends \
        build-essential git ca-certificates vamp-plugin-sdk libboost-dev >/dev/null
WORKDIR /src

# Pins: qm-vamp-plugins' own repoint-lock.json for its deps; HEAD-of-default
# at the time of writing (2026-10-08) for the plugins themselves.
ARG VAMP_SDK_PIN=c3ef877327f1b081b95a1d05349c297f8c4454f0
ARG QM_DSP_PIN=e34a3cc188332ed7c33cd9257ef164de5b587191
RUN git clone -q https://github.com/c4dm/vamp-plugin-sdk.git && git -C vamp-plugin-sdk checkout -q $VAMP_SDK_PIN \
 && git clone -q https://github.com/c4dm/qm-dsp.git          && git -C qm-dsp checkout -q $QM_DSP_PIN \
 && git clone -q https://github.com/c4dm/nnls-chroma.git \
 && git clone -q https://github.com/c4dm/qm-vamp-plugins.git \
 && git clone -q https://github.com/c4dm/segmentino.git

# Chordino / NNLS chroma — against Debian's Vamp SDK (headers + libvamp-sdk).
RUN cd nnls-chroma && make -s -j2 -f Makefile.linux VAMP_SDK_DIR=/usr/include BOOST_ROOT=/usr/include \
        LDFLAGS="-shared -Wl,-soname=nnls-chroma.so -lvamp-sdk -Wl,--version-script=vamp-plugin.map"

# qm-dsp (static) -> qm-vamp-plugins
RUN cd qm-dsp && make -s -j2 -f build/linux/Makefile.linux64 \
 && mkdir -p /src/qm-vamp-plugins/lib && ln -s /src/qm-dsp /src/qm-vamp-plugins/lib/qm-dsp \
 && ln -s /src/vamp-plugin-sdk /src/qm-vamp-plugins/lib/vamp-plugin-sdk \
 && cd /src/qm-vamp-plugins && make -s -j2 -f build/linux/Makefile.linux64

# Segmentino — expects its deps as sibling dirs; skip repoint.
RUN cd segmentino && ln -s /src/vamp-plugin-sdk vamp-plugin-sdk && ln -s /src/qm-dsp qm-dsp \
 && ln -s /src/nnls-chroma nnls-chroma && touch .repoint.point \
 && make -s -j2 -f Makefile.linux

RUN mkdir /out && cp nnls-chroma/nnls-chroma.so nnls-chroma/nnls-chroma.cat nnls-chroma/nnls-chroma.n3 \
        qm-vamp-plugins/qm-vamp-plugins.so qm-vamp-plugins/qm-vamp-plugins.cat qm-vamp-plugins/qm-vamp-plugins.n3 \
        segmentino/segmentino.so segmentino/segmentino.cat segmentino/segmentino.n3 /out/


FROM python:3.12-slim
RUN apt-get update -qq && apt-get install -qq -y --no-install-recommends \
        ffmpeg vamp-examples libvamp-sdk2t64 libvamp-hostsdk3t64 >/dev/null \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --quiet --no-cache-dir asyncpg==0.30.0 httpx==0.27.2 numpy==2.2.6
COPY --from=build /out/ /usr/local/lib/vamp/
ENV VAMP_PATH=/usr/local/lib/vamp
# numpy: studio_drift.py (24c/R24 click-drift analysis, IaC/docs/STUDIO_DRIFT.md), run on demand by
# /usr/local/bin/studio-drift with qm-vamp-plugins' onset detector.
WORKDIR /app
# vamp-simple-host --list-ids lists every plugin output it can load — the build
# fails here if any of the three libraries didn't load.
RUN vamp-simple-host --list-ids | tee /tmp/plugins.txt \
 && grep -q 'nnls-chroma:chordino' /tmp/plugins.txt \
 && grep -q 'qm-vamp-plugins:qm-barbeattracker' /tmp/plugins.txt \
 && grep -q 'qm-vamp-plugins:qm-keydetector' /tmp/plugins.txt \
 && grep -q 'segmentino:segmentino' /tmp/plugins.txt
