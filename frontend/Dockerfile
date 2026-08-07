# syntax=docker/dockerfile:1.7

# ----------------------------------------------------------------------------
# Stage 1 — dependencies (layer stays cached while the lockfile is unchanged)
# ----------------------------------------------------------------------------
FROM node:22-alpine AS deps

WORKDIR /app
COPY package.json package-lock.json ./
# `npm ci` honours the lockfile strictly: a production build must never resolve
# a version different from the one that was tested.
RUN npm ci


# ----------------------------------------------------------------------------
# Stage 2 — Next.js build
# ----------------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Careful: NEXT_PUBLIC_* variables are inlined here, permanently. The backend
# URL and the API key are deliberately not among them — they are read at
# runtime by the server-side relay (src/app/api/[...path]).
ARG NEXT_PUBLIC_MAX_UPLOAD_MB=25
ENV NEXT_PUBLIC_MAX_UPLOAD_MB=$NEXT_PUBLIC_MAX_UPLOAD_MB

RUN npm run build


# ----------------------------------------------------------------------------
# Stage 3 — runtime image
# ----------------------------------------------------------------------------
FROM node:22-alpine AS runtime

WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# `standalone` output: only the dependencies actually traced are copied
# (~40 MB versus ~420 MB for the full node_modules).
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:3000/api/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
