# PathCite frontend

Vue 3 + TypeScript + Tailwind v4 + shadcn-vue. Drag one of nine sample slides (one per tissue class) onto the stage and read the model's prediction.

> Research use only. Not a diagnosis.

## Run

Start the backend first (see `../backend/README.md`), then:

```
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. In dev, Vite proxies `/api/*` to `http://localhost:8000` (prefix stripped), so the backend needs no CORS setup. To point at a hosted backend, set `VITE_API_BASE`.

## Scripts

```
npm run dev         # dev server
npm run build       # type-check and production build
npm run type-check
npm test            # unit tests (API client, class list, bundled samples)
```

## Notes

- Sample slides are in `public/samples/<CLASS>/` (one folder per tissue class, 8 slides each; `samples.json` lists them). The left column shows them as a folder tree. They are JPEG renderings from the NCT-CRC-HE-100K test split (Kather et al., CC-BY-4.0): 224x224 px at 0.5 µm/px, which is why the stage can draw a true 25 µm scale bar for them.
- Each folder holds the first 8 cached images of its class, unfiltered, so some slides are misread (in the model's weakest class, STR, only about 6 of 20 come out right). The folder name is the slide's true label; the reading shows "Matches" or "Differs from label". Treat the folders as a demo, not a measure of accuracy.
- Score ruler: the tick at 0.70 is the backend's default `UNCERTAIN_THRESHOLD`. The `uncertain` flag in the response decides the colour, so a different server setting still behaves correctly; only the tick position would be off.
- `src/lib/classes.ts` must keep the same order as `backend/app/classes.py` (a test checks this).
- shadcn-vue components in `src/components/ui/` were added with CLI v2.4.3: the latest CLI crashes on Node 20.
- Not built yet: Azure storage for slides, auth, batch upload, dark mode.
