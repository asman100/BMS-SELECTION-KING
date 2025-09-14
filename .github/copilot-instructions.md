<!-- Copied guidance style: concise, actionable rules for AI coding agents working on this repo -->
# Copilot instructions for BMS-selection-

This repository is a small Flask single-file web app (backend + frontend) for managing Building Management System (BMS) equipment templates, point templates, panels and a scheduled equipment list.

Key facts an agent must know (read these files first):
- `app.py` — single Flask app and SQLAlchemy models. This is the canonical source of truth for routes, schema, and data initialization.
- `templates/index.html` — single-page frontend (vanilla JS) that talks to the backend via a small JSON API (`/api/*`). Read it to understand UI flows, expected request/response shapes and validation.
- `bms_tool.db` — SQLite file created/used by the app in the repository root. Database schema is created programmatically in `app.py`.

Big-picture architecture
- Monolithic Flask app: backend and frontend served from the same process. No separate frontend build step.
- Data layer: SQLAlchemy models defined in `app.py`. Key models: `Panel`, `PointTemplate`, `EquipmentTemplate`, `ScheduledEquipment` and two association tables (`template_points`, `selected_points`).
- API surface: `/api/data` (read-all), `/api/panel`, `/api/equipment`, `/api/points`, `/api/equipment_templates` and related CRUD endpoints. Frontend expects JSON shapes matching the `to_dict()` methods in the models.

Important patterns and conventions
- Single-file canonical app: prefer edits in `app.py` for API/DB changes. There is no separate `models.py` or `routes/` folder.
- DB initialization: `setup_database(app)` in `app.py` creates tables and populates initial data when the app runs directly. Avoid duplicating initialization logic elsewhere.
- IDs and keys:
  - Equipment templates use `type_key` (string) as the client-facing identifier (templates are indexed by this key in the frontend). Do not rename `type_key` without updating `templates/index.html` interactions.
  - `PointTemplate.id` and arrays of numeric ids are passed in requests (see `/api/equipment` and equipment templates requests).
- Frontend expectations:
  - `/api/data` returns an object with `panels`, `scheduledEquipment`, `pointTemplates` (map by id), and `equipmentTemplates` (map by type_key).
  - Many client operations assume successful 2xx responses return JSON; non-2xx returns are parsed and surfaced to users. Keep error messages JSON-friendly.

Developer workflows and commands
- Run locally: run `python app.py` from repository root (Windows PowerShell). The app runs a Flask dev server and will create/populate `bms_tool.db` if empty.
- No build step: frontend is pure HTML/CSS/vanilla JS embedded in `templates/index.html`.
- Database resets: remove `bms_tool.db` to force re-initialization on next run (or manually drop tables via SQLAlchemy shell if needed).

Integration points and external dependencies
- Dependencies are minimal and implicit in `app.py`: `flask`, `flask_sqlalchemy`. The repository has no `requirements.txt`; add one when modifying dependencies.
- No external services or CI integrations are present in the repo. Any code that calls external APIs should include clear error handling and timeouts.

How to make safe changes (rules for the agent)
1. When adding or changing models, update the in-repo DB initialization and the frontend `templates/index.html` if the JSON shapes or keys change.
2. Preserve existing route signatures unless you also update `templates/index.html` and ensure `/api/data` still contains the four top-level keys the frontend expects.
3. When adding fields to models, add them to the model's `to_dict()` and to the frontend state mapping in `loadInitialData()` and relevant UI renderers.
4. Avoid non-backwards-compatible changes to `type_key` and `selectedPoints`/`points` shapes; the frontend uses these directly as lookup keys.
5. Use SQLAlchemy session commits consistently; follow the pattern in `app.py` (add, commit, then return JSON). Follow existing 409/400 error responses pattern for conflicts or bad input.

Examples (copy/paste safe snippets from this repo):
- Expected `/api/data` shape (from `app.py`):

  {
    "panels": [ {"id": 1, "panelName": "LP-GF-01", "floor": "Ground Floor"}, ... ],
    "scheduledEquipment": [ {"id": 1, "panelName": "LP-GF-01", "instanceName": "AHU-GF-01", "quantity":1, "type": "ahu", "selectedPoints": [1,3,4] }, ... ],
    "pointTemplates": {"1": {"id":1,"name":"Supply Air Temp","point_type":"AI","part_number":"T-S-10k"}, ...},
    "equipmentTemplates": {"ahu": {"id": 1, "name":"Air Handling Unit", "points": [1,2,3,...]}, ...}
  }

+- Frontend expects `equipmentTemplates` keyed by the template `type_key`. When creating/updating templates use `/api/equipment_templates` and ensure the API returns the created/updated template keyed by its `typeKey`.

Files to inspect when debugging
- `app.py` — primary. Add logging here for server-side troubleshooting.
- `templates/index.html` — client behavior. Open browser console to see frontend errors and network requests.
- `bms_tool.db` — view with a SQLite browser when investigating data issues.

When in doubt, ask the user for clarity about desired behavior before making schema or route-breaking changes.

If you make edits that touch both backend and frontend, include these minimal validation steps before committing:
- Run `python app.py` and verify the Flask server starts (no import errors).
- Open `http://127.0.0.1:5000/` in a browser and exercise the UI flows you changed (add point, create template, add equipment). Errors will show in the browser console and server stdout.

Feedback
- After making changes, leave a short note at the top of this file describing what you changed and why so future agents can follow the rationale.
