# plan2bim with LibreDWG, so that it reads DWG and DXF. One image: the command line and the web service.
#
#   docker build -t plan2bim .
#   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work plan2bim build /work/PROJECT.toml
#
# The container needs no network for `build`. For drawings from people you do not know, run it with
# `--network none --read-only --tmpfs /tmp --memory 4g`: a DWG reader is a parser of untrusted files.
#
# With no command, the container runs the web service on port 8080. It trusts the identity headers of
# the authentication that stands before it, so do not publish the port without that.
# On your own machine, with no sign-in:
#
#   docker run --rm -p 127.0.0.1:8080:8080 -v plan2bim-data:/data plan2bim serve --host 0.0.0.0 --dev-user "$USER"

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
    XDG_CACHE_HOME=/tmp \
    PLAN2BIM_STORE=/data
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY plan2bim plan2bim
RUN uv sync --frozen --no-dev --no-editable --extra web \
    && mkdir /data && chown 1000:1000 /data
USER 1000:1000
EXPOSE 8080
ENTRYPOINT ["plan2bim"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
