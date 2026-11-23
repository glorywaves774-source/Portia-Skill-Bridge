# Changelog

## 1.0.1 — Install it like an app (22 Nov 2026)

PORTIA SkillBridge can now be installed on phones, tablets and computers. It gets its own icon and opens full-screen like any other app, with no app store involved.

### What's new
- **Install app** in the menu (☰ on phones, bottom of the sidebar on computers). It appears on Android and in Chrome or Edge on computers whenever the browser allows installing.
- **iPhone and iPad:** the same menu item opens step-by-step help (Share → Add to Home Screen), because Safari doesn't let websites show an install button.
- **Opens on your dashboard.** The installed app starts on the dashboard. Long-press the icon for shortcuts to Notifications, My learning and Jobs for me.
- **Proper app icons** in every size Android, iPhone and computers need, including the rounded and circular versions phones cut out. Browser tabs also get a sharper icon.
- **Offline page.** With no connection, the app shows a clear "You're offline" page with a *Try again* button instead of the browser's error.
- **Back button.** As before, the installed app shows a Back arrow at the top-left, since there's no browser Back button.
- New help topic: *How do I install the app on my phone?*

### Privacy
- The app stores only its own design files and icons on the device. **No names, records, documents or pages are ever saved on the phone.** Everything personal is fetched fresh, so signing out leaves nothing behind.

### Checks
- Chrome reports no installability problems. Offline mode was tested, and the cache was inspected to confirm nothing private is stored.
- 8 new automated tests: the manifest, icon sizes, service-worker rules, the offline page containing no personal details, and the install help.

## 1.0.0 — Stage 10: Notifications, security, backups and go-live (22 Nov 2026)

All ten stages are complete. This release makes PORTIA SkillBridge ready to run for real customers.

### What's new
- **Notifications.** A bell on every page shows what needs your attention, with a red count for unread items. Each person sees only what concerns them:
  - **Learners:** enrolled in a programme, work marked, certificate ready, shortlisted, interview, offer, hired.
  - **Centre staff:** new job applications, someone joining the workspace, follow-up checks due.
  - **Employer staff:** new candidates shortlisted for their jobs.
  - **People who manage billing:** payments, failed renewals, trial endings.

  Tapping an item takes you straight to it.
- **Email choices.** Under **My account → Notifications**, choose which kinds also come by email. Emails are queued and sent within 10 minutes, so pages never wait for the mail server, and they're retried if the mail server is down.
- **Two-step sign-in.** Anyone can protect their account with a code from an authenticator app (Google Authenticator, Microsoft Authenticator, Authy…). There are eight one-time recovery codes for when the phone isn't to hand. PORTIA staff can switch it off for someone who lost their phone, and System status warns if a Super Administrator hasn't turned it on.
- **Nightly encrypted backups.** The whole database is copied every night, encrypted and saved in your storage bucket. Backups are kept for 30 days, and the newest 7 are always kept. Each one can be checked (`flask backup verify`) and restored into a new database (`flask backup restore`). The console's **Backups** page lists them, runs one on demand and downloads them (still encrypted).
- **System status.** A console page, also `flask system check`, that checks everything a live site needs:
  - the database is up to date;
  - uploads go to the storage bucket;
  - email is set up;
  - Paystack's mode;
  - recent backups;
  - the three scheduled jobs are running;
  - two-step sign-in for Super Administrators.
- **Go-live checklist.** `docs/GO-LIVE.md` walks through accounts, secrets, deploying, the first sign-in, testing, switching on live payments and keeping the site healthy.

### Security review
- Scanned the code (bandit) and every library (pip-audit). **Pillow, which processes uploaded photos, was updated from 12.3 to 13.0, fixing 42 published security issues.** No known issues remain.
- Two new protective headers on every page, plus `security.txt` (where to report security problems) and `robots.txt` (keeps search engines out of private pages).
- Password-reset and invitation links are hidden in the server's access logs, and slow-page logs leave out anything after the "?".
- Notification links can only point to pages on this site.
- The database now accepts connections only from the app's own services on Render, not from the internet.

### Faster
- List pages (programmes, courses, assessments, certificates, jobs, employers, placements) now load their related records in a few queries, instead of one per row, so they stay quick as data grows. Every page tested responds in under 100 ms with the sample data.
- Stylesheets, scripts and icons carry the version number in their address, so browsers can keep them for a year and still get each new release straight away.
- Production server settings are in `gunicorn.conf.py`: worker threads, recycling and timeouts.

### Fixed while testing
- Restoring a backup at first left date and time columns as text, and stored "empty" settings as the JSON value null. Both are fixed, and a restored database now matches the original exactly, row for row, on PostgreSQL.
- Adding the new settings columns to accounts broke one existing PostgreSQL query, which lists trainers without duplicates. The columns now use PostgreSQL's `jsonb` type. Found by running the whole test suite on PostgreSQL.

### Checked before release
- 338 automated tests pass on SQLite and PostgreSQL 16. 26 are new, covering:
  - who gets which notification, privacy, email choices, retries and duplicates;
  - two-step sign-in, including the standard RFC 6238 test codes, codes not working twice, recovery codes, lock-out and the lost-phone reset;
  - backups: encryption, tampering, wrong keys, an exact restore, retention and the console pages;
  - the new security headers and log redaction.
- A full backup of the PostgreSQL demo database was restored into an empty database and compared row by row.
- Every page was checked as seven kinds of user at 320–440px phone widths and on desktop, in light and dark mode.

### Upgrading from Stage 9
1. Run `pip install -r requirements.txt` (Pillow 13, and cryptography for backups).
2. Run `flask db upgrade`. It adds notifications, job runs, two-step sign-in and email-choice columns.
3. In Render, add:
   - `PUBLIC_BASE_URL`;
   - `BACKUP_ENCRYPTION_KEY` (from `flask backup make-key`);
   - `SECURITY_CONTACT`.
4. Sync the blueprint to create the notification and backup cron jobs. The start command is now `gunicorn -c gunicorn.conf.py wsgi:app`.

## 0.9.0 — Stage 9: Online payments with Paystack (22 Nov 2026)

### What's new
- **Pay online.** Choose **Pay now** under Subscription, check the amount, and pay on Paystack's secure page with Mobile Money (MTN, Telecel, AirtelTigo) or a card. The paid period starts as soon as Paystack confirms, and you come straight back to your receipt.
- **Invoices and receipts.** Every payment gets a numbered invoice (for example INV-U6SFUQ-2026-00002). Each shows the plan, the paid period, how it was paid and the reference, and can be downloaded as a PDF invoice or receipt. Receipts are also emailed.
- **Changing plan made fair.**

  | Change | What happens |
  |---|---|
  | Renewal | Starts when the current period ends, so paying early never loses days |
  | Upgrade, or monthly to yearly | Starts straight away, with the unused part of what you've paid taken off the price |
  | Cheaper plan | Waits until the current period ends, with nothing to pay now. Refused, with a clear list, if you use more than the smaller plan allows. **Keep** undoes it |
  | Enterprise | Arranged with sales |

- **Automatic renewals.** Paying by card saves it (only the brand, last four digits and expiry are kept). The saved card pays each renewal automatically. If it's declined, we email you and try again the next day, up to three times, while your workspace keeps working through the 7-day grace period. You can turn automatic renewal off, or remove the card, at any time.
- **Reminders.** We email three days before a free trial or an unpaid period ends. This includes everyone who paid by Mobile Money, since Mobile Money can't be charged automatically.
- **Come back any time.** A workspace whose plan has ended can still open Subscription, pay and carry on with all its records.
- **For PORTIA staff.**
  - A **Payments** page in the console shows money received this month and this year, failed renewals, and every invoice.
  - **Record a payment** on each workspace handles bank transfers, cheques and cash. It gives the workspace a proper receipt.
- **Practice mode.** Without a Paystack key, development sites show a practice checkout page, so the whole flow can be tried with no money involved. It never runs in production.
- **Help.** PORTIA Assistant answers new questions:
  - paying;
  - changing plan;
  - failed payments;
  - invoices and receipts;
  - stopping automatic renewal.

### Security
- A payment only counts after the server checks it directly with Paystack, and only if the amount and currency match the invoice exactly. A tampered amount is refused and flagged for staff.
- Paystack's notifications (webhooks) must carry a valid signature made with your secret key. Processing the same payment twice changes nothing.
- Paystack receives only the billing email you type, the amount and two reference numbers. Card and Mobile Money details are entered on Paystack's page, never on this site.
- The security policy now allows forms to go to Paystack's checkout page, and nowhere else.
- Only people allowed to manage billing (owners, administrators, finance officers) can pay or change plans. Invoices can't be seen from other workspaces.

### Fixed while testing
- Taking a payment on PostgreSQL failed: the invoice lock clashed with the way plans are loaded. The lock now covers only the invoice. A browser test caught this before release.
- When the daily job took a renewal, the new period was dated from the server clock instead of the job's own time. The new period now always starts from the time the job ran.
- A workspace whose plan had ended couldn't remove its saved card. Now it can.
- Updated old wording that still said online payment was "coming in a later release" (pricing page) and that skills from assessments were "coming later" (learner profile).

### Checked before release
- 312 automated tests pass on SQLite and PostgreSQL 16. 29 of them are new, covering:
  - paying and declined payments;
  - Mobile Money;
  - repeated webhooks and bad signatures;
  - tampered amounts;
  - upgrades with credit, and downgrades at renewal;
  - failed renewals with retries and expiry;
  - reminders;
  - lapsed workspaces paying to return;
  - permissions and isolation between workspaces;
  - PDFs, manual payments, and the live Paystack client.
- A complete payment was made in a browser: trial → checkout → practice Paystack page → receipt → saved card → upgrade quote.
- Every page was checked as seven kinds of user, at 320–440px phone widths and on desktop, in light and dark mode.

### Upgrading from Stage 8
1. Run `flask db upgrade`. It adds invoices and saved payment methods, scheduled plan changes and renewal tracking on subscriptions, and a billing email on workspaces.
2. Set `PAYSTACK_SECRET_KEY` and the SMTP settings on the web service and the cron job.
3. Add the webhook URL in Paystack.

## 0.8.0 — Stage 8: Monitoring, evaluation and reports (22 Nov 2026)

### What's new
- **Reports page.** A new **Reports** item shows your results for any period. Tap a quick period (this year, this quarter, last quarter, last year, last 12 months) or pick dates, and narrow it to a programme. **More filters** lets you look at women only, one region, people with a disability or a beneficiary group.
- **The results chain.** How many learners were registered, enrolled, completed training, were assessed competent, were certified and were placed in work in the period.
- **Rates, with the numbers behind them.** Completion, attendance, competence, placement, and still in work at follow-up, each shown with its basis, for example "6 of 7 enrolments" or "3 of 6 graduates".
- **Breakdowns funders ask for.** Every result by female, male, youth (15–35) and people with a disability, plus tables by age band, region and programme.
- **Charts.** Results chain, month-by-month trend, female and male side by side, type of work, and the share still in work at 3, 6 and 12 months. Charts work in light and dark mode, show details when you hover or tap, and have the same figures in a table.
- **PDF report.** A print-ready report with the filters used, charts, all tables and how each rate is worked out, ready to attach to a donor report.
- **Excel workbook.** Six sheets (Summary, By group, By programme, Monthly, Employment, Learners) with Excel charts, ready for your own analysis. The Learners sheet lists each learner counted, with no phone numbers or emails.
- **Plain definitions.** "How each figure is worked out" at the bottom of the page, and on the PDF, explains every number.
- **Navigation.** M&E and finance officers get Reports as their second tab on phones. Everyone who can see reports gets a dashboard shortcut.
- **Help.** PORTIA Assistant answers three new questions: seeing reports, exporting a donor report, and what the rates mean.

### Security
- Reports only ever count the workspace's own records. A programme from another workspace is ignored, even if someone types its link.
- Viewing reports needs *See reports*. Downloading needs *Export reports* too, so finance officers can view reports but not download learner lists.
- Every download is written to the activity log. Downloads are never cached, and spreadsheet cells can't run formulas.
- The charts library is bundled with the app (no outside scripts), so the strict security policy is unchanged.

### Checked before release
- 283 automated tests pass on SQLite and on PostgreSQL 16. 14 of them are new: they check each indicator against a small set of learners whose figures were worked out by hand.
- Every page was checked in a browser as an Organization Administrator, M&E Officer, Training Manager, Trainer/Assessor, Learner, Employer staff member and Super Administrator, at 320–440px phone widths and on desktop, in light and dark mode.
- The PDF and Excel files were opened and checked page by page.

### Also changed
- `flask demo-data` now gives realistic training dates (enrolled when the programme started, completed when it ended, one dropout), so the sample report shows sensible rates.
- `openpyxl` (Excel files) is now listed in `requirements.txt`.

### Upgrading from Stage 7
No database changes. Run `pip install -r requirements.txt` to install `openpyxl`.

## 0.7.0 — Stage 7: Recruitment and employment (22 Nov 2026)

### What's new
- **Employers.** Keep a list of the employers you work with: sector, contact person, phone, email, website and location. Deactivate one you no longer work with without losing its history.
- **Employer staff accounts.** Invite an employer's HR person from the employer's page. They sign in and see only their own company: its vacancies, the candidates you've shortlisted for them, and the people placed with them. They never see your other learners. Contact details are shown to them only after you shortlist someone.
- **Vacancies.** Post jobs with a number (J2026-001), trade, type (full-time, part-time, internship, apprenticeship…), number of positions, region, pay range, closing date and the competencies the job needs. Vacancies move from Draft to Open to Closed, and become Filled automatically when every position is taken.
- **Matching.** Each vacancy has a **Find candidates** tab ranking your learners by how well they fit: assessed competencies count 60%, training in the trade 25% and living in the region 15%. It shows the reasons, for example "2 of 3 required competencies" or "Lives outside Volta". Tick learners and put them forward in one step.
- **Applications and interviews.** Track each candidate from Applied or Nominated through Shortlisted, Interview, Offer made and Hired, or Not selected or Withdrawn. Scheduling an interview emails the learner the time and place. Notes stay with the application.
- **Learners apply for jobs.** Learners have a **Jobs** page showing open jobs that suit them, with how well they match. They can apply with a short note, follow their applications and withdraw.
- **Placements.** Hiring someone records a placement and adds the job to the learner's employment history. You can also record placements directly, including self-employment.
- **Follow-up checks.** Every placement gets checks at 3, 6 and 12 months. Record whether the person is still working and what they earn. If they've left, the placement is ended. Overdue checks are flagged on the dashboard.
- **Dashboard.** A new Jobs and employment panel shows open vacancies, positions still to fill, people in work now, the share of graduates placed, how many were still working at the 6-month check, and overdue checks.
- **Demo data.** `flask demo-data` now builds a complete sample training centre, with programmes, a course, assessments, certificates, employers, vacancies, applications and placements.
- **Help.** PORTIA Assistant answers new questions about employers, vacancies, matching, applications, placements, follow-up checks, employer accounts and applying for jobs.

### Security
- Employer staff accounts are limited to their own employer. Every page and action checks this, not just the menus.
- Workspaces only ever see their own employers, vacancies, applications and placements.

### Fixed while testing
- On narrow phones (320–360px), an employer page with a long staff email address scrolled sideways. Long names and emails in lists now wrap.

### Checked before release
- 269 automated tests pass on SQLite and on PostgreSQL 16. The database upgrade from Stage 6 was tested and can be rolled back.
- Every page was checked in a browser as an Organization Administrator, Training Manager, Trainer/Assessor, M&E Officer, Learner, Employer staff member and Super Administrator, at 320–440px phone widths and on desktop, in light and dark mode.

### Upgrading from Stage 6
Run `flask db upgrade`. It adds tables for employers, vacancies, applications, placements and follow-ups, and links employer staff accounts to their employer.

## 0.6.0 — Stage 6: Skills assessment and certification (22 Nov 2026)

### What's new
- **Competency frameworks.** Build frameworks for a trade, for example *Garment Construction*, with coded competencies (GC-01, GC-02…), assessment criteria and the level a learner must reach. Frameworks move from Draft to Active to Retired.
- **Assessments.** Assessors record practical, written, oral, portfolio or observation assessments against a framework. Each competency is rated on a four-level scale (Not yet competent, Developing, Competent, Proficient) with comments, and criteria can be ticked. Photos and documents can be attached as evidence. Finalising locks the result and shows whether the learner is competent.
- **Competency profiles.** Each learner record has a **Competencies** tab showing their latest result for every competency, and learners see their own skills under My learning.
- **Skills gaps.** See, for a programme or the whole workspace, how many learners have achieved each competency, how many are below the level and how many haven't been assessed.
- **Certificates.** Issue course, programme and competency certificates, one at a time or to everyone eligible in a programme or course. Each has a number and a verification code. The PDF certificate includes a QR code, the issuer, the signatory and the competencies achieved. Learners download their own certificates.
- **Public verification.** Employers can check a certificate at **/verify** by typing the code or scanning the QR code. They see the learner's name, the award, the issuer and the date, or a clear notice that it was revoked.
- **Revoking.** Certificates can be revoked with a reason; the verify page shows this immediately.
- **Help.** PORTIA Assistant answers 6 more questions about frameworks, assessing, skills gaps, issuing, verifying and revoking certificates.

### Security
- Workspaces only ever see their own frameworks, assessments, evidence and certificates.
- The public verify page shows only what an employer needs, nothing else from the learner record, and is limited to 30 checks a minute.
- Certificates can only be issued when the learner has actually earned them (completed course or programme, or a competent finalised assessment).

### Checked before release
- 257 automated tests pass on SQLite and on PostgreSQL 16. The database upgrade from Stage 5 was tested and can be rolled back.
- Every new page was checked in a browser as an Organization Administrator, an Assessor/Trainer and a Learner, at 320–440px phone widths and on desktop, in light and dark mode.
- The certificate PDF and its QR code were opened and checked by eye, and the QR link opens the verify page.

### Fixed while testing
- Any assessor could change another assessor's draft. Now only the assessor who started it, or an administrator, can.
- Long lists of competencies were cut off on the PDF certificate. They now wrap neatly onto extra lines.
- Deleting a draft assessment with evidence attached logged a database warning. Evidence is now removed cleanly with the draft.
- The Stage 5 notes overstated the test count (264); the correct figure was 240.
- On narrow phones (320–360px), pages with two buttons in the header (such as Assessments) scrolled sideways. Header buttons now wrap onto a second row.

### Upgrading from Stage 5
Run `flask db upgrade` (adds tables for frameworks, competencies, assessments, ratings, evidence and certificates). Two new libraries are used: `reportlab` (PDF) and `segno` (QR codes); both are in `requirements.txt`.

## 0.5.0 — Stage 5: Training programmes and online learning (22 Nov 2026)

### What's new
- **Training programmes.** Create programmes with a trade, delivery mode (in person, online or blended), dates, location, capacity and funder. Each gets a code such as **P2026-001**. Statuses: Draft, Open for enrolment, Running, Completed, Cancelled.
- **Trainers.** Assign trainers to a programme and mark one as the lead.
- **Enrolment.** Tick learners from a searchable list, or select everyone shown. Capacity is enforced and people already enrolled are skipped. Mark learners Completed, Dropped out or Withdrawn.
- **Sessions and attendance.** Schedule sessions with times, venue and trainer. Take the register on a phone, with one-tap Present, Late, Absent or Excused, a "Mark everyone present" button and optional notes. Attendance rates show for every learner and programme, with anything below 75% highlighted.
- **Online courses (LMS).** Build courses from modules and lessons:
  - **Reading:** text with headings, lists and bold.
  - **Video:** YouTube or Vimeo, played in their privacy-friendly versions.
  - **Downloads:** PDF, Word, PowerPoint, Excel or images.
  - **Quiz:** pass mark, limited attempts, and the option to show answers afterwards.
  - **Exam:** a server-timed countdown that submits automatically.
  - **Assignment:** a written answer and/or a file, with a due date.
- **Publishing.** Courses can only be published once they're complete: lessons exist, questions have correct answers, videos have links and downloads have files.
- **Programme courses.** Link a course to a programme and everyone enrolled gets the course automatically.
- **My learning** for learners: their courses with progress, upcoming sessions, their programmes and attendance, a lesson player with previous and next, quiz and exam results with explanations, and assignment marks with feedback.
- **Marking.** Trainers see submissions waiting to be marked first, then enter a mark and feedback.
- **Training tab** on every learner record: programmes, attendance and course progress.
- **Plan limits** now count programmes and courses.
- **Help.** PORTIA Assistant answers 7 more questions about programmes, enrolment, attendance, courses, questions, marking and learning.

### Security
- Learners only reach published courses they're enrolled in. Their attempts, answers and files stay private to them.
- Quizzes and exams are marked on the server. Correct answers are never sent to the browser before submission, and exam deadlines are enforced by the server, not the page.
- Lesson text can't contain HTML or scripts. The only outside content allowed on pages is the YouTube and Vimeo players.

### Checked before release
- 240 automated tests pass on SQLite and on PostgreSQL 16. The database upgrade from Stage 4 was tested on real data and can be rolled back.
- Every programme tab, attendance register, course tab, course lesson and learner page was checked in a browser as an Organization Administrator, a Trainer and a Learner. Each was checked at 320–440px phone widths and on desktop, in light and dark mode. There was no sideways scrolling, no errors and no low-contrast text.
- Walked through on a phone: taking a register, watching a video lesson, sitting a timed exam and seeing the result.

### Fixed while testing
- On phones, a table containing dropdowns could make the whole page scroll sideways. Tables now keep everything inside their own scroll box, everywhere in the app.
- Sticky save buttons on phones now leave room for the assistant's round button.

### Upgrading from Stage 4
Run `flask db upgrade` (adds 15 tables for programmes, attendance and courses).

### Not yet included
- Skills assessment forms, competency profiles and digital certificates (Stage 6). Course completions will feed into certificates there.
- Uploading video files directly. Use YouTube (it can be unlisted) or Vimeo, which saves your storage and your learners' data.

## 0.4.0 — Stage 4: Learner and beneficiary management (22 Nov 2026)

### What's new
- **Learner and beneficiary records.** Register people with personal and contact details, their ID document (Ghana Card numbers are checked), and an emergency contact. Each record also holds the details donors and M&E reports need: gender, age, region and district, disability, highest education, employment status at registration, and beneficiary categories such as youth, women, persons with disability, rural or returnee.
- **Unique learner numbers.** Each learner gets one automatically, e.g. **L2026-00001**.
- **Duplicate checks.** The same ID document is refused. The same name and date of birth, or the same phone and date of birth, shows a warning first.
- **Passport photos** can be taken with the phone camera or uploaded. They're cropped to passport shape, and hidden data such as location is removed.
- **Documents.** Upload certificates, CVs, ID copies, transcripts, reference letters and consent forms (PDF, Word, JPG, PNG).
- **History.** Record education, skills and qualifications (with certifying body, e.g. NVTI or CTVET), and employment, with optional monthly income for outcome tracking.
- **Search and filters.** Find learners by name, number, phone, email, status, gender, region, category and disability.
- **CSV export** opens in Excel or Google Sheets. Contact details are included only for people who manage records, and every export is recorded.
- **Status and archive.** Learners can be marked Active, Completed, Dropped out, Inactive or Archived. Archived records don't count towards the plan.
- **Permanent deletion** for data-protection requests. Only an Organization Administrator can do it, after typing the learner number, and it removes the photo and documents too.
- **Dashboard:** learners registered, active, share of women, people with disabilities, new this month, and top regions.
- **Learner self-service:** invite a learner to sign in from their record. They see their profile, keep their contact details up to date and upload their own documents, such as a CV. They can't change their name or ID details.
- **Cloud storage.** Photos and documents are stored in any S3-compatible service (Cloudflare R2, AWS S3, Backblaze B2, DigitalOcean Spaces), or on local disk for development.
- **Help.** PORTIA Assistant now answers 9 more questions about learners, photos, documents, export and self-service.

### Security and privacy
- Each workspace's files are kept separately, and every download goes through a sign-in and permission check. Nothing is publicly reachable.
- Uploaded files are identified by their contents, not their names. Disguised programs, web pages, macro-enabled Word files, damaged images and oversized "bomb" images are refused.
- Read-only roles see ID numbers masked, and don't see dates of birth, disability details, notes or income.
- The audit log records which fields changed, never the personal values. Every document download and export is recorded.
- Downloads are sent in a way the browser can't run as a web page.

### Checked before release
- 214 automated tests pass on SQLite and on PostgreSQL 16, including the S3 storage backend against a simulated S3 service.
- Every page and every tab of a learner profile was checked in a browser as a visitor, a Super Administrator, an Organization Administrator, a Trainer and a Learner. Each was checked at 320–440px phone widths and on desktop, in light and dark mode. There was no sideways scrolling, no errors and no low-contrast text.

### Upgrading from Stage 3
1. Run `flask db upgrade` (adds learner, file and counter tables).
2. Before uploading in production, set `STORAGE_BACKEND=s3` and the `STORAGE_*` values. Render's normal disk is wiped on each deploy.

### Not yet included
- Training programmes, attendance and courses (Stage 5). The training and assessment history on each learner will fill in from those modules.
- Excel and PDF reports with charts (Stage 8).

## 0.3.0 — Stage 3: Organization and user management (22 Nov 2026)

### What's new
- **Invite people by email.** Organization Administrators open **People**, enter an email, choose one or more roles and add an optional note. The person gets a link that works for 7 days. Newcomers create their password from the link; people who already have an account sign in and accept. Pending invitations can be sent again or cancelled.
- **Manage people.** Select anyone on **People** to see their details and activity, and to change their roles, deactivate them (records kept, access paused), reactivate them, or remove them from the workspace.
- **Safeguards.** A workspace always keeps at least one active Organization Administrator. The owner can't be deactivated or removed, and nobody can deactivate or remove themselves.
- **Plan limits are enforced** for staff and learners. Pending invitations count, so a workspace can't over-invite.
- **Super Admin tools:**
  - Create a workspace and email an invitation to its owner.
  - Record payments received by Mobile Money, bank transfer or cheque, and start, renew, extend a trial, change plan or cancel.
  - Manage every account: clear a sign-in lock, email a reset link, deactivate or reactivate, grant or remove Super Administrator access.
  - Edit plan prices, limits, features and visibility. Loading the default plans again no longer overwrites these edits.
- **PORTIA Assistant**, the in-app help:
  - A round button on every signed-in page opens it. It answers questions from the help guide.
  - It only offers links the person's role can open, and says when something is another role's job.
  - On phones it opens as a half-screen panel, keeps the keyboard down until you tap, and makes room for the keyboard while you type.
  - The full guide is also available as a searchable page.
- **Deactivated accounts are signed out straight away** on every device.

### Security
- Invitation links are stored only in scrambled (hashed) form, work once, and stop working when re-sent or cancelled. Accepting is refused if the signed-in account isn't the one invited.
- People-management actions can't reach anyone outside the current workspace: other workspaces' people simply "don't exist" (404).
- Every invitation, role change, deactivation, removal, subscription action and plan edit is recorded in the audit log, with payment references.
- The assistant sends nothing to outside services. Personal details (emails, phone numbers, Ghana Card and passport numbers) are removed before any question is logged.

### Checked before release
- 169 automated tests pass on SQLite and on PostgreSQL 16. The database upgrade from Stage 2 was tested on real data and can be rolled back.
- Every page was checked in a browser as a visitor, a Super Administrator, an Organization Administrator, a Trainer and a Learner. Each was checked at 320, 360, 390, 402, 440 and 1366px wide, in light and dark mode. There was no sideways scrolling, no errors and no low-contrast text.
- Assistant on iPhone and Android sizes: the panel takes 52% of the screen and sits just above the round button. The keyboard stays closed until tapped, suggestions and the privacy note hide while typing, and Back, Escape and × close it.

### Upgrading from Stage 2
Run `flask db upgrade` (adds the invitations table). Plans you edit in the console are now kept. Use `flask seed-plans --update` only if you want to reset them to the defaults.

### Not yet included
- Learner and beneficiary records, passport photos and document uploads (Stage 4).
- Paying online (Stage 9).
- The assistant answers in English only. French or other languages can be added on request.

## 0.2.0 — Stage 2: Sign-in, workspaces and role-based access (22 Nov 2026)

### What's new
- **Sign in and sign up.** Organizations and individuals can now create their own account from the website. They pick a plan and their free trial starts straight away, with no card needed. The sign-in pages use a soft, raised "neumorphic" look blended with the glass style.
- **Forgotten passwords.** People can reset their password with an emailed link. The link works once, for one hour.
- **Workspaces.** After signing in, people land in their workspace. Anyone who belongs to several workspaces chooses one, and can switch at any time from the menu.
- **Roles and permissions.** All nine roles from the brief now control what each person sees. The menu, every page and every API call show only what that role allows. Each person can see a plain-language list of what they can do under *Your access*.
- **Workspace pages.** Dashboard (plan, trial days left, people by role, recent activity), People, Subscription and usage, Activity log, and Workspace settings, each shown only to the roles allowed to see it.
- **My account.** People can change their name and phone number, change their password, and sign out of every device.
- **Super Administrator console** is now live at `/admin` behind sign-in, and works in production.
- **Blocked workspaces.** When a workspace is suspended, or its subscription has ended, people see a clear message. The owner can still open the plan page to renew.
- **Website.** Sign-in and free-trial buttons in the header; every plan on the pricing page has a "Start free trial" button.

### Security
- Account lockout after 5 wrong passwords (15 minutes), plus per-device limits on sign-in, sign-up and reset attempts.
- The same message is shown whether the email or the password was wrong, so nobody can find out who has an account.
- A fresh session on every sign-in. Sign-out only works through a button, so a link on another site can't sign anyone out. Links after sign-in can only point to this site.
- Changing or resetting a password signs that person out on every other device.
- Every sign-in, failed attempt, sign-out, password change and workspace change is recorded in the audit log.

### Fixed while testing
- Workspace isolation now also covers record counts, joins and records reached through a person's account. Each of these gaps was found by a new test and closed.
- Emails typed with a stray space, which phones often add, are accepted.
- A brand-new 14-day trial now shows "14 days left" instead of 13.
- The production secret key is read when the app starts, so changing it on Render takes effect on the next restart.

### Checked before release
- 122 automated tests pass on SQLite and on PostgreSQL 16.
- The database upgrade from Stage 1 was tested on real Stage 1 data, and it can be rolled back.
- Every page was checked in a browser as a visitor, a Super Administrator, an Organization Administrator and a Trainer. Each was checked at 320, 360, 390, 402, 440 and 1366px wide, in light and dark mode. There was no sideways scrolling, no errors and no low-contrast text.
- On phones, the menu closes with Back, Escape or a tap outside, and choosing a page leaves no extra Back step.

### Upgrading from Stage 1
Run `flask db upgrade`. Existing accounts keep their passwords. The development console at `/console` is replaced by `/admin`, which needs a Super Administrator sign-in.

### Not yet included
- Inviting people and changing their roles from the app (Stage 3). Until then, people join by signing up or are added by the platform team.
- Paying online (Stage 9).
- The help assistant (Stage 3).

## 0.1.0 — Stage 1: SaaS foundation, architecture and database (19 Nov 2026)

### What's new
- **One platform, many independent workspaces.** Organizations (training institutions, vocational institutes, training centres, schools, NGOs, companies, government agencies) and individuals each get their own workspace. Every workspace has a unique code such as `PSB-ORG-QPK5RF`.
- **Plans for organizations and individuals.** These are Starter (GHS 150), Professional (GHS 350), Business (GHS 750) and Enterprise (custom) for organizations, plus three proposed individual plans. Yearly billing gives two months free.
- **Subscription lifecycle.** Free trials, activation, renewal, a 7-day grace period when payment is due, automatic expiry, cancellation, plan changes, and suspension or reactivation by an administrator. A daily job moves subscriptions along by date.
- **Usage limits** per plan (learners, staff, courses, programmes, vacancies, storage).
- **Strict separation between workspaces.** One organization can never see or change another's records. If no workspace has been chosen, the system refuses the request instead of guessing.
- **Audit trail** of every workspace and subscription change.
- **Public website:** home page and a live pricing page (organizations/individuals, monthly/yearly).
- **Platform console (development only):** overview figures, workspace list and search, workspace detail with suspend and reactivate, plan catalogue and audit log.
- **Design:** premium glass-panel look, deep navy sidebar, teal and blue accents, a light/dark switch on every page, and full phone support with a slide-in menu and bottom tab bar.
- **Ready for Render:** `render.yaml` with the web service, the PostgreSQL database, the daily subscription job and an automatic secret key.

### Checked before release
- 64 automated tests pass on SQLite and on PostgreSQL 16; the database migration matches the models.
- Every page was checked at 320, 360, 390, 402 (iPhone Pro), 440 (iPhone Pro Max) and 1366px wide, in light and dark mode. No sideways scrolling, no browser errors and no low-contrast text.
- Phone menu: closes with ×, a tap outside, Escape and the Android Back button; choosing a page leaves no extra Back step; the theme choice is remembered.
- With the keyboard open, the bottom tab bar hides so forms keep the space.

### Known limits (by design for Stage 1)
- There is no sign-in yet (Stage 2), so the console is only available in development and from your own machine.
- Online sign-up and Paystack payments come in Stage 9. Until then, workspaces are created with `flask create-tenant`.
- The help assistant comes once sign-in exists.
