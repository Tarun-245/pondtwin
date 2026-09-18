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

On first start the database is created and one example pond is seeded. Use the
**+** button next to the pond name to add your own.

### Run the tests

```bash
pytest -q
```

Thirteen tests covering saturation, mass conservation in the depth profile,
aerator transfer collapsing at saturation, and depth actually changing
behaviour.

### Optional configuration

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
│   ├── db.py          SQLite: ponds, readings, forecasts
│   ├── engine.py      the physics
│   ├── weather.py     Open-Meteo client, cached, hour-aligned
│   ├── telemetry.py   sensor sampling + quality checks
│   ├── schemas.py     request/response models
│   └── main.py        API routes and UI host
├── web/index.html     the 3D interface, single file
├── tests/             physics tests
└── requirements.txt
```

---

## API

```
GET    /healthz                          liveness
GET    /readyz                           readiness (checks the database)
GET    /api/v1/species                   species and their oxygen thresholds

GET    /api/v1/ponds                     list ponds
POST   /api/v1/ponds                     create
GET    /api/v1/ponds/{id}                read
PATCH  /api/v1/ponds/{id}                update
DELETE /api/v1/ponds/{id}                delete

GET    /api/v1/ponds/{id}/state          current state with depth profile
GET    /api/v1/ponds/{id}/history        stored readings
POST   /api/v1/ponds/{id}/forecast       prediction  {horizon_hours, optimise}
POST   /api/v1/experiment                sandbox run, writes nothing
GET    /api/v1/weather                   raw forecast
```

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

- **Telemetry is synthetic.** Readings are generated, not measured. They travel
  the real path — sample, quality-check, persist — so swapping in probes means
  replacing one function. But nothing here is a measurement yet.
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
- **No authentication or multi-tenancy.** Every pond is visible to every
  visitor. Fine on your laptop, not fine on the internet.
- **No alerting.** A forecast nobody sees at 2 AM is worth nothing.

---

## Where the learned model goes

`PhysicsEngine` in `engine.py` has one entry point:

```python
run(cfg, initial, weather, options) -> dict
```

A learned model implements the same signature. The strong version is a residual
correction — let the physics produce the trajectory and have the model predict
its error — because it stays physically plausible and needs far less data than
learning the dynamics outright.

Before that is worth doing, two things need to exist: enough stored readings to
train on, and an evaluation harness with persistence and climatology baselines.
If a model cannot beat persistence at three hours, that is worth knowing early.
