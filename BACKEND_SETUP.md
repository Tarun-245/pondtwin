# PondTwin backend setup

Prepared against GitHub main commit `8a645708bbeb1368bb627f7f7a8975c48c1101e4`.
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
- Configurable ThingSpeak channel, Read API Key and four explicit field mappings.
- Original UTC timestamps, quality checks and duplicate-resistant imports.
- Old sensor-data status; current forecasts reject stale readings.
- Existing physics forecasts and experiments. Transformer availability is explicitly false until its actual files are supplied.

## Supabase setup

1. Use the intended owner's Supabase account. Reuse an existing Free organization if one exists.
2. Create one Free project named `pondtwin`, using standard Postgres in a suitable nearby region.
3. The owner completes any new database-password entry and saves it securely.
4. For a new database, run `supabase/setup.sql` once. The deployed project already has recorded migrations `20261005194648_initial_pondtwin_auth_and_telemetry` and `index_pond_owner_relationships`; do not rerun initial setup there.
5. Verify that all four tables have RLS enabled and `anon` has no table grants.
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

After signup/sign-in, add a pond. Edit pond accepts the Channel ID and four
distinct ThingSpeak field numbers from 1–8. The mapping starts blank because
the actual ESP32/ThingSpeak configuration has not been supplied.
Use a **Read API Key**, rather than a Write API Key, for private channels.

The key is protected by the farmer's ownership policy and is not returned by
PondTwin's API. Blank on edit preserves it; the explicit checkbox clears it.
Ingestion is read-only and sends no actuator commands.

Compare all four parameters and units against ThingSpeak, including the
original observed timestamp. Repeated refreshes must not duplicate entry IDs.
To change a channel or field mapping after history exists, add a separate pond
so distinct sensor series are not mixed.

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

49 local backend and physics tests passed. Live database checks confirmed RLS
and grants on all four tables, isolated two simulated farmer roles, and rejected
forged ownership, ownership reassignment, cross-pond attachments and anonymous
access. All verification rows were rolled back. The security advisor found no
issues; missing foreign-key indexes were added after the performance review.

Real farmer signup and a real ThingSpeak channel still need owner-supplied
credentials/configuration. Email confirmation stays enabled; external farmer
email delivery requires custom SMTP. Transformer inference remains unavailable
until the actual trained model and preprocessing files are supplied.

References:

- [Supabase Auth](https://supabase.com/docs/guides/auth)
- [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase SMTP](https://supabase.com/docs/guides/auth/auth-smtp)
- [ThingSpeak API](https://www.mathworks.com/help/thingspeak/readdata.html)
