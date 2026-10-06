# PondTwin backend setup

Account backend originally prepared against `8a645708bbeb1368bb627f7f7a8975c48c1101e4`.
Channel ID onboarding builds on deployed main `13994ddd0cb0b274e5c83823dbc23bd0c3637dfc`.
Deployment uses the existing Free Render service and the owner's Free Supabase
project `fhhdyhvdejhjeanckblt` (final year Project, Smart Fish Tank organization).
The database, production authentication URLs and Render environment variables
were configured on 2026-10-05. GitHub main triggers Render deployments.

## Implemented

- Email/password farmer signup, sign-in, automatic session refresh and sign-out.
- New-farmer empty state and first-pond onboarding.
- Persistent ponds, readings and forecasts in Supabase Postgres.
- Verified user authentication on private API endpoints.
- RLS ownership checks and composite foreign keys for each pond's records.
- Channel ID onboarding for devices assigned to a farmer; private keys and mappings are supplied once by the provider.
- Automatic recognition of supported ThingSpeak field labels, with optional manual mapping.
- Channel validation before writes and transactional pond/connection saves.
- Partial sensor readings remain visible; missing/invalid readings block forecasts. Raw turbidity voltage is kept separate from NTU.
- Original UTC timestamps, quality checks and duplicate-resistant imports.
- Old sensor-data status; current forecasts reject stale readings.
- Existing physics forecasts and experiments. Transformer availability is explicitly false until its actual files are supplied.

## Supabase setup

1. Use the intended owner's Supabase account. Reuse an existing Free organization if one exists.
2. Create one Free project named `pondtwin`, using standard Postgres in a suitable nearby region.
3. The owner completes any new database-password entry and saves it securely.
4. For a new database, run `supabase/setup.sql`, then `supabase/device_setup.sql` once. The deployed project already has recorded migrations `20261005194648_initial_pondtwin_auth_and_telemetry` , `index_pond_owner_relationships`, `provisioned_devices_and_atomic_pond_save` and `partial_sensor_devices`; do not rerun initial setup there.
5. Verify that all five tables have RLS enabled and `anon` has no table grants.
6. Set Authentication Site URL to the real web origin and allow `https://pondtwin.onrender.com/login` as a redirect. Allow local login URLs only for development.
7. Keep email confirmation enabled. Configure custom SMTP before inviting farmer addresses outside the project team; the default service only sends to team addresses.
8. Copy the project URL and **publishable** key. The application does not need a service-role key or database password.
9. Run the Supabase security and performance advisors.

After setup, verify ownership with two real farmer accounts and one pond each:

- Each farmer's list must contain only their own ponds.
- Farmer A accessing Farmer B's pond, connection, state, history or forecast must get 404.
- Direct Data API access must filter other owners' rows and reject inserts/updates with another owner's ID.
- Connection responses must return only `has_read_api_key`, never the saved key.

## Publish the code

The owner's GitHub connection has write access to `Tarun-245/pondtwin`.
Changes are published to main, which the existing Render service tracks.

From a clean checkout at the base commit:

```bash
git apply --check /path/to/PondTwin_Backend_Changes.patch
git apply /path/to/PondTwin_Backend_Changes.patch
pip install -r requirements.txt
python -m pytest -q
```

Review, commit and push before deployment. Preserve newer teammate changes
rather than overwriting them with this patch.

## Existing Render service

Reuse the existing `pondtwin` service and keep its plan Free.
Add these values in the service's Environment page:

| Variable | Value |
| --- | --- |
| `PONDTWIN_SUPABASE_URL` | Actual Supabase project URL |
| `PONDTWIN_SUPABASE_PUBLISHABLE_KEY` | Actual `sb_publishable_...` key |
| `PONDTWIN_CORS_ORIGINS` | `["https://pondtwin.onrender.com"]` |
| `PONDTWIN_TELEMETRY_INTERVAL_SECONDS` | `60` |
| `PONDTWIN_TELEMETRY_STALE_SECONDS` | `600` |
| `PONDTWIN_THINGSPEAK_HISTORY_RESULTS` | `2000` |

Build: `pip install -r requirements.txt`.
Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
Health check: `/healthz`.

On an existing Blueprint, `sync: false` does not prompt again on updates.
Set the two Supabase variables explicitly, then deploy the reviewed code.
Do not attach a paid disk or create Render Postgres.

## Real pond connection

After signup/sign-in, farmers enter the Channel ID supplied with their device.
The MCU sends readings to ThingSpeak using its **Write API Key**. The provider
registers the channel's **Read API Key** and sensor mapping in
`public.provisioned_devices`, assigned to the intended farmer's Auth user ID.
Only that farmer can read the device configuration through RLS; farmers cannot
insert, edit or claim entries. A Channel ID alone cannot claim another farmer's
private device. PondTwin's API never returns the read key.

Register a device only after the owner confirms the exact channel and farmer.
Use the Supabase SQL editor/management connector, parameterized values, and the
farmer's existing account. Do not put keys, account passwords, or actual device
rows in git. No service-role key is needed by the app.

Readable unassigned channels can also connect: recognized metadata labels supply
the mapping. Unknown/ambiguous labels require Advanced sensor setup. Manual setup
accepts distinct field numbers 1–8 for the installed sensors. Blank Read API Key
on edit preserves the same channel's saved key; the explicit checkbox clears it.
A different channel never inherits the previous channel's key.

Before any save, PondTwin verifies the channel through the read-only feeds API.
The pond and connection are written together with a SECURITY INVOKER transaction,
using the farmer's JWT and RLS. Failed connections/edits leave both unchanged.
The form displays errors inline. Leaving Channel ID blank saves the pond for
later connection. Ingestion sends no actuator commands.

On 2026-10-06 the owner's ThingSpeak account showed these private channels:

| Channel | Sensor mapping | Limits |
| --- | --- | --- |
| 2966968 — Fish Farming Application | temperature 1, turbidity NTU 2, pH 3 | No dissolved oxygen field |
| 3148055 — Fish Farming 2 | temperature 1, turbidity NTU 2, pH 3 | No dissolved oxygen field |
| 3449300 — Simulation Driven Aquaculture | temperature 1, pH 2, turbidity voltage 3 | No DO or calibrated NTU field |

No device assignment has been saved yet: automatic review requires explicit
approval of the private channel and recipient. Once assigned, a farmer needs
only its Channel ID. The latest visible update for channel 3449300 was August
2026, so the MCU must resume uploads for current data. Voltage needs a real
calibration before it can be converted to NTU. Missing values remain null and
oxygen risk/depth profiles are unavailable until valid readings exist.

Compare values, units and original UTC timestamps with ThingSpeak. Repeated
refreshes must not duplicate entry IDs. To change a channel or field mapping
once history exists, add a separate pond; the transaction also enforces this.

Imports run on demand. ThingSpeak retains ESP32 readings while Render sleeps,
but only the configured import window is recovered per sync (default 2,000,
maximum 8,000). No always-running ingestion worker is included.
Readings and forecasts accumulate; monitor Supabase storage as the project
grows. This setup does not automatically purge history.

## Transformer files still needed

Supply the trained checkpoint, model architecture/config, saved scaler,
feature order, history-window length, trained sampling interval and output
horizon. These are absent from the current repository and ZIP.
Until supplied, forecasts use the existing physics engine. No PPO or live
actuator-control integration has been added.

## Verification status

Local backend integration and physics tests use mocked upstream APIs.
Dashboard/authentication scripts passed JavaScript syntax checks.
The vendored Supabase SDK is pinned to 2.57.4, with a SHA-256 manifest and license.

65 local backend, device and physics tests passed. Live database checks confirmed RLS
and grants on all five tables, isolated two simulated farmer roles, and rejected
forged ownership, ownership reassignment, cross-pond attachments and anonymous
access. All verification rows were rolled back. Atomic create/edit rollback and sensor-history protection were verified in the
live database. Missing foreign-key indexes were added. The security advisor
reported the existing leaked-password-protection setting; no RLS or function
security warnings were found. Performance notices are unused indexes in the
new database.

A real farmer has signed up and the real ThingSpeak field configurations were
inspected. Private device assignment and a complete live round trip remain
pending explicit channel/recipient approval. Email confirmation stays enabled; external farmer
email delivery requires custom SMTP. Transformer inference remains unavailable
until the actual trained model and preprocessing files are supplied.

References:

- [Supabase Auth](https://supabase.com/docs/guides/auth)
- [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase SMTP](https://supabase.com/docs/guides/auth/auth-smtp)
- [ThingSpeak API](https://www.mathworks.com/help/thingspeak/readdata.html)
