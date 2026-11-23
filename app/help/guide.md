# PORTIA SkillBridge help guide

<!--
How this file works
- Each "## " heading is one topic the assistant can answer.
- keywords: extra words people might use (comma separated).
- permission: the person needs this permission for the topic to be "theirs";
  otherwise the assistant explains which role does it. Use "platform" for
  Super Administrator topics.
- links: pages to offer (Flask endpoint names). Links are only shown if the
  person can open them.
- suggest: show this question as a suggestion button.
-->

## How do I sign in?
<!-- keywords: login, log in, sign in, access, enter, account -->
<!-- links: auth.signin -->
Open the PORTIA SkillBridge website and choose **Sign in**. Enter the email address and password for your account.
Tick **Keep me signed in on this device** only on your own phone or computer.
If you belong to more than one workspace, you'll be asked which one to open.

## I forgot my password
<!-- keywords: forgot, reset, lost, password, can't sign in, locked -->
<!-- links: auth.forgot_password -->
On the sign-in page choose **Forgot password?** and enter your email address. We'll email you a link to choose a new password. The link works once, for one hour.
After 5 wrong passwords, your account is locked for 15 minutes. A password reset unlocks it straight away.
If no email arrives, check your spam folder, or ask the PORTIA SkillBridge team to send a new link.

## How do I change my password?
<!-- keywords: change password, new password, update password, security -->
<!-- links: account.profile -->
<!-- suggest -->
Open **My account**, fill in your current password and your new password twice, then choose **Change password**.
Use at least 10 characters with numbers or symbols. Changing your password signs you out on every other device.

## How do I sign out of every device?
<!-- keywords: sign out everywhere, lost phone, shared computer, logout all, stolen -->
<!-- links: account.profile -->
Open **My account** and choose **Sign out everywhere**. Every phone and computer signed in to your account is signed out, including this one. Sign in again with your password.

## How do I update my name or phone number?
<!-- keywords: profile, name, phone, details, update, edit, my account -->
<!-- links: account.profile -->
Open **My account**, change your name or phone number, and choose **Save**. Your email address can't be changed here; ask the PORTIA SkillBridge team if it needs to change.

## How do I switch workspace?
<!-- keywords: switch, change workspace, organization, other organization, workspaces, choose -->
<!-- links: auth.choose_workspace -->
<!-- suggest -->
If you belong to more than one workspace, open the menu and choose **Switch** next to the workspace name, or go to **My workspaces**. Pick the one you want; everything you see then belongs to that workspace only.

## How do I invite someone to my workspace?
<!-- keywords: invite, add person, add user, add staff, new member, new trainer, add learner, join, email invitation -->
<!-- permission: members.manage -->
<!-- links: workspace.people -->
<!-- suggest -->
Open **People**. In **Invite someone**, enter their email address, choose one or more roles, and add a note if you like. Then choose **Send invitation**.
They get an email with a link that works for 7 days. If they're new, they create a password; if they already have an account, they sign in and accept.
Pending invitations are listed under **Plan limits**. You can send them again or cancel them from there.

## How do I change someone's role?
<!-- keywords: role, change role, permissions, promote, make admin, trainer, assessor, access level -->
<!-- permission: members.manage -->
<!-- links: workspace.people -->
Open **People** and select the person's name. Under **Roles**, tick the roles they should have and choose **Save roles**.
A workspace must always keep at least one active Organization Administrator, so make someone else an administrator before you remove your own administrator role.

## How do I deactivate or remove someone?
<!-- keywords: deactivate, remove, delete user, block, suspend person, left, leaver, disable -->
<!-- permission: members.manage -->
<!-- links: workspace.people -->
Open **People**, select the person, and choose **Deactivate** or **Remove from workspace**.
**Deactivate** keeps their records and roles but stops them opening the workspace; you can reactivate them later. **Remove** takes away all their roles here, though their own account stays.
The workspace owner can't be deactivated or removed, and you can't deactivate or remove yourself.

## Why can't I see a page?
<!-- keywords: can't see, missing, not allowed, forbidden, permission, access denied, 403, menu, hidden -->
<!-- links: workspace.my_access -->
<!-- suggest -->
What you see depends on your role in this workspace. Open **Your access** on the dashboard to see what your roles allow.
If you need more access, ask your workspace's Organization Administrator to change your roles.

## How do I register a learner or beneficiary?
<!-- keywords: register, new learner, add learner, beneficiary, enrol, enroll, add trainee, add student -->
<!-- permission: learners.manage -->
<!-- links: learners.new, learners.index -->
<!-- suggest -->
Open **Learners** and choose **Register**. Fill in the personal details, contact details and background, then choose **Register**. Every learner gets a number such as L2026-00001 automatically.
If someone with the same name and date of birth, the same phone and date of birth, or the same ID document is already registered, you'll be warned before a duplicate is created.
Each plan allows a number of learners. Archived records don't count.

## How do I find a learner?
<!-- keywords: find learner, search learner, learner list, filter, region, gender, disability -->
<!-- permission: learners.view -->
<!-- links: learners.index -->
Open **Learners** and type a name, learner number, phone number or email in the search box. You can also filter by status, gender, region, beneficiary category and disability.

## How do I add a passport photo?
<!-- keywords: photo, passport photo, picture, image, camera, upload photo -->
<!-- permission: learners.manage -->
<!-- links: learners.index -->
Open the learner's profile and choose **Add photo** under the picture. On a phone you can take the photo with the camera. It is cropped to passport shape, and hidden data such as location is removed.

## How do I upload documents for a learner?
<!-- keywords: document, upload, certificate, cv, id copy, transcript, consent form, attach, file -->
<!-- permission: learners.manage -->
<!-- links: learners.index -->
Open the learner's profile, go to **Documents**, choose the type, add a title and pick the file. PDF, Word (.docx), JPG and PNG files are accepted, up to 10 MB each.
Files are checked when they're uploaded, kept privately for your workspace only, and every download is recorded.

## How do I record education, skills or work history?
<!-- keywords: education, school, qualification, skills, employment, job history, work experience, income -->
<!-- permission: learners.manage -->
<!-- links: learners.index -->
Open the learner's profile and use the **Education**, **Skills** or **Employment** tabs. Each tab has a form at the bottom to add a record, and an × to remove one.

## How do I export the learner list?
<!-- keywords: export, csv, excel, download list, spreadsheet, report, donor report -->
<!-- permission: reports.export -->
<!-- links: learners.index -->
Open **Learners**, set any filters you want, and choose **Export CSV**. The file opens in Excel or Google Sheets. Phone numbers and emails are included only for people who manage learner records, and every export is recorded.

## How can a learner see their own profile?
<!-- keywords: learner sign in, learner login, learner account, my profile, invite learner, learner portal -->
<!-- permission: learners.manage -->
<!-- links: learners.index -->
Open the learner's profile. Under **Sign-in for this learner**, enter their email and choose **Invite to sign in**. Once they accept, they can see their profile, update their contact details and upload documents such as a CV.

## How do I update my own details or upload my CV?
<!-- keywords: my profile, my details, my cv, my documents, update my phone, learner -->
<!-- permission: self.learning -->
<!-- links: me.my_profile -->
<!-- suggest -->
Open **My profile**. You can change your phone numbers, email, address and emergency contact, and upload documents such as your CV. To correct your name, date of birth or ID details, ask your training centre.

## How do I archive or delete a learner?
<!-- keywords: archive, delete learner, remove learner, data protection, erase, dropped out, completed, status -->
<!-- permission: learners.manage -->
<!-- links: learners.index -->
To stop using a record but keep it, open the learner's profile and set **Status** to Archived. You can also mark learners Completed, Dropped out or Inactive.
To erase a record completely, for example for a data-protection request, an Organization Administrator types the learner number under **Delete permanently**. This removes the photo and documents too, and it can't be undone.

## How do I set up a training programme?
<!-- keywords: programme, program, cohort, class, training programme, new programme, intake, course run -->
<!-- permission: programmes.manage -->
<!-- links: programmes.new, programmes.index -->
<!-- suggest -->
Open **Programmes** and choose **New programme**. Give it a name, trade, dates, location and capacity, and the funder if it's donor-funded. A code such as P2026-001 is added automatically.
Then, on the programme page, assign trainers, enrol learners and add sessions. Set the status to **Open for enrolment** or **Running** when you're ready.

## How do I enrol learners in a programme?
<!-- keywords: enrol, enroll, add learners to programme, intake, admit, cohort list -->
<!-- permission: programmes.manage -->
<!-- links: programmes.index -->
Open the programme, go to **Learners** and choose **Enrol learners**. Tick the learners, or use **Select all**, and choose **Enrol selected**. The capacity is checked, and anyone already enrolled is skipped.
Everyone enrolled is also added to the programme's published online courses automatically.

## How do I take attendance?
<!-- keywords: attendance, register, roll call, present, absent, late, mark attendance -->
<!-- permission: attendance.record -->
<!-- links: programmes.index -->
<!-- suggest -->
Open the programme, go to **Sessions & attendance**, and choose **Take register** next to the session. Mark each learner Present, Late, Absent or Excused; **Mark everyone present** saves time. Then choose **Save register**.
Trainers can take the register for the programmes they're assigned to. Attendance rates appear on the programme and on each learner's Training tab; anything below 75% is highlighted.

## How do I create an online course?
<!-- keywords: course, lms, online course, e-learning, build course, lesson, module, content -->
<!-- permission: courses.manage -->
<!-- links: courses.new, courses.index -->
Open **Courses** and choose **New course**. Add modules, then add lessons to each one. A lesson can be Reading, Video (a YouTube or Vimeo link), Downloads (PDF, Word, PowerPoint, Excel or images), Quiz, Exam (with a time limit) or Assignment.
When the course is ready, set it to **Published**. Learners see only published courses they're enrolled in. Link the course to a programme to enrol its learners automatically.

## How do I add quiz or exam questions?
<!-- keywords: question, quiz, exam, test, multiple choice, answers, pass mark, attempts, time limit -->
<!-- permission: courses.manage -->
<!-- links: courses.index -->
Open the quiz or exam lesson, scroll to **Questions**, write the question and up to six answers, and tick every correct one. Ticking more than one makes it a "choose all that apply" question. You can set points and an explanation.
In the lesson settings, set the pass mark, the number of attempts allowed and, for exams, the time limit. Exams submit automatically when time runs out, and late answers don't count.

## How do I mark assignments?
<!-- keywords: mark, grade, assignment, submission, feedback, score, homework -->
<!-- permission: courses.manage -->
<!-- links: courses.index -->
Open the course and go to **Marking**. Submissions waiting are shown first. Choose **Mark**, read the answer or download the file, then enter a mark and feedback. The learner sees both on their lesson page.

## How do I take my course?
<!-- keywords: my courses, start course, continue, lesson, quiz, exam, submit assignment, progress, my learning -->
<!-- permission: self.learning -->
<!-- links: learn.home -->
<!-- suggest -->
Open **My learning** and choose a course. Press **Start** or **Continue**, and work through each lesson. Readings and videos complete when you press **Done**, quizzes when you reach the pass mark, and assignments when you submit them.
Your progress, upcoming sessions and attendance are shown on **My learning**.

## How do I set up a competency framework?
<!-- keywords: competency framework, standard, units of competence, ctvet, nvti, occupational standard, criteria, cbt -->
<!-- permission: competencies.manage -->
<!-- links: frameworks.new, frameworks.index -->
Open **Competency frameworks** and choose **New framework**. Add each unit of competence with a code (e.g. GC-01), a title and its performance criteria, one per line, plus the level needed to be competent. Then set the status to **Active** so assessors can use it.
Units that learners have already been assessed on can't be deleted. When the standard changes, retire the framework and create a new version.

## How do I assess a learner?
<!-- keywords: assess, assessment, practical test, evaluation, rate, competent, not yet competent, rpl, observation -->
<!-- permission: assessments.conduct -->
<!-- links: assessments.new, assessments.index -->
<!-- suggest -->
Open **Assessments** and choose **New assessment**, or choose **Assess** on the learner's Competencies tab. Pick the learner, framework, type and date. For each unit, tick the criteria you observed and choose a rating from 1 (not yet competent) to 4 (proficient). Add photos or documents as evidence.
Use **Save draft** to finish later. **Finalise result** works out the overall result: competent only if every unit reaches its required level. Finalised results can't be changed; record a new assessment instead.

## How do I see skills gaps?
<!-- keywords: skills gap, gap analysis, weak areas, cohort, competency profile, who needs help -->
<!-- permission: assessments.view -->
<!-- links: assessments.gaps -->
Open **Assessments** and choose **Skills gaps**. Choose a framework and, if you like, a programme. You'll see how many learners are competent in each unit; units below 60% are highlighted. Each learner's own profile is on their Competencies tab.

## How do I issue a certificate?
<!-- keywords: certificate, issue certificate, award, diploma, completion certificate, print certificate -->
<!-- permission: certificates.issue -->
<!-- links: certificates.index -->
<!-- suggest -->
A certificate can be issued when a learner completes an online course, is marked Completed in a programme, or is assessed competent. Open **Certificates**: anything ready shows under **Ready to issue**, where you can issue a whole programme or course at once. For one learner, choose **Issue** on their Competencies tab.
Each certificate has a unique number and a verification code, and the PDF has a QR code.

## How can an employer check a certificate?
<!-- keywords: verify, verification, check certificate, fake certificate, genuine, qr code, employer -->
<!-- links: verify.form -->
Anyone can go to the **verification page** (/verify) and enter the code printed at the bottom of the certificate, or scan its QR code. It shows whether the certificate is valid, revoked or expired, who it was awarded to and who issued it. No other personal details are shown.

## How do I revoke a certificate?
<!-- keywords: revoke, cancel certificate, withdraw certificate, mistake, wrong certificate -->
<!-- permission: certificates.issue -->
<!-- links: certificates.index -->
Open the certificate from **Certificates**, give a reason under **Revoke certificate**, and confirm. The verification page shows it as revoked straight away; the reason stays internal. You can then issue a corrected certificate.

## What can each role do?
<!-- keywords: roles, role list, organization administrator, training manager, trainer, instructor, assessor, employer, m&e, monitoring, finance officer, learner, beneficiary -->
<!-- links: workspace.my_access -->
- **Organization Administrator:** everything in the workspace, including people and settings.
- **Training Manager:** learners, programmes, courses, attendance, apprenticeships, certificates and reports.
- **Trainer / Instructor:** courses and content, attendance, and viewing learners.
- **Assessor:** skills assessments, competency frameworks and certificates.
- **Employer:** vacancies, applicants and placements.
- **M&E Officer:** reports, exports and the activity log, read-only across modules.
- **Finance Officer:** plan, billing and payments.
- **Learner / Beneficiary:** their own profile, learning and job applications.

## How do I add an employer?
<!-- keywords: employer, company, business, partner, industry partner, add employer, employers list -->
<!-- permission: vacancies.manage -->
<!-- links: employers.new, employers.index -->
Open **Jobs**, choose **Employers**, then **Add employer**. Enter the company name, sector, contact person and where they are. Each employer page lists their vacancies and the learners working there.
Mark an employer **inactive** when you stop working with them. Their history stays.

## How do I post a vacancy?
<!-- keywords: vacancy, job, job opening, post a job, advertise, opening, position, recruit -->
<!-- permission: vacancies.manage -->
<!-- links: jobs.new, jobs.index -->
<!-- suggest -->
Open **Jobs** and choose **New vacancy**, or **Post a vacancy** on the employer's page. Give the job title, type of work, number of positions, region and pay. Tick the **required competencies** from your frameworks so the best-skilled learners rank first.
It's saved as a draft. Set the status to **Open** when you're ready. Untick **Learners can see this job and apply** if you'd rather put people forward yourself.

## How do I find the right learners for a job?
<!-- keywords: match, matching, candidates, shortlist, best learners, suggest learners, nominate, put forward, recommend -->
<!-- permission: applicants.manage -->
<!-- links: jobs.index -->
Open the vacancy and choose **Find candidates**. Learners are ranked by fit: the required competencies they've been assessed competent in, training in the job's trade, and living in the job's region. People already in work are hidden unless you tick **Include people already in work**.
Tick the people you want and choose **Put forward**. They join the applicants list as **Nominated**.

## What do the application stages mean?
<!-- keywords: application, stage, status, shortlisted, interview, offer, hired, not selected, withdrawn, pipeline -->
<!-- permission: vacancies.view -->
<!-- links: jobs.index -->
**Applied** (the learner applied) or **Nominated** (you put them forward) → **Shortlisted** → **Interview** → **Offer made** → **Hired**. **Not selected** and **Withdrawn** close an application.
Shortlisting shares the candidate, including their phone and email, with the employer's own team if they sign in. Set an interview from the applicant's page; the learner is emailed the time and place.

## How do I record a hire?
<!-- keywords: hire, hired, got the job, employed, start date, placement, job placement -->
<!-- permission: applicants.manage -->
<!-- links: jobs.index -->
Open the applicant from the vacancy's **Applicants** tab and fill in **Hire**: job title, type of work, start date and pay. This records the placement, adds the job to the learner's employment history and schedules checks at 3, 6 and 12 months. When every position is filled, the vacancy is marked **Filled**.

## How do I record a learner who found work or started a business?
<!-- keywords: self-employed, own business, entrepreneur, found a job, apprenticeship, internship, record placement -->
<!-- permission: placements.manage -->
<!-- links: placements.new, placements.index -->
Open **Placements** and choose **Record a placement**, or use the button on the learner's Employment tab. Choose an employer from your list, or type the business name for jobs elsewhere and the learner's own business. For a business, enter their usual monthly earnings as pay.

## What are follow-up checks?
<!-- keywords: follow up, follow-up, tracer, tracer study, retention, still employed, 3 months, 6 months, 12 months, outcome -->
<!-- permission: placements.manage -->
<!-- links: placements.index -->
<!-- suggest -->
Every placement gets checks at 3, 6 and 12 months to see whether the person is still in work and what they earn. Checks that are due or overdue are listed at the top of **Placements** and on the dashboard.
Open the placement and record the check. If they're no longer in that work, the placement ends on the date of the check and later checks are cancelled.

## How can an employer's staff use SkillBridge?
<!-- keywords: employer account, employer login, employer access, partner access, invite employer, hr manager -->
<!-- permission: members.manage -->
<!-- links: employers.index -->
On the employer's page, under **Their team on SkillBridge**, enter the person's email and choose **Invite to sign in**. Once they accept, they see only that employer's vacancies, the candidates you've shortlisted for them, and their placements. They can post vacancies, set interviews, make offers and record hires. They never see your learner records or other employers.
Someone who already has the Employer role can be linked with **Link** instead.

## How do I apply for a job?
<!-- keywords: apply, job, jobs for me, find work, my applications, vacancy, cover letter, withdraw -->
<!-- permission: self.jobs -->
<!-- links: myjobs.index -->
<!-- suggest -->
Open **Jobs for me**. Each open job shows how well your skills, training and region fit it. Open a job, add a short note about why you're a good fit if you like, and choose **Send application**. Your profile, assessed skills and certificates are shared with the centre automatically.
Your applications and their stage, including interview times, are listed at the top. You can withdraw while an application is still open.

## How do I see reports and indicators?
<!-- keywords: report, reports, m&e, monitoring, evaluation, indicators, statistics, figures, numbers, donor, funder, results, dashboard, charts -->
<!-- permission: reports.view -->
<!-- links: reports.index -->
<!-- suggest -->
Open **Reports**. Choose a period (or tap a quick period such as **This quarter**) and, if you like, a programme. **More filters** narrows it to a gender, region, disability or beneficiary group. The page shows the results chain (registered, enrolled, completed, competent, certified, placed in work), the main rates, a month-by-month chart, and breakdowns by gender, youth, disability, age, region and programme.
Every chart has the same figures as a table underneath. **How each figure is worked out** at the bottom explains each number.

## How do I export a donor report?
<!-- keywords: export report, pdf report, excel report, donor report, funder report, download report, spreadsheet, xlsx, quarterly report -->
<!-- permission: reports.export -->
<!-- links: reports.index -->
On **Reports**, set the period and filters, then choose **PDF report** for a print-ready summary, or **Excel** for a workbook. The workbook has sheets for the summary, groups, programmes, months, employment and a list of the learners counted.
The Excel learner list includes names, so share it carefully. Exports are recorded in the activity log.

## What do the rates on the report mean?
<!-- keywords: completion rate, attendance rate, placement rate, competence rate, retention rate, definition, how calculated, percentage -->
<!-- permission: reports.view -->
<!-- links: reports.index -->
**Completion rate**: of enrolments that started in the period and have finished, the share completed. **Attendance rate**: present or late out of all marks for sessions in the period (excused absences left out). **Competence rate**: of learners assessed in the period, the share judged competent. **Placement rate**: of learners who completed in the period, the share who started work or a business after enrolling. **Still in work**: of follow-up checks recorded in the period, the share where the person was still working.
Youth means aged 15 to 35 at the end of the period.

## When does my free trial end?
<!-- keywords: trial, free trial, days left, expire, ends, how long -->
<!-- permission: billing.view -->
<!-- links: workspace.subscription, workspace.dashboard -->
<!-- suggest -->
Your dashboard shows how many trial days are left. **Subscription** shows the exact end date.
We email a reminder 3 days before it ends. When the trial ends without payment, the workspace pauses, but your records are kept safely. Paying under **Subscription** reopens it straight away.

## How do I pay or renew my subscription?
<!-- keywords: pay, payment, renew, momo, mobile money, mtn, telecel, airteltigo, card, visa, mastercard, paystack, subscription, price, bill -->
<!-- permission: billing.manage -->
<!-- links: workspace.subscription -->
<!-- suggest -->
Open **Subscription** and choose **Pay now** (or **Pay for another month/year**). Check the amount, enter the email for receipts, and choose **Pay with Paystack**. On Paystack's secure page, pay with Mobile Money (MTN, Telecel, AirtelTigo) or a card. You come back here once it's done, and your paid period starts as soon as Paystack confirms.
Paying early never loses days: a renewal starts when the current period ends. If you pay by card, it can be saved for automatic renewals.

## How do I change my plan?
<!-- keywords: change plan, upgrade, downgrade, bigger plan, smaller plan, cheaper, yearly, annual, monthly, switch plan -->
<!-- permission: billing.manage -->
<!-- links: workspace.subscription -->
Under **Subscription**, choose another plan. Moving **up** (or from monthly to yearly) starts straight away, and the unused part of what you've already paid is taken off the price. Moving **down** costs nothing now: the change happens when your current period ends. Choose **Keep** to undo it before then.
You can't move to a plan that's smaller than what you already use (for example more staff than it allows). Remove some first. Enterprise is arranged with our sales team.

## What happens if a payment fails?
<!-- keywords: payment failed, declined, card declined, insufficient funds, past due, payment due, grace period, retry -->
<!-- permission: billing.view -->
<!-- links: workspace.subscription, billing.invoices -->
If an automatic renewal is declined, we email you and try again the next day, up to three times. Meanwhile your workspace keeps working for a 7-day grace period. Pay under **Subscription** at any time to settle it. With Mobile Money, or no saved card, we email a reminder 3 days before your period ends instead.

## Where are my invoices and receipts?
<!-- keywords: invoice, receipt, proof of payment, download receipt, pdf, payment history, tax, accounts -->
<!-- permission: billing.view -->
<!-- links: billing.invoices -->
**Subscription → Invoices** lists every payment. Open one and choose **PDF** to download the invoice, or the receipt once it's paid. Paystack also emails a receipt to your billing email.

## How do I stop automatic renewal or remove my card?
<!-- keywords: auto renew, automatic renewal, cancel subscription, stop paying, remove card, delete card, saved card, unsubscribe -->
<!-- permission: billing.manage -->
<!-- links: workspace.subscription -->
Under **Subscription**, choose **Turn off automatic renewal**. Your plan then ends when the paid period ends, and you can turn it back on before then. To remove a saved card, choose **Remove** next to it. Renewals then need paying by hand, and we email a reminder before each one.

## What are plan limits?
<!-- keywords: limit, plan limit, how many, maximum, staff users, learners, storage, upgrade, full -->
<!-- permission: billing.view -->
<!-- links: workspace.subscription, workspace.people -->
Each plan allows a number of staff users, learners, courses, programmes, vacancies and storage. **Subscription** shows how much you're using.
Pending invitations count towards the limits. If you reach a limit, choose a bigger plan under **Subscription**. The upgrade starts as soon as you pay.

## My workspace says it is paused or suspended
<!-- keywords: paused, suspended, blocked, ended, expired, locked out, cannot open workspace -->
A workspace pauses when its subscription has ended, or when the PORTIA SkillBridge team has suspended it. Your records are always kept.
The owner, or anyone who manages billing, can still open **Subscription** to see what's needed. Contact Kayron Global Tech Services to renew or to ask why it was suspended.

## How do I change the workspace details?
<!-- keywords: settings, workspace name, address, contact details, website, registration number, organization details -->
<!-- permission: workspace.settings -->
<!-- links: workspace.settings -->
Open **Settings** to change the workspace name, organization type, contact email and phone, website, registration number and address. Choose **Save changes** when you're done. Every change is recorded in the activity log.

## Where can I see who did what?
<!-- keywords: activity, audit, log, history, who changed, track, record -->
<!-- permission: audit.view -->
<!-- links: workspace.activity -->
Open **Activity** to see the latest 200 actions in this workspace: who did what, and when.

## What are notifications?
<!-- keywords: notification, notifications, bell, alerts, updates, unread, news, messages -->
<!-- links: notifications.index -->
<!-- suggest -->
The bell at the top of every page shows things that need your attention: a new job application, a shortlisting or interview, a marked assignment, a new certificate, a payment, someone joining. A red number means unread ones. Tap an item to go straight to it, or choose **Mark all as read**.
You only see what concerns you in the workspace you're in.

## How do I choose which emails I get?
<!-- keywords: email notifications, stop emails, too many emails, unsubscribe, email settings, notification settings -->
<!-- links: account.profile -->
Open **My account** and find **Notifications**. Untick the kinds you don't want by email, then choose **Save email choices**. Everything still appears under the bell. Payment receipts and password emails are always sent.

## How do I turn on two-step sign-in?
<!-- keywords: two-step, two factor, 2fa, mfa, authenticator, google authenticator, security code, protect account -->
<!-- links: account.profile -->
Open **My account** → **Two-step sign-in** → **Set up two-step sign-in**. Scan the QR code with an authenticator app (Google Authenticator, Microsoft Authenticator, Authy and others), then type the 6-digit code it shows. Save the eight recovery codes somewhere safe. From then on you'll enter a code from the app after your password.

## I can't get my sign-in code
<!-- keywords: lost phone, new phone, recovery code, can't sign in code, authenticator lost, code not working -->
<!-- links: auth.signin -->
Check your phone's time is set automatically, then use the newest code. If you don't have your phone, enter one of your recovery codes instead (like ABCD-EFGH); each works once. If you have none left, ask PORTIA support to switch two-step sign-in off for you, then set it up again on your new phone.

## How do I install the app on my phone?
<!-- keywords: install, app, download app, home screen, add to home screen, pwa, icon, android app, iphone app, desktop app -->
<!-- suggest -->
**Android (Chrome):** open the PORTIA SkillBridge website, sign in, then open the menu (☰) and choose **Install app**. Or use Chrome's ⋮ menu → **Install app** / **Add to Home screen**.
**iPhone or iPad (Safari):** tap the **Share** button (the square with an arrow), scroll down, choose **Add to Home Screen**, then **Add**.
**Computer (Chrome or Edge):** choose **Install app** in the menu, or the install icon at the right of the address bar.
The app opens full-screen from its own icon and always shows your latest information. It needs the internet: nothing private is stored on the phone.

## How do I use dark mode?
<!-- keywords: dark mode, light mode, theme, night, colours, colors, brightness -->
Choose the **Dark mode** or **Light mode** button at the top of any page. Your choice is remembered on this device. Until you choose, the app follows your phone or computer setting.

## Can I use PORTIA SkillBridge on my phone?
<!-- keywords: phone, mobile, android, iphone, tablet, small screen -->
Yes. It works in any modern phone browser, with a bottom bar for your main pages and the full menu behind ☰. You can also install it so it opens from its own icon, like an app (see **How do I install the app on my phone?**).

## How do I create a workspace for a customer?
<!-- keywords: create workspace, new workspace, new organization, new customer, onboard, set up client -->
<!-- permission: platform -->
<!-- links: admin.tenant_new -->
In the Super Admin console, open **Workspaces** and choose **New workspace**. Fill in the details, pick the plan, and enter the owner's name and email. The owner gets an invitation by email.

## How do I record a payment received outside the app?
<!-- keywords: record payment, activate, mobile money received, bank transfer, extend trial, change customer plan, cancel subscription -->
<!-- permission: platform -->
<!-- links: admin.tenants -->
In the Super Admin console, open the workspace and use **Record a payment**. Choose the plan and billing, enter the amount, how it was paid and the reference, then choose **Record payment**. It starts or extends the paid period like an online payment, and the workspace gets a receipt. **Payments** in the console lists every payment across workspaces.
Use **Manage subscription** to extend a trial, change the plan or cancel.

## How do I help someone who is locked out?
<!-- keywords: unlock, locked out, reset someone's password, deactivate account, super administrator, make admin -->
<!-- permission: platform -->
<!-- links: admin.users -->
In the Super Admin console, open **People** and find the person. From their page you can clear a sign-in lock, email them a password reset link, deactivate or reactivate their account, and grant or remove Super Administrator access.

## How do I change plan prices or limits?
<!-- keywords: edit plan, price change, plan limits, pricing page, hide plan -->
<!-- permission: platform -->
<!-- links: admin.plans -->
In the Super Admin console, open **Plans** and choose a plan. Change its prices, trial length, limits or features, and whether it shows on the pricing page. New prices apply to new subscriptions and at each subscriber's next renewal.

## How do backups work?
<!-- keywords: backup, backups, restore, disaster recovery, database copy, encryption key, data loss -->
<!-- permission: platform -->
<!-- links: admin.backups_page, admin.system_status -->
Every night the whole database is copied, encrypted with BACKUP_ENCRYPTION_KEY and saved in the storage bucket. Backups are kept for 30 days, and the newest 7 are always kept. **Backups** in the console lists them, lets you **Back up now**, and downloads them (still encrypted). Keep a copy of the key away from Render: backups can't be opened without it.
To restore: create an empty database, run `flask db upgrade`, set the same key, then run `flask backup verify NAME` and `flask backup restore NAME`.

## What does System status show?
<!-- keywords: system status, health, go live, checklist, configuration, is everything working, cron, jobs -->
<!-- permission: platform -->
<!-- links: admin.system_status -->
**System status** checks the things a live site needs: the database is up to date, uploads go to a storage bucket, email is set up, Paystack's mode, the public address, recent backups, that the daily, notification and backup jobs are running, and that every Super Administrator uses two-step sign-in. Red items need fixing; amber ones need checking.
