# plan2bim with LibreDWG, so that it reads DWG and DXF.
#
#   docker build -t plan2bim .
#   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work plan2bim build /work/PROJECT.toml
#
# The container needs no network. For drawings from people you do not know, run it with
# `--network none --read-only --tmpfs /tmp --memory 4g`: a DWG reader is a parser of untrusted files.

FROM python:3.12-slim-bookworm AS libredwg
ARG LIBREDWG=0.14
ARG LIBREDWG_SHA256=cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates curl pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/libredwg.tar.gz "https://ftp.gnu.org/gnu/libredwg/libredwg-${LIBREDWG}.tar.gz" \
    && echo "${LIBREDWG_SHA256}  /tmp/libredwg.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/libredwg.tar.gz -C /tmp \
    && cd "/tmp/libredwg-${LIBREDWG}" \
    && ./configure --prefix=/opt/libredwg --disable-bindings --disable-static \
    && make -j"$(nproc)" \
    && make install-strip

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv
COPY --from=libredwg /opt/libredwg/bin/dwg2dxf /opt/libredwg/bin/dwg2dxf
COPY --from=libredwg /opt/libredwg/lib /opt/libredwg/lib
ENV PATH=/app/.venv/bin:/opt/libredwg/bin:$PATH \
    LD_LIBRARY_PATH=/opt/libredwg/lib \
    UV_COMPILE_BYTECODE=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY plan2bim plan2bim
RUN uv sync --frozen --no-dev --no-editable
USER 1000:1000
ENTRYPOINT ["plan2bim"]
