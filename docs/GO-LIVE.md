# PORTIA SkillBridge — go-live checklist

Work through this in order. Tick each box. The **System status** page in the Super Admin console (or
`flask system check` in Render's Shell) checks most of these for you and shows anything still amber or red.

## 1. Accounts you need

- [ ] **GitHub** repository with this project (private).
- [ ] **Render** account, with a payment method added. The blueprint uses paid instance types.
- [ ] **S3-compatible storage**, such as Cloudflare R2, AWS S3, Backblaze B2 or DigitalOcean Spaces. Create:
  - one **private** bucket;
  - an access key that can read and write only that bucket.

  Turn on **object versioning** (or the provider's backups), because uploaded photos and documents live here.
- [ ] **Email (SMTP)** provider, such as Brevo, Mailgun, Amazon SES or Postmark. Verify the sending domain (SPF and DKIM) so emails don't land in spam.
- [ ] **Paystack** business account (Ghana) with **Mobile Money** enabled. Complete Paystack's compliance review before taking live payments.
- [ ] A **domain name**, for example `app.skillbridge.com.gh`.

## 2. Make your secrets (once, on your own computer)

```bash
pip install -r requirements.txt
flask backup make-key        # BACKUP_ENCRYPTION_KEY
```

- [ ] Store the backup key in a password manager **and** somewhere else safe, away from Render. Without it, backups can't be opened.

## 3. Deploy on Render

- [ ] Render → **New → Blueprint** → pick the repository. `render.yaml` creates the web service, the database (closed to the internet) and three cron jobs.
- [ ] Fill in what Render asks for:

  | Setting | Value |
  |---|---|
  | `PUBLIC_BASE_URL` | `https://app.skillbridge.com.gh` (no trailing slash) |
  | `STORAGE_BUCKET`, `STORAGE_ENDPOINT_URL`, `STORAGE_REGION`, `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY` | From your storage provider. For R2: endpoint `https://<account>.r2.cloudflarestorage.com`, region `auto` |
  | `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM` | From your email provider; `MAIL_FROM` like `PORTIA SkillBridge <no-reply@skillbridge.com.gh>` |
  | `PAYSTACK_SECRET_KEY` | Start with the **test** key `sk_test_…` |
  | `BACKUP_ENCRYPTION_KEY` | The key from step 2 |
  | `SECURITY_CONTACT`, `SALES_EMAIL` | Where security reports and sales enquiries should go |

  The same values are asked for on the cron jobs that need them.
- [ ] Wait for the first deploy to go green. The health check is `/api/v1/health`.
- [ ] Add your domain under the web service's **Settings → Custom Domains**, and set the DNS record your registrar needs. Render issues the HTTPS certificate.

## 4. First sign-in

- [ ] Render → web service → **Shell**:
  ```bash
  flask create-superadmin --email you@yourcompany.com --name "Your Name"
  ```
- [ ] Sign in at `/signin`, then go to **My account → Two-step sign-in** and turn it on. Save the recovery codes.
- [ ] Console → **Plans**: check prices and limits (they're editable).
- [ ] Console → **System status**: everything should be green, apart from:
  - "Online payment: test mode" (until step 6);
  - the backup and cron jobs, if they haven't run yet.

## 5. Try everything once (test mode)

- [ ] Console → **Workspaces → New** to create a test workspace, and invite yourself as its administrator with a second email address.
- [ ] Check the invitation email arrived. If it didn't, check the SMTP settings and the sending domain.
- [ ] Register a learner and upload a photo. Check the file appears in the bucket under `tenants/…`.
- [ ] Pay for a plan with a Paystack **test card**. Check that:
  - the invoice turns **Paid**;
  - the receipt email arrives;
  - **Payments** in the console shows it.
- [ ] Console → **Backups → Back up now**, then in the Shell run `flask backup verify <name>`.
- [ ] Next morning, check **System status** shows the daily job, the notification emails job and the backup job ran.

## 6. Switch on real payments

- [ ] In Paystack: **Settings → API Keys & Webhooks**:
  - copy the **live** secret key into `PAYSTACK_SECRET_KEY` (on the web service **and** the daily cron job);
  - set the webhook URL to `https://app.skillbridge.com.gh/webhooks/paystack`.
- [ ] Make one small real payment, then refund it from the Paystack dashboard.

## 7. Keep it healthy

- **Every week:** glance at **System status** and **Payments** (look for failed renewals).
- **Every quarter:**
  1. Restore last night's backup into a scratch database: create one on Render, then `flask db upgrade` and `flask backup restore <name>` with `DATABASE_URL` pointing at it.
  2. Click around, then delete the scratch database.
- **Before each release:**
  1. Run `pytest`.
  2. Run `bandit -q -r app -x app/static -ll`.
  3. Run `pip-audit -r requirements.txt`.
  4. Deploy, and check **System status** afterwards.
- **If someone loses their phone:** Console → **People** → the person → **Turn off two-step sign-in (lost phone)**. Ask them to set it up again.
- **If the site is down:**
  1. Render → web service → **Logs**.
  2. Check the database is up and `/api/v1/health` responds.
  3. Roll back to the previous deploy from Render's **Events** tab if needed.

## What lives where

| Thing | Where | Backed up by |
|---|---|---|
| All records (workspaces, learners, training, assessments, jobs, payments, audit log) | Render PostgreSQL | Nightly encrypted backups in the bucket (30 days), plus Render snapshots on paid plans |
| Photos, documents, evidence, backups | Storage bucket | Bucket versioning or the provider's replication |
| Secrets | Render environment settings | Your password manager |
| Card and Mobile Money details | Paystack only (never on this site) | Paystack |
