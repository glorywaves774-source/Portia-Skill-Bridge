"""Layout, dark-mode and CSP checks across every page (Playwright + Chromium).

    python scripts/ui_check.py http://localhost:5000 [--shots DIR] \
        [--as admin@example.com:password] [--as orgadmin@example.com:password]

Each --as signs in as that person and also checks every page their menu offers.

Checks, per page and width (320, 360, 390, 402, 440, 1366):
  * no sideways scrolling, nothing sticking out of the viewport
    (wide tables may scroll inside their own .table-wrap box)
  * no console errors (CSP violations show up here)
  * dark mode: no light patches and no text below WCAG 4.5:1 contrast
Chrome phone emulation is not real Safari: confirm on a real iPhone too.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

WIDTHS = [(320, 640), (360, 780), (390, 844), (402, 874), (440, 956), (1366, 900)]

OVERFLOW_JS = """
() => {
  const vw = document.documentElement.clientWidth, bad = [];
  if (document.documentElement.scrollWidth > vw + 1) bad.push('page scrolls sideways: ' + document.documentElement.scrollWidth + ' > ' + vw);
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('.table-wrap, .sidebar, .icon-sprite, .visually-hidden, .skip-link, .drawer-scrim')) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (r.right > vw + 1 || r.left < -1) bad.push((el.className || el.tagName) + ' x=' + Math.round(r.left) + '..' + Math.round(r.right));
  }
  return bad.slice(0, 8);
}
"""

CONTRAST_JS = """
() => {
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1}; };
  const lum = c => { const f = v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4); }; return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b); };
  const blend = (top, under) => ({r: top.r*top.a+under.r*(1-top.a), g: top.g*top.a+under.g*(1-top.a), b: top.b*top.a+under.b*(1-top.a), a:1});
  const bodyBg = parse(getComputedStyle(document.body).backgroundColor) || {r:0,g:0,b:0,a:1};
  const bgOf = el => { const stack=[]; for (let n=el; n && n!==document.documentElement; n=n.parentElement){ const s=getComputedStyle(n); if (s.backgroundImage && s.backgroundImage.includes('gradient') && !n.classList.contains('backdrop')) return null; const c=parse(s.backgroundColor); if (c && c.a>0) stack.push(c); if (c && c.a>=1) break; } let acc = bodyBg; for (let i=stack.length-1;i>=0;i--) acc = blend(stack[i], acc); return acc; };
  const issues = [], light = [];
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if (s.display==='none' || s.visibility==='hidden') continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    if (el.closest('.sidebar') && !el.closest('.sidebar.is-open') && innerWidth <= 760) continue;
    const own = parse(s.backgroundColor);
    if (own && own.a > 0.5 && lum(own) > 0.6 && r.width*r.height > 400 && !el.closest('.icon-sprite, .keep-light')) light.push(el.tagName+'.'+el.className);
    const hasText = [...el.childNodes].some(n => n.nodeType===3 && n.textContent.trim());
    if (!hasText) continue;
    if (s.color === 'rgba(0, 0, 0, 0)') continue; // gradient text
    const fg = parse(s.color), bg = bgOf(el); if (!fg || !bg) continue;
    const L1 = lum(blend(fg,bg)), L2 = lum(bg), ratio = (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
    if (ratio < 4.5) issues.push(el.tagName+'.'+el.className+' "'+el.textContent.trim().slice(0,30)+'" '+ratio.toFixed(2));
  }
  return {issues: issues.slice(0,10), light: light.slice(0,10)};
}
"""


PUBLIC_PAGES = ["/", "/pricing", "/pricing?for=individual&billing=annual", "/signin", "/signup",
                "/signup?for=individual", "/forgot-password", "/verify", "/verify/AAAAA-BBBBB", "/missing-page", "/offline"]


def _sign_in(browser, base, email, password):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(base + "/signin")
    page.fill("#email", email)
    page.fill("#password", password)
    page.click("form.auth-form button[type=submit]")
    page.wait_for_load_state("networkidle")
    links = page.locator("#nav-list a[href^='/']").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    extra = []
    # Also check one detail page behind each list page.
    for listing, selector in [("/admin/tenants", "a[href*='/admin/tenants/PSB-']"),
                              ("/admin/tenants", "a[href='/admin/tenants/new']"),
                              ("/admin/users", "a[href^='/admin/users/']"),
                              ("/admin/plans", "a[href$='/edit']"),
                              ("/app/people", "a[href^='/app/people/']"),
                              ("/app/learners/", "a[href='/app/learners/new']"),
                              ("/app/learners/", "a.person-cell"),
                              ("/app/programmes/", "table a[href^='/app/programmes/']"),
                              ("/app/programmes/", "a[href='/app/programmes/new']"),
                              ("/app/courses/", "a.course-card"),
                              ("/app/courses/", "a[href='/app/courses/new']"),
                              ("/app/learn/", "a.course-card"),
                              ("/app/competencies/", "table a[href^='/app/competencies/']"),
                              ("/app/competencies/", "a[href='/app/competencies/new']"),
                              ("/app/assessments/", "table a[href^='/app/assessments/']"),
                              ("/app/assessments/", "a[href='/app/assessments/new']"),
                              ("/app/assessments/", "a[href='/app/assessments/gaps']"),
                              ("/app/certificates/", "table a[href^='/app/certificates/']"),
                              ("/app/employers/", "table a[href^='/app/employers/']"),
                              ("/app/employers/", "a[href='/app/employers/new']"),
                              ("/app/jobs/", "table a[href^='/app/jobs/']"),
                              ("/app/jobs/", "a[href='/app/jobs/new']"),
                              ("/app/placements/", "table a[href^='/app/placements/']"),
                              ("/app/placements/", "a[href='/app/placements/new']"),
                              ("/app/my-jobs/", "main a[href^='/app/my-jobs/']:not([href='/app/my-jobs/'])")]:
        if listing in links:
            page.goto(base + listing)
            detail = page.locator(selector).first
            if detail.count():
                extra.append(detail.get_attribute("href"))
    # Every tab of the first learner profile, and its edit page
    profile = next((e for e in extra if e and e.startswith("/app/learners/") and e.count("/") == 3
                    and not e.endswith("/new")), None)
    if profile:
        extra += [f"{profile}?tab={t}" for t in ("education", "skills", "employment", "documents")]
        if "/app/learners/new" in extra:
            extra.append(profile + "/edit")
    # Tabs and deeper pages for programmes, courses and learning
    for e in list(extra):
        if not e:
            continue
        if e.startswith("/app/programmes/") and e.count("/") == 3 and not e.endswith("/new"):
            extra += [f"{e}?tab={t}" for t in ("learners", "sessions", "courses")]
            page.goto(base + e + "?tab=sessions")
            reg = page.locator("a[href$='/attendance']").first
            if reg.count():
                extra.append(reg.get_attribute("href"))
            if "/app/programmes/new" in extra:
                extra += [e + "/edit", e + "/enrol"]
        if e.startswith("/app/courses/") and e.count("/") == 3 and not e.endswith("/new"):
            extra += [f"{e}?tab={t}" for t in ("learners", "submissions", "settings")]
            page.goto(base + e)
            for les in page.locator("a[href*='/lessons/']").evaluate_all("els => els.map(x => x.getAttribute('href'))")[:5]:
                extra.append(les)
        if e.startswith("/app/jobs/") and e.count("/") == 3 and not e.endswith("/new"):
            extra += [f"{e}?tab={t}" for t in ("applicants", "matches")]
            page.goto(base + e + "?tab=applicants")
            appl = page.locator("a[href^='/app/jobs/applications/']").first
            if appl.count():
                extra.append(appl.get_attribute("href"))
            if "/app/jobs/new" in extra:
                extra.append(e + "/edit")
        if e.startswith("/app/employers/") and e.count("/") == 3 and "/app/employers/new" in extra and not e.endswith("/new"):
            extra.append(e + "/edit")
        if e.startswith("/app/learn/courses/"):
            page.goto(base + e)
            for les in page.locator("a[href*='/lessons/']").evaluate_all("els => els.map(x => x.getAttribute('href'))")[:5]:
                extra.append(les)
            att = page.goto(base + e)
    if "/app/subscription" in links and "/app/billing/invoices" not in links:  # billing pages
        extra += ["/app/billing/invoices", "/app/billing/checkout?plan=org-business&cycle=annual",
                  "/app/billing/checkout?plan=org-starter&cycle=monthly",
                  "/app/billing/checkout?plan=org-enterprise&cycle=monthly"]
        page.goto(base + "/app/billing/invoices")
        first = page.locator("table a[href^='/app/billing/invoices/']").first
        if first.count():
            extra.append(first.get_attribute("href"))
    if "/admin/" in links:
        page.goto(base + "/admin/tenants")
        t = page.locator("table a[href^='/admin/tenants/']").first
        if t.count():
            extra.append(t.get_attribute("href"))
    if any(l.startswith("/app/") for l in links):  # the bell's page
        extra.append("/app/notifications/")
    extra.append("/account/two-step")
    if "/app/reports/" in links:  # filtered report (More filters open) and an empty period
        extra += ["/app/reports/?start=2025-06-01&gender=female&region=Greater+Accra",
                  "/app/reports/?start=2019-01-01&end=2019-12-31"]
    extra = [x for x in dict.fromkeys(extra) if x]
    state = ctx.storage_state()
    ctx.close()
    pages = [l for l in dict.fromkeys(links + extra) if l not in ("/", "/pricing")]
    return state, pages


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--shots", default=None)
    ap.add_argument("--as", dest="people", action="append", default=[])
    args = ap.parse_args()
    base = args.base.rstrip("/")
    shots = pathlib.Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        runs = [("visitor", None, PUBLIC_PAGES)]
        for spec in args.people:
            email, _, password = spec.partition(":")
            state, pages = _sign_in(browser, base, email, password)
            runs.append((email.split("@")[0], state, pages))

        for who, state, pages in runs:
            for theme in ("light", "dark"):
                for w, h in WIDTHS:
                    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2,
                                              is_mobile=w < 760, has_touch=w < 760, color_scheme=theme,
                                              storage_state=state)
                    page = ctx.new_page()
                    errors: list[str] = []
                    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    for path in pages:
                        errors.clear()
                        page.goto(base + path, wait_until="networkidle")
                        over = page.evaluate(OVERFLOW_JS)
                        real_errors = [e for e in errors if not (path in ("/missing-page", "/verify/AAAAA-BBBBB") and "404" in e)]
                        problems = []
                        if over:
                            problems.append(f"overflow: {over}")
                        if real_errors:
                            problems.append(f"console: {real_errors[:3]}")
                        if theme == "dark":
                            res = page.evaluate(CONTRAST_JS)
                            if res["issues"]:
                                problems.append(f"contrast: {res['issues']}")
                            if res["light"]:
                                problems.append(f"light patches: {res['light']}")
                        failures += bool(problems)
                        if problems:
                            print(f"FAIL {who} {theme:<5} {w:>4}px {path}" + "".join("\n      " + x for x in problems))
                        if shots and (w, h) in [(360, 780), (402, 874), (440, 956), (1366, 900)]:
                            name = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "home"
                            page.screenshot(path=str(shots / f"{who}_{name}_{theme}_{w}.png"))
                    ctx.close()
            print(f"checked {who}: {len(pages)} pages x {len(WIDTHS)} widths x 2 themes")
        browser.close()
    print(f"\n{failures} problem page/width combination(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
