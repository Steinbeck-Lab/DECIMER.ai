# The edge: the React app (static files) and the nginx that serves it, proxies /api/ to the job API and
# delivers job media, passes the browsers' error reports to GlitchTip (/glitchtip/api/<project>/
# envelope/) and has a loopback-only listener for GlitchTip's UI (8081). Two stages: Node builds the app, nginx
# serves it.
#
# The build context is the REPOSITORY ROOT:
#   docker build -f deploy/web.Dockerfile -t localhost/decimer-next/edge:local .
#   docker build -f deploy/web.Dockerfile --build-arg VITE_API=mock -t localhost/decimer-next/edge-demo:local .
#
# VITE_API picks the backend client at build time (web/src/api/index.ts). `http` is the real one;
# `mock` gives a working UI on canned data (a demo, never for decimer.ai).
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
# Error reporting of the browser: the DSN of GlitchTip's web project through THIS site's own
# origin (https://<key>@<host>/glitchtip/<project id>; the CSP allows no other), and the release it names. Empty
# DSN: the build carries no error-reporting code (the import is behind this constant, so Vite drops it). A
# DSN is not a secret (the key is sent with every report), but it belongs to one host: rebuild to change it.
ARG VITE_SENTRY_DSN=
ARG VITE_RELEASE=
ENV VITE_SENTRY_DSN=${VITE_SENTRY_DSN} \
    VITE_RELEASE=${VITE_RELEASE}
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
# /var/log/nginx: the error log (nginx.conf), on the edge-logs volume. The official image makes
# access.log and error.log symlinks to /dev/stdout and /dev/stderr there, and a new volume would copy
# them: they go, so that the error log is a real file that edge-logs.sh can rotate and delete (the access
# log is written to stdout by edge.conf itself). The directory is handed over like the cache.
RUN rm -rf /etc/nginx/conf.d/* /usr/share/nginx/html/* \
    && mkdir -p /etc/nginx/snippets /data/jobs \
    && chown 10001:10001 /data/jobs \
    && chmod 755 /data/jobs \
    && chown 101:101 /var/cache/nginx \
    && chmod 700 /var/cache/nginx \
    && rm -f /var/log/nginx/access.log /var/log/nginx/error.log \
    && chown 101:101 /var/log/nginx \
    && chmod 700 /var/log/nginx

COPY --chmod=0644 deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --chmod=0644 deploy/nginx/edge.conf /etc/nginx/conf.d/edge.conf
# The operator's GlitchTip UI: a server of its own on 8081, published on the host's loopback only by compose.
COPY --chmod=0644 deploy/nginx/glitchtip-ui.conf /etc/nginx/conf.d/glitchtip-ui.conf
COPY --chmod=0644 deploy/nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --chmod=0755 deploy/nginx/edge-entrypoint.sh /usr/local/bin/edge-entrypoint.sh
COPY --chmod=0755 deploy/nginx/edge-logs.sh /usr/local/bin/edge-logs.sh
# No --chmod here: it would apply to the directories too (0644: nobody could enter them).
COPY --from=build /app/dist/ /usr/share/nginx/html/

# Fail the build on a configuration nginx rejects. edge-entrypoint.sh writes /tmp/edge-generated.conf at
# start; a stand-in with the same shape lets `nginx -t` run here. nginx -t runs as root and creates its
# temp directories and error.log, which would then belong to root: they are removed again, nginx makes its
# own at start. ($max_upload_mb below is nginx syntax and must stay literal.)
# hadolint ignore=SC2016
RUN printf 'set_real_ip_from 127.0.0.1;\nproxy_read_timeout 60000ms;\nclient_max_body_size 101m;\nset $max_upload_mb 100;\nset $glitchtip_web_project "";\n' > /tmp/edge-generated.conf \
    && nginx -t \
    && rm -rf /tmp/edge-generated.conf /var/cache/nginx/* /var/log/nginx/*

USER 101:101
# As the nginx user: every file the edge serves or reads must be readable, which also needs every
# directory above it to be searchable. A tree that is not (mode bits lost in a COPY, say) fails the build
# here instead of answering 403 or 404 for the whole app.
RUN test -r /usr/share/nginx/html/index.html \
    && find /usr/share/nginx/html /etc/nginx -type f -exec head -c 1 {} + > /dev/null
# 8080 is the site; 8081 is GlitchTip's UI (conf.d/glitchtip-ui.conf), published on the host's loopback only.
EXPOSE 8080 8081
# The edge's own answer; /readyz (through to the API) is for the uptime check, not for this. And the
# error log's retention: `edge-logs.sh enforce` rotates it and deletes what is a week old by itself, then
# checks (nginx/edge-logs.sh), so the seven days are kept every 15 seconds even if the loop beside nginx
# has died; the edge is "unhealthy" in `docker ps` only for what rotating cannot mend.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["sh", "-c", "wget -q -O /dev/null http://127.0.0.1:8080/healthz && /usr/local/bin/edge-logs.sh enforce"]
ENTRYPOINT ["/usr/local/bin/edge-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
