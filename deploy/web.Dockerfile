# The edge: the React app (static files) and the nginx that serves it, proxies /api/ to the job API and
# delivers job media. Two stages: Node builds the app, nginx serves it.
#
# The build context is the REPOSITORY ROOT:
#   docker build -f deploy/web.Dockerfile -t localhost/decimer-next/edge:local .
#   docker build -f deploy/web.Dockerfile --build-arg VITE_API=mock -t localhost/decimer-next/edge-demo:local .
#
# VITE_API picks the backend client at build time (web/src/api/index.ts). `http` is the real one, but
# its client is still TODO: until that lands, an `http` build throws at start-up and shows an
# error page; `mock` gives a working UI on canned data (a demo, never for decimer.ai).
#
# The app is built with a relative base, so the same files serve / and /beta/.

ARG NODE_IMAGE=node:24.22.1-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea
ARG NGINX_IMAGE=nginx:1.31.8-alpine3.24@sha256:1ef7ca94d7879df82421428ce13bd1eaa412404953859cf7a5b6512c25e67a60

# ---- build: npm ci + npm run build ------------------------------------------------------------
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY web/package.json web/package-lock.json web/.npmrc ./
# --ignore-scripts: no dependency runs code at install time (nothing in this tree needs it; the build
# fails loudly if that ever changes).
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts
COPY web/ ./
ARG VITE_API=http
ENV VITE_API=${VITE_API}
# After the build, precompress the text assets once, at maximum strength, for gzip_static (nginx's
# official image has no brotli module). -n: no timestamp in the header, so the output is reproducible.
# The .wasm goes from 11.8 MB to 3.85 MB. Then make the tree readable and enterable for everybody
# (a+rX: files readable, directories searchable): nginx runs as uid 101 and the owner is root. The
# final stage copies it WITHOUT --chmod, which would give directories the file mode and lock nginx out.
RUN npm run build \
    && find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' \
        -o -name '*.wasm' -o -name '*.json' -o -name '*.ico' -o -name '*.txt' \) \
        -exec gzip -9 -k -n {} + \
    && chmod -R a+rX dist

# ---- edge: nginx, unprivileged ----------------------------------------------------------------
FROM ${NGINX_IMAGE} AS runtime
LABEL org.opencontainers.image.title="decimer-edge" \
      org.opencontainers.image.description="decimer.ai edge: static app, /api proxy, job media"

# /data/jobs: the job volume's mount point. It is created here with the API's uid, the same as in
# api.Dockerfile, so that the volume ends up writable for the API whichever container mounts it first.
# The edge only reads it (compose mounts it read-only here).
# /var/cache/nginx: where nginx keeps buffered request bodies (nginx.conf). The official image leaves it
# to root; nginx runs as uid 101 here, so it is handed over (a volume mounted there takes this owner).
RUN rm -rf /etc/nginx/conf.d/* /usr/share/nginx/html/* \
    && mkdir -p /etc/nginx/snippets /data/jobs \
    && chown 10001:10001 /data/jobs \
    && chmod 755 /data/jobs \
    && chown 101:101 /var/cache/nginx \
    && chmod 700 /var/cache/nginx

COPY --chmod=0644 deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --chmod=0644 deploy/nginx/edge.conf /etc/nginx/conf.d/edge.conf
COPY --chmod=0644 deploy/nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --chmod=0755 deploy/nginx/edge-entrypoint.sh /usr/local/bin/edge-entrypoint.sh
# No --chmod here: it would apply to the directories too (0644: nobody could enter them).
COPY --from=build /app/dist/ /usr/share/nginx/html/

# Fail the build on a configuration nginx rejects. edge-entrypoint.sh writes /tmp/edge-generated.conf at
# start; a stand-in with the same shape lets `nginx -t` run here. nginx -t runs as root and creates its
# temp directories, which would then belong to root: they are removed again, nginx makes its own at start.
# ($max_upload_mb below is nginx syntax and must stay literal.)
# hadolint ignore=SC2016
RUN printf 'set_real_ip_from 127.0.0.1;\nproxy_read_timeout 60000ms;\nclient_max_body_size 101m;\nset $max_upload_mb 100;\n' > /tmp/edge-generated.conf \
    && nginx -t \
    && rm -rf /tmp/edge-generated.conf /var/cache/nginx/*

USER 101:101
# As the nginx user: every file the edge serves or reads must be readable, which also needs every
# directory above it to be searchable. A tree that is not (mode bits lost in a COPY, say) fails the build
# here instead of answering 403 or 404 for the whole app.
RUN test -r /usr/share/nginx/html/index.html \
    && find /usr/share/nginx/html /etc/nginx -type f -exec head -c 1 {} + > /dev/null
EXPOSE 8080
# The edge's own answer; /readyz (through to the API) is for the uptime check, not for this.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
ENTRYPOINT ["/usr/local/bin/edge-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
