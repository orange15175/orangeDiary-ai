# Orange Diary Web

Vue 3 mobile H5 replacement for the existing Uni-app client.

```powershell
npm install
npm run dev
```

Run these commands inside `frontend/`. The development server proxies `/api` to the Python backend on port `8000`. Production requests remain relative `/api` paths and are routed by Nginx. Backend configuration and startup instructions are in `../backend/README.md`.

```powershell
npm run build
```

Upload or deploy the resulting `dist/` files as the Nginx web root.
