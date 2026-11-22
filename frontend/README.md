# Eureka frontend

The website for Eureka (Next.js, App Router, TypeScript, Tailwind CSS), built from the Figma file "Eureka: website
UI". Design and screens: `../docs/12-frontend-and-ux.md`.

## Run it locally

The backend must be running (from the repo root: `docker compose up -d db`, then `uvicorn src.api.main:app`).

```bash
npm install
npm run dev        # http://localhost:3000; /api/* is forwarded to BACKEND_URL (default http://127.0.0.1:8000)
```

The browser only talks to this site: Next forwards `/api/*` to the backend (`next.config.ts`), so the login cookie is
first-party. Set `BACKEND_URL` in `.env.local` if the API runs elsewhere (see `.env.example`).

## Check your work

```bash
npm run lint && npm run typecheck && npm test && npm run build
```

## After changing the API

The API's TypeScript types are generated from its OpenAPI description, so they can't drift from the backend:

```bash
python -m scripts.openapi     # from the repo root (venv active): writes frontend/openapi.json
npm run api:types             # here: regenerates src/lib/api/schema.d.ts
```

## Layout

- `src/app/(auth)/`: log in, sign up (no login needed).
- `src/app/(app)/`: everything behind the login; its layout checks the session and shows the top nav.
- `src/components/`: the design's components (`ui.tsx`: buttons, pills, cards, fields; `top-nav.tsx`).
- `src/lib/api/`: the API client (`client.ts`), the React Query hooks (`hooks.ts`), the generated types.
- `src/app/globals.css`: the design tokens (colours, radii, fonts, text styles) from the Figma variables.
