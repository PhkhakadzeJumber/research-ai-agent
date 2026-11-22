# The Eureka website (Next.js) as a container: `docker compose up --build` (compose.yaml).
#
# Built in three stages: install the exact packages (package-lock.json), build the site, then run only Next's
# standalone server. BACKEND_URL is where /api/* is forwarded: Next fixes it when building, so it is a build
# argument (compose sets it to the api service).

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
