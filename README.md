# PORTIA SkillBridge

**Bridging Skills, Building Futures.**
A multi-tenant SaaS platform for skills development, vocational training, competency assessment, recruitment and employment. It is built by Kayron Global Tech Services for Ghana, Africa and international markets.

> **Release status: 1.0 — all 10 stages complete.** This final stage adds in-app and email notifications, two-step sign-in with an authenticator app, encrypted nightly backups with restore, a System status page for go-live, a security review (dependency and code scans, extra headers, log redaction, a locked-down database) and speed work (eager loading, long-lived cached static files). See `docs/GO-LIVE.md` for the launch checklist.

---

## Contents
1. [What is delivered](#what-is-delivered)
2. [Quick start (local)](#quick-start-local)
3. [Environment variables](#environment-variables)
4. [Command-line tools](#command-line-tools)
5. [Architecture](#architecture)
6. [Tenant isolation](#tenant-isolation)
7. [Roles and permissions](#roles-and-permissions)
8. [Learners and files](#learners-and-files)
9. [Training and online learning](#training-and-online-learning)
10. [Assessment and certificates](#assessment-and-certificates)
11. [Jobs and employment](#jobs-and-employment)
12. [Reports](#reports)
13. [Help assistant](#help-assistant)
14. [Subscriptions](#subscriptions)
15. [Payments (Paystack)](#payments-paystack)
16. [Design system](#design-system)
17. [Installable app](#installable-app)
18. [Testing](#testing)
19. [Deploying to Render](#deploying-to-render)
20. [Database backups](#database-backups)
21. [Security](#security)
22. [Roadmap](#roadmap)

---

## What is delivered

| Area | Delivered |
|---|---|
| Application foundation | Flask app factory, configuration per environment, extensions, error pages, JSON API v1 |
| Database | PostgreSQL schema with Alembic/Flask-Migrate migration; constraint naming; timezone-aware UTC dates |
| Workspaces (tenants) | Organizations **and** individuals, 8 organization types, unique workspace codes (`PSB-ORG-XXXXXX` / `PSB-IND-XXXXXX`), suspension and reactivation |
| People | Global user identities and per-workspace memberships with the 9 roles from the brief (enforced from Stage 2) |
| Subscriptions | 4 organization plans + 3 individual plans, trials, monthly/annual billing, renewals, grace period, expiry, cancellation, plan changes, usage limits |
| Tenant isolation | Automatic filtering of every query, writes to other tenants refused, fail-closed when no tenant is chosen |
| Audit trail | Every workspace and subscription change recorded |
| Security | CSRF, strict Content-Security-Policy (no inline scripts), security headers, rate limiting, scrypt password hashing, secure cookies in production |
| Design | Spatial glass UI, deep navy sidebar, teal/blue accents, Lucide icons, light/dark mode, phone layout with slide-in menu and bottom tab bar |
| Sign-in (Stage 2) | Sign in and out, self-service sign-up for organizations and individuals (starts a trial), password reset by email, "keep me signed in", account lockout after 5 failed attempts, sign out of every device |
| Workspaces (Stage 2) | Choose and switch workspace; suspended workspaces and ended subscriptions are blocked (the plan page stays open so the owner can renew) |
| Access control (Stage 2) | 29 permissions, 8 workspace roles + owner + Super Administrator; every page, menu item and API endpoint is checked |
| Pages | Public home and pricing; sign-in, sign-up, password reset, workspace chooser; workspace dashboard, people, subscription, activity, settings, "your access"; my account; Super Admin console (overview, workspaces, workspace detail with suspend/reactivate, plans, audit log) |
| People (Stage 3) | Invite by email (7-day single-use links, stored only as hashes), new people create their account from the link, resend/cancel, change roles, deactivate/reactivate, remove. A workspace always keeps an active administrator; the owner and yourself are protected; plan limits for staff and learners include pending invitations |
| Super Admin (Stage 3) | Create a workspace and invite its owner; record payments and start, renew, extend trials, change plan or cancel; all accounts: unlock, email a reset link, deactivate, grant or remove Super Administrator; edit plan prices, limits and visibility |
| Help (Stage 3) | PORTIA Assistant on every signed-in page, plus a searchable help guide (`app/help/guide.md`) |
| Learners (Stage 4) | Register learners and beneficiaries with automatic numbers (L2026-00001), personal, contact, emergency and M&E details (gender, age, region, disability, education, employment, beneficiary categories); search and filters; passport photos; documents; education, skills and employment history; status and archive; permanent deletion for data-protection requests; CSV export; learner figures on the dashboard |
| Learner self-service (Stage 4) | Invite a learner to sign in from their record; they see their profile, update contact details and upload their own documents |
| Files (Stage 4) | Local or S3-compatible storage, kept separately for each workspace; file types checked by content; photos cleaned of hidden data; per-file and per-plan storage limits; downloads only through permission checks, and every document download is recorded |
| Training (Stage 5) | Programmes (code P2026-001, trade, delivery mode, dates, capacity, funder, status), trainer assignment, enrolment with capacity checks, sessions, phone-friendly attendance registers (Present/Late/Absent/Excused with notes), attendance rates per learner and programme, a Training tab on every learner |
| Online learning (Stage 5) | Courses with modules and lessons: reading, video (YouTube/Vimeo), downloads, quizzes, timed exams, assignments; publishing checks; automatic enrolment from linked programmes; progress and completion; marking with feedback; the learner's **My learning** space |
| Assessment (Stage 6) | Competency frameworks (draft, active, retired) with coded competencies, criteria and a required level; practical, written, oral, portfolio and observation assessments rated on a 4-level scale; evidence uploads; finalising locks the result; each learner's competency profile; skills-gap analysis for a programme or the whole workspace |
| Certificates (Stage 6) | Course, programme and competency certificates with numbers such as `QPK5RF-2026-00001` (end of the workspace code, year, sequence); PDF with a QR code; a public **Verify** page (`/verify`) for employers; revocation with a reason; eligibility checks; competency certificates add the skill to the learner's record |
| Employers (Stage 7) | Employer partners with sector, contacts and location; activate or deactivate; invite an employer's own staff, who then see only that employer's vacancies, shortlisted candidates and placements |
| Vacancies and matching (Stage 7) | Vacancies with numbers, trade, type, positions, region, pay range, closing date and required competencies; Draft, Open, Closed and Filled; a **Find candidates** tab ranking learners by assessed competencies (60%), training in the trade (25%) and region (15%), with the reasons shown; put learners forward in one step |
| Applications and placements (Stage 7) | Learners browse open jobs that match them and apply; pipeline from Applied or Nominated to Shortlisted, Interview, Offer and Hired; interview invitations by email; hiring records a placement and adds it to the learner's employment history; self-employment; follow-up checks at 3, 6 and 12 months with pay and whether the person is still working; placement rate on the dashboard |
| API | `/api/v1/health`, `/api/v1/plans`, `/api/v1/me`, `/api/v1/workspace/members` |
| Reports (Stage 8) | **Reports** page for any period (quick periods: this year, this quarter, last quarter, last year, last 12 months), filtered by programme, gender, region, disability and beneficiary group; results chain (registered → enrolled → completed → competent → certified → placed); completion, attendance, competence, placement and still-in-work rates with the numbers behind each; breakdowns by gender, youth (15–35), disability, age band, region and programme; month-by-month trend; type of work and pay; Chart.js charts with table views, light and dark; PDF report and six-sheet Excel workbook, both logged in the activity log |
| Payments (Stage 9) | Pay with Mobile Money (MTN, Telecel, AirtelTigo) or a card on Paystack's secure page; invoices numbered per workspace (`INV-…-2026-00001`) with PDF invoices and receipts; renewals stack onto the current period; upgrades start now with credit for unused time; downgrades and the free plan wait for the period end (refused if the workspace is too big); saved cards renew automatically with up to three daily retries; reminder emails before trials and unpaid periods end; signed webhook; turn off automatic renewal or remove the card; Super Admin **Payments** console and **Record a payment** for bank transfers and cash; practice checkout for development |
| Notifications (Stage 10) | A bell on every page with an unread count; notifications for applications, shortlisting, interviews, offers, hires, enrolments, marked work, certificates, payments, failed renewals, trial endings, overdue follow-up checks and people joining; each person chooses which categories also arrive by email (**My account → Notifications**); emails are queued and sent every 10 minutes, retried up to three times |
| Security and operations (Stage 10) | Optional two-step sign-in (authenticator app, QR drawn on the server, one-time recovery codes, Super Admin reset for lost phones); encrypted nightly backups with verify/restore commands and 30-day retention; **System status** and **Backups** pages in the console; `flask system check`; extra security headers, `security.txt`, `robots.txt`; secret links hidden from access logs; slow-request logging; dependency audit (Pillow updated); eager loading on list pages; year-long caching for versioned static files; production gunicorn settings; database closed to the internet |
| Tests | 338 automated tests (run on SQLite and PostgreSQL) plus a browser layout checker that signs in as different roles |

## Quick start (local)

Requirements: Python 3.12+ and PostgreSQL 14+. SQLite also works for quick local trials.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                    # then edit DATABASE_URL and SECRET_KEY
createdb skillbridge

export FLASK_APP=wsgi.py                                # Windows PowerShell: $env:FLASK_APP="wsgi.py"
flask db upgrade                                        # create the tables
flask seed-plans                                        # load the subscription catalogue
flask create-superadmin --email you@example.com --name "Your Name"

# Optional sample workspaces
flask create-tenant --name "Accra Technical Training Centre" --plan org-professional --org-type training_centre --city Accra
flask create-tenant --name "Ama Owusu" --kind individual --plan ind-plus --city Takoradi

flask run
```

Open <http://localhost:5000>. Sign in with the Super Administrator account to reach the console at `/admin/`, or use **Free trial** to sign up a new organization or individual. In development, password-reset emails are written to the server log (`MAIL_BACKEND=log`).

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_ENV` | no | `development` | `development`, `testing` or `production` |
| `SECRET_KEY` | **production** | dev placeholder | Signs sessions and CSRF tokens. At least 32 random characters in production. |
| `DATABASE_URL` | **production** | `sqlite:///portia_dev.db` | PostgreSQL URL. `postgres://` URLs are accepted and converted. |
| `TEST_DATABASE_URL` | no | in-memory SQLite | Run the test suite against PostgreSQL |
| `MAIL_BACKEND` | no | `log` | `log` (write to the log), `smtp` (send) or `memory` (tests) |
| `MAIL_FROM` | no | `PORTIA SkillBridge <no-reply@localhost>` | Sender of outgoing email |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS` | with `smtp` | port 587, TLS on | Mail server for password resets |
| `SALES_EMAIL` | no | — | Shows a "Contact sales" button on the Enterprise plan |
| `LOGIN_RATE_LIMIT` | no | `10 per minute;50 per hour` | Sign-in attempts per IP address |
| `SIGNUP_RATE_LIMIT`, `RESET_RATE_LIMIT` | no | `5 per hour` | Sign-ups and password-reset requests per IP address |
| `RATELIMIT_STORAGE_URI` | no | `memory://` | Use `redis://…` when running several servers |
| `RATELIMIT_DEFAULT` | no | `300 per minute` | Default limit per IP address |
| `BILLING_GRACE_DAYS` | no | `7` | Days a lapsed subscription keeps working while payment is due |
| `MAX_UPLOAD_MB` | no | `10` | Largest file a person can upload |
| `STORAGE_BACKEND` | **production** | `local` | `local` (files in `UPLOAD_ROOT`) or `s3` (any S3-compatible service) |
| `UPLOAD_ROOT` | no | `instance/uploads` | Folder for the local backend |
| `STORAGE_BUCKET`, `STORAGE_ENDPOINT_URL`, `STORAGE_REGION`, `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY` | with `s3` | — | Bucket and keys. Leave the endpoint blank for AWS; for Cloudflare R2 use `https://<account>.r2.cloudflarestorage.com` and region `auto` |
| `PAYSTACK_SECRET_KEY` | for online payment | — | Paystack secret key (`sk_test_…` while testing, `sk_live_…` when live). Without it, production switches online payment off and shows a clear message |
| `PAYSTACK_BACKEND` | no | automatic | `live` or `fake` (practice checkout; refused in production). Blank: `live` when a key is set, otherwise `fake` outside production |
| `PAYSTACK_PUBLIC_KEY` | no | — | Not used by the server (checkout happens on Paystack's page); kept for reference |
| `PUBLIC_BASE_URL` | recommended | — | The site's address (e.g. `https://skillbridge.example.com`), used for links in emails sent by scheduled jobs |
| `BACKUP_ENCRYPTION_KEY` | for backups | — | Encrypts backups. Make one with `flask backup make-key` and keep a copy off Render. Production refuses to make unencrypted backups |
| `BACKUP_RETENTION_DAYS` | no | `30` | How long backups are kept (the newest 7 are always kept) |
| `SECURITY_CONTACT` | recommended | `SALES_EMAIL` | Email shown in `/.well-known/security.txt` for security reports |
| `SLOW_REQUEST_MS` | no | `1500` | Pages slower than this are logged as warnings |
| `WEB_CONCURRENCY`, `GUNICORN_THREADS` | no | `2`, `4` | Gunicorn workers and threads (`gunicorn.conf.py`) |
| `LOG_LEVEL` | no | `INFO` | Logging level |

Secrets belong only in environment variables. They never go in the code, the database or release archives.

## Command-line tools

```text
flask seed-plans [--update]              Create missing plans; --update resets them to app/seeds.py (console edits are kept otherwise)
flask create-superadmin --email --name   Create or promote a SaaS Super Administrator (asks for the password)
flask create-tenant --name --plan [--kind organization|individual] [--org-type …] [--cycle monthly|annual]
flask tenants list | suspend CODE --reason "…" | reactivate CODE
flask demo-data [--password …]           Build a sample training centre (learners, training, assessments, jobs). Local/test databases only
flask notifications send                Email queued notifications (cron, every 10 minutes)
flask backup create | list | verify | restore | make-key   Encrypted backups (see Database backups)
flask system check                       Go-live checks; exits with an error if anything is red
flask subscriptions process              Daily job: reminders, renewals, expiry, follow-up alerts, emails
flask db upgrade | migrate -m "…" | check
```

## Architecture

```text
app/
  __init__.py          application factory, template filters, error handlers
  config.py            configuration per environment (all secrets from env vars)
  extensions.py        SQLAlchemy, Migrate, Login, CSRF, Limiter
  tenancy.py           tenant isolation (see below)
  security.py          Content-Security-Policy and security headers
  seeds.py             subscription catalogue
  cli.py               command-line tools
  models/              Tenant, SubscriptionPlan, Subscription, User, Membership, AuditLog
  services/            tenants, subscriptions, usage limits, audit
  blueprints/
    public/            home and pricing pages
    api/               /api/v1/health, /api/v1/plans
    workspace/         /app: dashboard, people, subscription, activity, settings, your access
    account/           /account: profile, password, sign out everywhere
    invitations/       /invite/<token>: accept, or create an account and join
    learners/          /app/learners: records, photos, documents, export; /app/me: the learner's own profile
    programmes/        /app/programmes: programmes, trainers, enrolment, sessions, attendance registers
    courses/           /app/courses: course builder, publishing, course enrolment, marking
    learn/             /app/learn: the learner's courses, lessons, quizzes, exams and assignments
    assess/            /app/competencies, /app/assessments, /app/certificates and the public /verify page
    jobs/              /app/employers, /app/jobs, /app/placements and the learner's /app/my-jobs
    reports/           /app/reports and its PDF and Excel exports
    billing/           /app/billing (checkout, invoices, plan changes), /webhooks/paystack, /dev/paystack (practice)
    admin/             /admin: Super Administrator console
  auth/                sign in/up/out, password reset, workspace chooser; forms.py (validated forms)
  access.py            active workspace per request, permission checks, access gates, safe redirects
  rbac.py              permission catalogue and role → permission map
  navigation.py        menus and tab bar built from the person's permissions
  help/                PORTIA Assistant: guide.md (edit to change answers) and search
  services/members.py  invitations, roles, deactivation and removal, with safeguards
  services/learners.py learner records, numbers, duplicates, photos and documents
  services/training.py programmes, trainers, enrolment, sessions, attendance
  services/lms.py      courses, lessons, quizzes and exams, assignments, progress
  services/assessments.py frameworks, competencies, assessments, ratings, evidence, profiles, gaps
  services/certificates.py eligibility, issue, revoke, numbering, PDF with QR code, verification
  services/employment.py   employers, vacancies, matching, applications, interviews, placements, follow-ups
  services/reports.py      M&E indicators, definitions, filters, disaggregation, trends, chart data
  services/report_exports.py Excel (openpyxl) and PDF (reportlab) versions of the report
  services/paystack.py     Paystack API client (standard library only), practice backend, webhook signatures
  services/billing.py      quotes, invoices, checkout, verification, saved cards, renewals, reminders, daily job
  services/invoices_pdf.py PDF invoices and receipts
  services/notifications.py in-app notifications, who gets them, queued emails
  services/twofactor.py    two-step sign-in (TOTP, RFC 6238) and recovery codes
  services/backups.py      encrypted backups: build, verify, restore, retention
  services/system.py       go-live checks for System status and `flask system check`
  jobs.py                  runs scheduled jobs and records each run (job_runs)
  services/files.py    upload checks, image cleaning, quotas and safe downloads
  storage.py           local and S3-compatible storage
  templates/           layouts (public, auth, shell), pages, partials, Lucide icon sprite
  static/              css/app.css, dark.css, mobile.css (loaded last), print.css; js/theme-init.js, app.js
migrations/            Alembic migrations
tests/                 pytest suite
scripts/               build_icons.py, ui_check.py (browser layout checker)
```

**One database, shared tables, a `tenant_id` column on every tenant-owned table.** This keeps operations simple on Render while isolation is enforced centrally (next section).

**People are global, roles are per workspace.** A `User` is one identity with one email and one password. A `Membership` gives that person a role inside a workspace. The same person can be a learner at a training centre and also hold their own individual subscription.

## Tenant isolation

`app/tenancy.py` enforces isolation for the whole application at the ORM layer, rather than relying on each query to remember a filter:

- **Reads:** inside `tenant_context(id)`, every SELECT, UPDATE and DELETE on tenant-owned data is restricted to that tenant. This covers joins, subqueries, `.count()`, lazy loads and relationships reached through global records such as users.
- **Writes:** creating, changing or moving a record for another tenant raises `TenantIsolationError`. New records automatically get the current tenant.
- **Fail-closed:** querying tenant data with no tenant chosen raises `TenantContextRequired`. Platform-wide work must explicitly enter `platform_scope()`.
- **Per request:** the scope is reset at the start of every request, so nothing leaks between users.

```python
with tenant_context(tenant.id):
    Membership.query.all()           # this workspace only

with platform_scope():               # super administrator, CLI, scheduled jobs
    Tenant.query.count()
```

The suite includes 17 tests covering isolation, including attempts to read, join, count, bulk-update, delete, move and hijack data across tenants.

## Roles and permissions

Pages check **permissions**, never role names (`app/rbac.py`). A person's permissions in a workspace combine every role they hold there, plus owner permissions if they created the workspace.

| Role | Can do (summary) |
|---|---|
| Organization Administrator | Everything in the workspace |
| Training Manager | Learners, programmes, courses, attendance, apprenticeships, certificates, reports |
| Trainer / Instructor | Courses and content, attendance, view learners and assessments |
| Assessor | Assessments, competency frameworks, certificates |
| Employer | Vacancies, applicants and placements. Employer partner accounts (people who hold only this role) see only the employer they are linked to |
| M&E Officer | Read-only across modules, reports and exports, activity log |
| Finance Officer | Plan, billing, payments, financial reports |
| Learner / Beneficiary | Own profile, own learning, apply for jobs |
| Owner (whoever signed the workspace up) | Workspace settings, people list, billing and activity, whatever their roles |
| SaaS Super Administrator | The `/admin` console across all workspaces (held on the account, not in a workspace) |

Each person can see their own list under **Dashboard → Your access**. People on the Independent Pro plan also get the Trainer and Assessor roles in their own workspace.

## Learners and files

**Learner numbers** are given automatically in each workspace as `L<year registered>-<sequence>`, e.g. `L2026-00001`, from a counter that is safe to use from several devices at once.

**Duplicates:** registering someone with the same ID document is refused. The same name and date of birth, or the same phone and date of birth, shows a warning, and the record is saved only if the person confirms.

**Privacy:**
- Only people who manage learner records see full ID numbers, dates of birth, disability details, notes and income. Read-only roles, such as the M&E Officer, see ID numbers masked (••••6789-0).
- CSV exports leave out phone numbers and emails unless the person manages learner records, and are protected against spreadsheet formula tricks.
- The audit log records which fields changed, never the personal values themselves.

**Files** (`app/services/files.py`, `app/storage.py`):
- The file type is decided from the file's contents. Accepted types are PDF, Word `.docx` (macro-enabled files refused), JPG, PNG and, for photos, WEBP.
- Images are redrawn, which removes hidden data such as GPS location. Passport photos are cropped to 3:4 (600×800).
- Files are stored under `tenants/<workspace id>/…` with random names. They are never served straight from storage: every download goes through a permission check, and document downloads are recorded.
- Limits apply per file (`MAX_UPLOAD_MB`) and per workspace (the plan's storage allowance).

**Production storage:** Render's normal disk is wiped on every deploy, so set `STORAGE_BACKEND=s3` and fill in the bucket and keys; `render.yaml` asks for them. Cloudflare R2 has no download fees and a free allowance, which suits most training providers. Keep the bucket **private**: the app serves files itself.

## Training and online learning

**Programmes** get codes such as `P2026-001`. Enrolment respects capacity, and capacity can't be set below the number already enrolled. Archived learners and learners already enrolled are skipped. Completed and cancelled programmes take no new learners.

**Attendance:** people who manage programmes can take any register. Trainers can take registers only for programmes they're assigned to. Only enrolled learners appear on a register. The rate is (present + late) ÷ sessions recorded, and anything below 75% is highlighted.

**Courses:** a course can be published only when it has lessons, every quiz and exam has questions with correct answers, every video has a YouTube or Vimeo link, and every downloads lesson has files. Linking a course to a programme enrols the programme's learners when the course is published. Archived courses don't count towards the plan.

**Lesson text** supports simple formatting (`## heading`, `- list`, `**bold**`, blank line for a new paragraph). Everything is escaped first, so no HTML or script typed into a lesson ever reaches a page. Videos play in the privacy-friendly YouTube (`youtube-nocookie.com`) and Vimeo (`dnt=1`) players. These are the only outside frames the security policy allows.

**Quizzes and exams** are marked on the server: a question scores only when exactly the correct answers are chosen. Attempts can be limited. Exams start a server-side clock when the learner presses Start; the page counts down and submits automatically. Answers arriving more than a minute after the deadline are marked but don't count. Correct answers are hidden until submission, and only shown if the course builder allows it.

**Assignments** accept a written answer and/or a file (PDF, Word, PowerPoint, Excel, JPG or PNG). Learners can change their answer until it's marked.

**Progress** is the share of lessons completed: readings and videos are completed by the learner, quizzes and exams by passing, and assignments by submitting.

## Assessment and certificates

**Frameworks** group competencies (for example `GC-01 Take body measurements`). Each competency has a code, criteria and a required level. Only **active** frameworks can be used for new assessments; retired ones stay readable for past results.

**The scale** has four levels: 1 Not yet competent, 2 Developing, 3 Competent, 4 Proficient. A competency is achieved when its rating meets its required level (Competent by default). An assessment is **competent** only when every competency rated is achieved.

**Assessments** are drafts until finalised. Only the assessor who created a draft, or someone who manages people, can change it. Finalising needs every competency rated and locks the result. Evidence (photos, PDFs, Word files) is stored privately for the workspace.

**Profiles and gaps:** a learner's profile uses their latest finalised rating for each competency. The skills-gap page shows, for each competency, how many learners are achieved, below the required level, or not yet assessed, for a programme or the whole workspace.

**Certificates** are numbered per workspace (`QPK5RF-2026-00001`: the end of the workspace code, the year and a running number) and carry a verification code such as `RC6WM-RHASB`. Course certificates need a completed course, programme certificates a completed enrolment, and competency certificates a competent, finalised assessment. The PDF includes a QR code linking to `/verify/<code>`. The public verify page shows only the learner's name, the award, the issuer and the date, or that the certificate was revoked. It is rate limited (30 checks a minute) and never shows other learner details.

## Jobs and employment

**Employers** are the companies and organisations that hire your learners. An administrator can invite an employer's own staff (they get the Employer role) or link an existing account from the employer's page. Employer staff then see only that employer: its vacancies, the candidates your centre has shortlisted, and its placements. They never see learners you have not shortlisted, and contact details are shared only once someone is shortlisted.

**Vacancies** get numbers such as `J2026-001`. A vacancy can list required competencies from your frameworks. Open vacancies marked *Learners can see this job and apply* appear on learners' **Jobs** page until the closing date.

**Matching** ranks active and graduated learners for a vacancy. Only what the vacancy asks for is counted:

| Part | Weight | How it is checked |
|---|---|---|
| Required competencies | 60% | The learner's latest finalised rating meets each competency's required level |
| Trade | 25% | The learner trained in a programme for that trade, or lists it as a skill |
| Region | 15% | The learner lives in the vacancy's region |

People already in work are hidden unless you ask to include them. Certified learners come first when scores are equal.

**Applications** start as *Applied* (the learner applied) or *Nominated* (your centre put them forward), then move through Shortlisted, Interview, Offer made and Hired, or Not selected or Withdrawn. Scheduling an interview emails the learner. **Hire** records a placement, adds the job to the learner's employment history, updates their employment status and counts towards the vacancy's positions. A vacancy becomes *Filled* when every position is taken.

**Placements** can also be recorded directly, including self-employment. Each placement gets follow-up checks at 3, 6 and 12 months. Recording a check notes whether the person is still working and their monthly pay. A check that finds the person is no longer working ends the placement. Overdue checks appear on the dashboard and the Placements page.

## Reports

**Reports** (`/app/reports`) is for M&E officers, training managers, administrators and finance officers. Choose a period and, optionally, a programme; **More filters** narrows it to a gender, region, disability or beneficiary group. Everything is worked out live from the workspace's own records, so every figure can be checked on the underlying pages.

| Figure | What it counts |
|---|---|
| Registered | Learners whose registration date falls in the period |
| Enrolled in training | Learners who started a programme in the period |
| Completed training | Learners who completed a programme in the period |
| Assessed / Assessed competent | Learners with a finalised assessment in the period / judged competent in at least one |
| Certified | Learners issued a certificate in the period (revoked ones left out) |
| Placed in work | Learners who started a job, internship, apprenticeship or own business in the period |
| Completion rate | Of enrolments that started in the period and have finished, the share completed |
| Attendance rate | Present or late, out of all marks for sessions in the period (excused left out) |
| Competence rate | Of learners assessed in the period, the share judged competent |
| Placement rate | Of learners who completed in the period, the share who started work or a business after enrolling |
| Still in work at follow-up | Of follow-up checks recorded in the period, the share where the person was still working |

Youth means aged 15–35 at the end of the period. Archived learner records are left out. A programme filter also limits training, attendance and assessment figures to that programme. The dashboard's "Graduates placed" figure covers all time; the report's placement rate covers the chosen period.

**Exports** (needs the *Export reports* permission): **PDF report** is a print-ready summary with charts, tables and definitions; **Excel** is a workbook with Summary, By group, By programme, Monthly, Employment and Learners sheets (with native Excel charts). The Learners sheet lists names but no contact details. Cells are protected against formula injection, downloads are never cached, and every export is written to the activity log.

**Charts** use Chart.js 4.6.1, bundled in `app/static/vendor` (no outside script sources, so the strict CSP stays). Data reaches the page as a JSON block, never inline script. Colours follow a fixed, colour-blind-checked order with separate light and dark steps, charts redraw when the theme changes, and every chart has its figures in a table too.

## Help assistant

The round button at the bottom right of every signed-in page opens **PORTIA Assistant**. It answers from `app/help/guide.md`: each `## ` heading is a topic, with optional `keywords`, `permission`, `links` and `suggest` comments. Edit that file to change the answers; no code changes are needed.

- Links are shown only if the person's role can open that page. For topics that belong to another role, it says which role does them.
- Nothing is sent to an outside AI service. Unanswered questions are logged with emails, phone numbers and ID numbers removed, to help improve the guide.
- It requires sign-in, uses a CSRF token, and is limited to 30 questions a minute.
- On phones it opens as a half-screen panel above the round button, keeps the keyboard down until you tap the box, and fills the space above the keyboard while you type. It closes with ×, Escape or the Back button.
- `/help/` shows the whole guide as a searchable page, filtered to the person's role.

## Subscriptions

Prices are stored in pesewas (integer minor units). Annual price = 10 × monthly (two months free).

| Plan | For | Monthly | Yearly | Trial | Learners | Staff |
|---|---|---|---|---|---|---|
| Starter | Organizations | GHS 150 | GHS 1,500 | 14 days | 100 | 5 |
| Professional | Organizations | GHS 350 | GHS 3,500 | 14 days | 500 | 20 |
| Business | Organizations | GHS 750 | GHS 7,500 | 14 days | 2,000 | 60 |
| Enterprise | Organizations | Custom | Custom | 30 days | Unlimited | Unlimited |
| Learner Free | Individuals | Free | Free | — | — | — |
| Learner Plus | Individuals | GHS 35 | GHS 350 | 7 days | — | — |
| Independent Pro | Individuals | GHS 90 | GHS 900 | 14 days | 50 | 1 |

*Individual prices and all usage limits are proposals; edit `app/seeds.py` and run `flask seed-plans`.*

**Lifecycle:** trial → active (after payment or administrator activation) → payment due when the period ends → active again on renewal, or expired after the grace period. Subscribers can cancel at the end of the period or immediately. Administrators can suspend and reactivate. The database guarantees **one live subscription per workspace**. Usage limits are checked with `services.limits.check(tenant, "learners")`.

## Payments (Paystack)

**How a payment works.** On **Subscription**, the person chooses **Pay now** or a plan, checks the amount and enters an email for receipts. The app creates an invoice with a random reference and sends them to Paystack's checkout page. Paystack sends them back to `/app/billing/paystack/return`, where the server **verifies the transaction with Paystack** before anything changes. The amount and currency must match the invoice exactly. Paystack also calls `/webhooks/paystack` (signed with HMAC-SHA512). That covers people who close the browser before returning. Applying the same payment twice changes nothing.

| Change | What is charged | When it starts |
|---|---|---|
| First payment, after a trial or after the plan ended | Full price | When payment is confirmed |
| Same plan again (renewal) | Full price | When the current period ends (paying early loses no days) |
| More expensive plan, or monthly → yearly | Price minus the unused part of the current period (to the nearest cedi) | Straight away |
| Cheaper plan, or the free plan | Nothing now | When the current period ends; refused if the workspace uses more than the new plan allows |
| Enterprise (custom) | Arranged with sales | — |

**Automatic renewals.** When someone pays by card, Paystack returns a reusable authorization. It is stored as a saved card (brand, last four digits, expiry; never the card number) and charged by the daily job when the period ends. A declined charge is retried once a day, up to three times, while the workspace keeps working through the grace period. Each failure is emailed. Mobile Money payments aren't reusable, so those workspaces get a reminder email three days before the period ends instead. People can turn automatic renewal off, or remove the card, under **Subscription**.

**The daily job** (`flask subscriptions process`, the Render cron) runs in this order: reminders, automatic charges, then the usual expiry and payment-due changes.

**Privacy.** Paystack receives only the billing email the person typed, the amount, the invoice reference and the workspace id. Card and Mobile Money details are entered on Paystack's page, never on this site. The security policy allows form submissions only to this site and `https://checkout.paystack.com`.

**Setting it up**
1. In the Paystack dashboard (Settings → API Keys & Webhooks), copy the **test** secret key into `PAYSTACK_SECRET_KEY`.
2. Set the webhook URL to `https://<your-site>/webhooks/paystack`. The callback URL is sent with each payment, so it needs no setting.
3. Make a test payment with one of Paystack's test cards or test Mobile Money numbers, and check that it appears under Subscription → Invoices.
4. Swap in the **live** secret key when you're ready. Mobile Money must be enabled on your Paystack account (Ghana).
5. Set the email settings (`MAIL_BACKEND=smtp`, `SMTP_*`, `MAIL_FROM`) on both the web service and the cron job so receipts and reminders are sent.

**Without Paystack.** In development with no key, a **practice checkout** (`/dev/paystack/<reference>`) stands in for Paystack, so the whole flow can be tried with no money involved. It only works with the single-process development server. Super Administrators can always record bank transfers, cheques and cash under **Workspaces → (workspace) → Record a payment**. That gives the workspace a receipt just like an online payment. **Payments** in the console shows takings and failed renewals.

## Design system

- Spatial glass panels (18–24px radius, soft depth), deep navy sidebar, teal and blue accents, Apple system fonts, Lucide icons served from an inline sprite (no external requests).
- **Light/dark mode** on every page. It follows the device setting until the person chooses, then remembers the choice in both `localStorage` and a cookie. A synchronous `theme-init.js` in `<head>` prevents a flash of the wrong theme. Printouts are always white.
- **Phones:** no sideways scrolling, and wide tables scroll inside their own box. Form fields use 16px text, so iPhones don't zoom in. Safe areas are respected. The ☰ menu slides in from the left over a dimmed page and closes with ×, a tap outside, Escape or the Android Back button. Choosing a page from the menu leaves no extra Back step. A *Find a page* filter ignores accents. The bottom tab bar shows the main destinations plus Menu and hides while you type. A Back button appears only in installed home-screen apps.
- Strict CSP: no inline scripts or styles; all behaviour is in `static/js/`.

To add an icon, put its name in `scripts/build_icons.py`. Then run it against the `lucide-static` package (Lucide icons are ISC-licensed; see `app/static/img/LUCIDE-LICENSE.txt`).

## Installable app

PORTIA SkillBridge is a Progressive Web App, so people can install it on Android, iPhone, iPad, Windows, Mac and ChromeOS. Nothing has to go through an app store.

- **Android, and Chrome or Edge on a computer:** an **Install app** button appears in the menu (and the browser shows its own install option). Installing opens the app full-screen from its own icon, straight to the dashboard.
- **iPhone and iPad:** Safari has no install button for websites, so the menu shows **Install app**, which opens the help topic with the *Share → Add to Home Screen* steps.
- **Long-press the icon** (Android) or right-click it (computer) for shortcuts to Dashboard, Notifications, My learning and Jobs for me.
- **Privacy:** the service worker (`/sw.js`) stores only the app's own CSS, JavaScript, icons and a plain offline page. Pages and personal information are always fetched fresh from the server and never saved on the device. Without a connection, people see "You're offline" with a *Try again* button.
- **Updates:** static files carry the version (`?v=APP_VERSION`), so changing `APP_VERSION` in `app/config.py` makes installed apps fetch the new files and discard the old cache.
- **Icons:** `python scripts/build_pwa_icons.py` regenerates every icon size (including the "maskable" ones Android crops into circles or squircles) from `app/static/img/logo-mark.svg`. It needs `rsvg-convert` (`librsvg2-bin`).
- The manifest is served from `/manifest.webmanifest`, so its address and colours can never go out of step with the app. Installation needs HTTPS, which Render provides automatically.

## Testing

```bash
pytest                                                    # SQLite in memory
TEST_DATABASE_URL=postgresql://localhost/skillbridge_test pytest   # PostgreSQL (recommended)
flask db check                                            # models and migrations agree

# Browser layout check: every page at 320–440px phone widths and desktop, light and dark,
# looking for sideways scrolling, console/CSP errors and low-contrast text
pip install playwright && playwright install chromium
python scripts/ui_check.py http://localhost:5000 --shots screenshots/
```

Chrome phone emulation is not real Safari, so check on a real iPhone before each release.

## Deploying to Render

The full, step-by-step launch checklist is in **[docs/GO-LIVE.md](docs/GO-LIVE.md)**. In short:

1. Push this folder to a GitHub repository.
2. In Render, choose **New → Blueprint** and select the repository. `render.yaml` creates:
   - the web service (`gunicorn -c gunicorn.conf.py`), with migrations and the plan catalogue applied on each deploy;
   - a PostgreSQL 16 database that only these services can reach;
   - three cron jobs: the daily job (`flask subscriptions process`), notification emails every 10 minutes (`flask notifications send`) and the nightly backup (`flask backup create`);
   - a randomly generated `SECRET_KEY`.
3. Fill in the values Render asks for:
   - `PUBLIC_BASE_URL`;
   - the `STORAGE_*` bucket details;
   - the `SMTP_*` and `MAIL_FROM` email settings;
   - `PAYSTACK_SECRET_KEY`;
   - `BACKUP_ENCRYPTION_KEY` (from `flask backup make-key`);
   - `SECURITY_CONTACT` and `SALES_EMAIL`.
4. After the first deploy, open the web service **Shell** and run `flask create-superadmin --email … --name …`. Sign in, then turn on two-step sign-in under **My account**.
5. Open **System status** in the console and work through anything amber or red, or run `flask system check` in the Shell.

Health check: `GET /api/v1/health`. The blueprint uses paid instance types, because pre-deploy commands and cron jobs need them. Adjust `plan:` values in `render.yaml` if required.

## Database backups

**Built in (nightly).** The `portia-skillbridge-backup` cron job runs `flask backup create` every night. It writes every table to a zip with a manifest (app version, database revision, row counts and a SHA-256 checksum per table) and encrypts it with `BACKUP_ENCRYPTION_KEY`. Fernet is used, so tampering is detected. The result is saved as `backups/portia-YYYYMMDD-HHMMSS.psb` in the storage bucket. Backups older than `BACKUP_RETENTION_DAYS` (30) are deleted, but the newest 7 always stay. They need no database tools and work the same on PostgreSQL and SQLite.

```bash
flask backup make-key                 # once: create the key, store it in a password manager AND in Render
flask backup create                   # what the cron job runs
flask backup list                     # newest first
flask backup verify portia-20261122-024100.psb     # decrypt and check every checksum; changes nothing
flask backup restore portia-20261122-024100.psb    # into an EMPTY database at the same revision
flask backup restore FILE.psb --wipe  # replace everything (asks to confirm)
```

**Restoring after a disaster:**
1. Create a new PostgreSQL database and point `DATABASE_URL` at it.
2. Deploy the same release and run `flask db upgrade`.
3. Set the same `BACKUP_ENCRYPTION_KEY`.
4. Run `flask backup verify NAME`, then `flask backup restore NAME`.

ID sequences are reset, so new records carry on where the backup left off. Super Administrators can also see, run and download backups (still encrypted) under **Backups** in the console.

**Also recommended:**
- Render's own snapshots on paid Postgres plans.
- Object versioning on the storage bucket, because uploaded photos and documents live there, not in the database backups.
- A restore test into a scratch database once a quarter.

## Security

Stage 10 review:
- `bandit` (no medium or high findings left) and `pip-audit` (Pillow 12.3 → 13.0 fixed 42 known issues; none remain).
- Optional **two-step sign-in**. A Super Administrator can reset it for someone who lost their phone, and the System status page warns about Super Administrators without it.
- `Cross-Origin-Resource-Policy`, `X-Permitted-Cross-Domain-Policies`, `security.txt` and `robots.txt`.
- Password-reset and invitation tokens hidden from access logs (`gunicorn.conf.py`), and slow-request logs that omit query strings.
- Notification links limited to this site.
- The database closed to the internet (`ipAllowList: []`).
- Encrypted backups.

Run `bandit -q -r app -x app/static -ll` and `pip-audit -r requirements.txt` before each release (both are in `requirements-dev.txt`).

Already in place before that: CSRF on all forms, a strict CSP, `X-Frame-Options: DENY`, `nosniff`, a referrer policy, HSTS in production, secure/HttpOnly/SameSite cookies, scrypt password hashing (minimum 10 characters), rate limiting, server-side validation, an audit trail, tenant isolation with fail-closed defaults, and a 10 MB upload cap. Sign-in adds: per-IP rate limits, account lockout (5 failures → 15 minutes), the same generic error for wrong email or wrong password (with equal timing), a fresh session on every sign-in, sign-out by POST only, off-site redirect protection, single-use reset links that expire after one hour, and a session token that changes with the password, so a password change or reset signs out every other device.

## Roadmap

| Stage | Deliverable | Status |
|---|---|---|
| 1 | SaaS foundation, architecture and database | ✅ Done |
| 2 | Authentication, tenants and RBAC; Super Admin console behind sign-in | ✅ Done |
| 3 | Organization and user management, invitations, help assistant | ✅ Done |
| 4 | Learner and beneficiary management, passport photos, documents, cloud storage | ✅ Done |
| 5 | Training programmes, attendance and LMS modules | ✅ Done |
| 6 | Skills assessment and certification | ✅ Done |
| 7 | Recruitment and employment management | ✅ Done |
| 8 | Monitoring, evaluation, reporting, Chart.js dashboards, PDF/Excel export | ✅ Done |
| 9 | Paystack payments, invoices, automated renewals | ✅ Done |
| 10 | Security hardening, optimisation, automated backups, deployment | ✅ This release (1.0) |

---
© Kayron Global Tech Services. All rights reserved.
