# Deploying IncidentLens v1

This guide covers **where** to host IncidentLens, **how** to deploy it repeatably, and **how to
operate** it (releases, secrets, backups, rollbacks, monitoring). Provider facts were checked on
**2026-10-30**; prices change, so confirm them before you pay.

---

## 1. What we are deploying

| Component | What it is | Notes |
|---|---|---|
| **web** | The Docker image built from `Dockerfile`: FastAPI + the built React UI, one origin | Stateless. Needs ~50 MB RAM idle and ~250 MB once the embedding model is loaded; plan for 512 MB–1 GB |
| **postgres** | PostgreSQL 17 + pgvector 0.8.7 (`pgvector/pgvector:0.8.7-pg17-bookworm`) | The only stateful part; needs a persistent volume and backups |
| **release** | The **same image**, run once per release with `python -m app.db.release` | The **only** component that receives `DATABASE_ADMIN_URL` |

Why a separate release job: the release (create roles, migrate, seed demos, embed runbooks) needs
superuser credentials; the web service must never hold them. The API logs a warning if it finds
`DATABASE_ADMIN_URL` in its environment.

### A hard compatibility constraint
`app.db.bootstrap` creates the two application roles and **requires a true PostgreSQL superuser**
(`rolsuper`). Most managed PostgreSQL services do not provide one:
- Neon: `neon_superuser` has `CREATEROLE` but you "can't connect using the Postgres `superuser`
  account" ([Neon docs: roles](https://neon.com/docs/manage/roles)).
- AWS RDS, Google Cloud SQL, Azure Flexible Server, Supabase and Render Postgres likewise expose a
  privileged-but-not-superuser admin role.

Supporting a `CREATEROLE`-only admin is a planned v2 change. For v1, the database must be **our
own PostgreSQL container** (or we would have to modify `bootstrap` first).

---

## 2. Options compared (checked 2026-10-30)

### Architectures
| | Description | Verdict |
|---|---|---|
| **A** | One Docker service (UI + API) + separate PostgreSQL | **Recommended.** Same origin (no CORS), one build artifact, already smoke-tested |
| B | Separate frontend hosting + API + database | Extra moving parts (CORS, two deploys, API URL config) for no v1 benefit |
| C | One VPS running Docker Compose (app + PostgreSQL + reverse proxy) | Cheapest and full control, but you operate the server (patching, TLS, firewall, backups). Good second choice |

### Hosting providers
| Provider | Runs our Docker image | PostgreSQL 17 + pgvector with **superuser** | Cost for a small MVP | Verdict |
|---|---|---|---|---|
| **Railway** | Yes | Yes: run our own pgvector container on a volume | Hobby $5/mo including $5 usage; RAM $10/GB-mo, CPU $20/vCPU-mo, volume $0.15/GB-mo, egress $0.05/GB ([pricing](https://docs.railway.com/pricing/plans)). Expected **≈ $5–10/mo** | **Recommended** |
| **Hetzner Cloud VPS** | Yes (Docker Compose) | Yes: same container as local | CX23 ≈ €5.49/mo after the 2026 increases; automated backups +20% of server price ([price adjustment](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/), [price history](https://cloudtally.eu/hetzner-cloud-prices)). Expected **≈ €7/mo** | **Second choice** (more operations work) |
| Render | Yes | pgvector yes ([extensions](https://render.com/docs/postgresql-extensions)); superuser not documented → bootstrap would need changes | Starter web ≈ $7/mo with 512 MB (tight), Standard ≈ $25/mo; Postgres Basic from ≈ $6–7/mo (third-party summaries: [1](https://www.budgetforge.dev/tools/render-pricing-2026), [2](https://kuberns.com/blogs/render-postgres-pricing-setup-limits/)) | Viable after a v2 bootstrap change |
| Fly.io | Yes | Managed Postgres from $38/mo ([pricing](https://fly.io/docs/about/pricing/)) | 1 GB machine $6.70/mo + DB ≥ $38 | Too expensive for this MVP |
| Neon (DB only) | n/a | pgvector yes; **no superuser** | Free tier with scale-to-zero; paid usage-based | Not compatible with v1 bootstrap; cold starts |
| Supabase (DB only) | n/a | pgvector yes; `postgres` role not a superuser | Pro from $25/mo ([summary](https://makerkit.dev/blog/saas/supabase-pricing)) | Not compatible with v1 bootstrap |
| AWS / Azure / Google Cloud | Yes (App Runner/ECS, Container Apps, Cloud Run) | PostgreSQL 17 + pgvector available (e.g. [RDS PG17 with pgvector 0.8.0](https://aws.amazon.com//about-aws/whats-new/2024/11/amazon-rds-postgresql-supports-version-17), [Azure pgvector](https://learn.microsoft.com/et-ee/azure/postgresql/flexible-server/how-to-use-pgvector)); **no superuser** | Current official prices **not verified in this review**; always-on managed database plus container runtime (and often a load balancer) is more than the options above | Overkill for v1; good for a later production phase |

Free tiers vs credits: Railway's Free plan gives $1/month of credit and new accounts get a
**one-time** $5 trial ([pricing](https://docs.railway.com/pricing/plans)). That is not enough to keep
the app always on, so budget for Hobby. Do not treat trial credits as permanent hosting.

### Why Railway for v1
- Runs our exact Docker image and our exact pgvector image: **no code changes**, and the same
  configuration we smoke-tested locally.
- Private networking (`SERVICE.railway.internal`) keeps the database off the internet
  ([private networking](https://docs.railway.com/reference/private-networking)).
- Health-checked deploys: a new version goes live only after its health path returns 2xx; default
  timeout 300 s ([healthchecks](https://docs.railway.com/reference/healthchecks)).
- Volume backups with daily/weekly/monthly schedules and in-dashboard restore
  ([backups](https://docs.railway.com/reference/backups)).
- Usage-based billing suits a low-traffic demo (the app is mostly idle).

Trade-offs to accept: PostgreSQL is a container you own (no managed high availability or
point-in-time recovery); Hobby volumes are capped at 5 GB ([volumes](https://docs.railway.com/reference/volumes));
a service with a volume has brief downtime when redeployed (only `postgres`, rarely redeployed).
Note that Railway's `railway.toml` config-as-code is deprecated for new services
([config as code](https://docs.railway.com/reference/config-as-code)), so this guide uses the dashboard.

### Expected monthly cost (Railway, low traffic)
| Item | Estimate |
|---|---|
| Hobby plan (includes $5 usage) | $5 |
| web: ~0.3 GB average RAM, low CPU | ~$3–5 usage |
| postgres: ~0.1–0.2 GB RAM, ≤ 1 GB volume | ~$1–2 usage |
| Backups, egress | cents |
| **Total** | **≈ $5–10/month** (usage beyond the included $5 is billed) |

A more comfortable setup is Railway Pro ($20/mo including $20 usage; larger volumes). Groq usage is
billed separately by Groq; the free tier has strict per-minute token limits.

---

## 3. Deploying on Railway (step by step)

You will need: a Railway account on the Hobby plan, the GitHub repository connected, and your Groq
key. Generate three strong, **different** passwords (letters and digits only, ≥ 16 characters),
for example in PowerShell (copied to the clipboard, never printed):

```powershell
$b = [byte[]]::new(24); [Security.Cryptography.RandomNumberGenerator]::Fill($b); (-join ($b | % { $_.ToString('x2') })) | Set-Clipboard
```

### 3.1 `postgres` service
1. New service → **Docker image** → `pgvector/pgvector:0.8.7-pg17-bookworm`.
2. Variables: `POSTGRES_PASSWORD` = admin password, `POSTGRES_DB` = `incidentlens`,
   `PGDATA` = `/var/lib/postgresql/data/pgdata` (a sub-directory keeps PostgreSQL away from any
   files the platform places at the volume root).
3. Attach a **volume** mounted at `/var/lib/postgresql/data`.
4. Do **not** enable public networking / TCP proxy. The database is reachable only at
   `postgres.railway.internal:5432` from inside the project.
5. Enable **backups** on the volume (daily + weekly) if your plan offers them.

### 3.2 `release` service (one-off job)
1. New service → **GitHub repo** (builds the root `Dockerfile`).
2. Start command: `python -m app.db.release`. Restart policy: **never**. No public domain.
3. Variables (`<...>` are your passwords; hostname is the private name of the postgres service):
   ```
   DATABASE_ADMIN_URL=postgresql://postgres:<admin>@postgres.railway.internal:5432/incidentlens
   DATABASE_URL=postgresql://incidentlens_app:<app>@postgres.railway.internal:5432/incidentlens
   TOOLS_RO_DATABASE_URL=postgresql://tools_ro:<ro>@postgres.railway.internal:5432/incidentlens
   ```
4. Deploy it once: it creates the roles, applies migrations 0001–0006, seeds the four demo
   incidents and embeds the runbooks, then exits. Check its logs for `release complete`.

### 3.3 `web` service
1. New service → the same **GitHub repo** (same `Dockerfile`, default command).
2. Variables (**no** `DATABASE_ADMIN_URL`):
   ```
   DATABASE_URL=postgresql://incidentlens_app:<app>@postgres.railway.internal:5432/incidentlens
   TOOLS_RO_DATABASE_URL=postgresql://tools_ro:<ro>@postgres.railway.internal:5432/incidentlens
   GROQ_API_KEY=<your Groq key>
   MAX_RUNS_PER_HOUR=10            # protects your Groq quota on a public URL
   ```
   `PUBLIC_DEMO_MODE=true` comes from the image: session cookies are `Secure` and `__Host-`
   prefixed, which requires HTTPS (Railway's public domain provides it). Optional account
   settings: `REGISTRATION_ENABLED=false` to stop new sign-ups (existing accounts still work),
   `MAX_INCIDENTS_PER_USER`, `MAX_RUNS_PER_USER_PER_HOUR`, `SESSION_TTL_HOURS`.
   Leave `CORS_ORIGINS` empty: the UI is served from the same origin, and same-origin is what
   the cross-site write protection expects. Serve the app on **one** domain; a request whose
   `Origin` does not match the `Host` it arrived on is refused.
3. Health check path: **`/api/health/ready`** (checks the database). Railway provides `PORT`.
4. Networking → generate a public domain (HTTPS is provided by the platform).
5. Verify: open the domain as a visitor (only the four demo incidents are listed), open
   `https://<domain>/api/health/ready`, run one demo investigation, then create an account,
   create an incident, and confirm in a private window (a second account) that it is not
   visible there.

---

## 4. Operating it

| Task | How |
|---|---|
| **Releasing a new version** | Push to the deployed branch → web rebuilds and goes live only after `/api/health/ready` passes. If the release contains a new migration, redeploy the `release` service **first**, then web. Migrations are additive and forward-only; every step is idempotent |
| **Rollback** | Redeploy the previous successful web deployment from the Railway deployments list. Because migrations only add columns or tables, the previous app version keeps working with the newer schema. Never hand-edit an applied migration (the runner refuses: checksums) |
| **Secrets** | Only as Railway variables, per service. The admin URL exists only on `release`. Rotate app/tools passwords by changing them on `release` + `web` and redeploying `release` (bootstrap re-applies passwords), then `web` |
| **Backups** | Railway volume backups (scheduled). Before risky changes, also take a logical dump with `pg_dump` from a one-off shell in the postgres service and store it outside Railway |
| **Restore** | Railway: Backups tab → Restore (creates a new volume from the backup), then deploy. Logical dump: restore into a fresh database with `pg_restore`, then run the release job to re-apply grants |
| **Logs** | Railway per-service logs. The app logs no request bodies, evidence or secrets, only paths, statuses and exception types |
| **Monitoring** | Railway metrics for CPU/RAM. Add a free external uptime check on `/api/health/ready` |
| **Retention** | Run `python -m app.db.prune --execute` periodically from the `release` service (it holds the admin URL): it deletes user incidents older than `USER_INCIDENT_RETENTION_DAYS` with their runs and evidence, stale cache entries and expired sessions. It never deletes demos or accounts. Tell users that incidents are kept for that many days |
| **Accounts** | There is no admin UI. To remove an account (and, through foreign keys, its sessions and incidents) use the admin connection: `DELETE FROM users WHERE email = '...'` after deleting that account's runs (`DELETE FROM runs WHERE scenario_id IN (SELECT id FROM scenarios WHERE owner_id = ...)`). To sign every user out, `DELETE FROM sessions` |
| **Upgrading to accounts (0006)** | Incidents created before migration 0006 have no owner and are visible to nobody. Prune them or assign them: `UPDATE scenarios SET owner_id = <user id> WHERE kind = 'user' AND owner_id IS NULL` |
| **Failed deploy** | The previous deployment keeps serving until a new one passes its health check. Read the failed deployment's logs; common causes are missing variables (the app fails fast with a validation error naming the variable, never its value) or the release not having run |

---

## 5. Alternative: a single VPS (Hetzner or similar)

Same image and the same release-job split, on one server:
- Docker Compose with three services: `postgres` (pgvector image + named volume, **not** published
  to the internet), `web` (our image), and a reverse proxy such as Caddy for automatic HTTPS.
- Run the release job with `docker compose run --rm` and an env file containing the admin URL
  that the `web` service does not load.
- You are responsible for OS updates, firewall (allow only 80/443 and SSH), off-server backups
  (scheduled `pg_dump` copied elsewhere, plus provider backups), and monitoring.

This is cheaper and teaches more operations, but is more work to keep safe than Railway.

---

## 6. Production configuration checklist

- [ ] Served over HTTPS on a single domain; `PUBLIC_DEMO_MODE=true` (image default) so session
      cookies are `Secure`
- [ ] `CORS_ORIGINS` empty (same-origin UI)
- [ ] Behind a proxy that overwrites `X-Forwarded-For` (Railway does); otherwise remove
      `--forwarded-allow-ips='*'` from the image command so per-IP limits cannot be spoofed
- [ ] Decide `REGISTRATION_ENABLED` (open sign-up vs. invite-only by toggling it)
- [ ] `DATABASE_ADMIN_URL` present **only** in the release job
- [ ] Database has no public endpoint
- [ ] Three distinct strong passwords; Groq key set only on `web`
- [ ] Health check path `/api/health/ready`
- [ ] Backups scheduled and one restore rehearsed
- [ ] `MAX_RUNS_PER_HOUR` set to protect the Groq quota
- [ ] Release job run after every deploy that adds a migration
