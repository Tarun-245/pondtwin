# Pond Twin

A digital twin and simulator for aquaculture ponds. Depth-resolved oxygen
modelling, live weather, multiple ponds per farmer, and a 3D view of the water
column driven by the model output rather than decorating it.

Three modes, in the order a farmer actually thinks:

| Mode | What it answers |
|---|---|
| **Now** | What is the pond doing right now, through the whole column? Read-only. |
| **Forecast** | What will it do over the next N hours, and what aeration would keep it safe? |
| **Experiment** | What if I change something? Runs on a copy of the live state. The real pond is never touched. |

---

## Run it

Requires Python 3.11 or newer.

```bash
# 1. go to the project folder
cd pondtwin

# 2. create and activate a virtual environment
python -m venv .venv

# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell:
.venv\Scripts\Activate.ps1

# 3. install dependencies
pip install -r requirements.txt

# 4. start the server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000** in a browser.

Before opening the site, complete [BACKEND_SETUP.md](BACKEND_SETUP.md).
Farmers sign in first. A new account starts without ponds and is prompted to
add one. Each farmer can only read or change their own ponds.

### Run the tests

```bash
pytest -q
```

Tests cover authentication, owner-scoped API requests, ThingSpeak parsing,
stale and invalid readings, and the existing physics engine. Mocked upstream
tests do not replace live Supabase RLS and deployment verification.

### Configuration

```bash
cp .env.example .env
```

Every setting is prefixed `PONDTWIN_`. Nothing is hard-coded in source.

---

## Layout

```
pondtwin/
├── app/
│   ├── config.py      settings from environment
│   ├── db.py          Supabase persistence using the farmer JWT
│   ├── engine.py      the physics
│   ├── weather.py     Open-Meteo client, cached, hour-aligned
│   ├── telemetry.py   ThingSpeak ingestion + quality checks
│   ├── schemas.py     request/response models
│   └── main.py        API routes and UI host
├── web/index.html     the 3D interface, single file
├── tests/             backend and physics tests
└── requirements.txt
```

---

## API

```
GET    /healthz                          liveness
GET    /readyz                           authenticated database readiness
GET    /api/v1/public-config             public Supabase URL + publishable key
GET    /api/v1/me                        verified farmer
GET    /api/v1/species                   species and their oxygen thresholds

GET    /api/v1/ponds                     list ponds
POST   /api/v1/ponds                     create
GET    /api/v1/ponds/{id}                read
PATCH  /api/v1/ponds/{id}                update
DELETE /api/v1/ponds/{id}                delete

GET    /api/v1/ponds/{id}/connection     channel/field mapping; no key value returned
PUT    /api/v1/ponds/{id}/connection     configure ThingSpeak
GET    /api/v1/ponds/{id}/state          current state with depth profile
GET    /api/v1/ponds/{id}/history        stored readings
POST   /api/v1/ponds/{id}/forecast       prediction  {horizon_hours, optimise}
POST   /api/v1/experiment                sandbox run, writes nothing
GET    /api/v1/weather                   raw forecast
```

All pond, experiment, weather, and readiness endpoints require a Supabase Bearer token.

Interactive docs at http://127.0.0.1:8000/docs

---

## What the model does

Two state variables are integrated forward in fifteen-minute substeps:
bulk water temperature and volume-averaged dissolved oxygen.

**Oxygen**

- photosynthesis, saturating in light, attenuated down the column by turbidity
  (Beer-Lambert)
- plankton and fish respiration, scaled by temperature and stocking density
- sediment oxygen demand as an areal flux divided by depth
- surface reaeration driven by the saturation *deficit*, so it stops at
  saturation instead of pushing past it
- aerator transfer from standard rating, corrected by the alpha / beta / theta
  factors, which collapses as the water approaches saturation

**Temperature** — a real surface energy balance: shortwave in, longwave both
ways, sensible exchange, and latent heat of evaporation. Divided by depth, so a
0.8 m nursery and a 3 m grow-out pond behave differently.

**The depth profile** is a mass-conserving diagnostic. Stratification
redistributes oxygen through the column; it never creates any. The volume
average of the twelve layers equals the bulk value exactly, and there is a test
that proves it.

This matters because it is the difference between a picture and an instrument.
A surface sensor can read 7 mg/L while the bed sits at 3 — which is the failure
mode the 3D view is built to expose.

---

## Known limits

Read these before showing it to anyone who will ask hard questions.

- **ThingSpeak configuration is required.** Channel ID and four field mappings
  must match the ESP32 setup; private channels also need a Read API Key.
  Unconnected ponds never receive fake readings.
- **Free hosting sleeps.** Sensor history stays in ThingSpeak and is imported
  on demand, up to 2,000 readings by default. Long gaps can exceed that window.
- **Farmer email verification needs SMTP.** Supabase's default email service
  only sends to project-team addresses. Configure an email provider before
  inviting other farmers; confirmation remains enabled.
- **The model is uncalibrated.** Every coefficient is a literature-typical
  value, not one fitted to your pond. It is physically shaped, not accurate.
- **Single deterministic run.** No ensemble, so no probability of hypoxia. That
  is the next thing worth building.
- **No prediction-versus-reality loop.** Forecasts are stored but never scored
  against what happened. Closing that loop is what makes it a twin rather than a
  simulator.
- **No ammonia, nitrite or alkalinity chemistry.** pH moves with net production
  buffered by alkalinity, which is a sketch of the carbonate system, not the
  system itself. Unionised ammonia is a real cause of loss and is not modelled.
- **Live verification is pending setup.** Apply the defined RLS schema and
  test with two real farmer accounts before publishing.
- **No alerting.** A forecast nobody sees at 2 AM is worth nothing.

---

## Transformer integration

The trained model is absent from the supplied ZIP and repository. The existing
physics forecasts remain explicitly labelled, and the API reports that the
Transformer is unavailable.

Integration requires the checkpoint, model class/config, saved scaler,
exact feature order, input history length, sampling interval and forecast
horizon used during training. The parameters are temperature, pH, dissolved
oxygen and turbidity. Use original timestamps and the exact training
preprocessing. Do not guess the sampling window or label physics output as a
Transformer prediction.
