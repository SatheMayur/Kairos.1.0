/**
 * Apna applicant sync — runs on the owner's always-on PC.
 *
 * Goal: "connect Apna" as closely as Apna allows. Apna offers no official API,
 * so this signs into the owner's own account in a real browser and pulls
 * applicants. After a ONE-TIME manual sign-in, it needs zero configuration:
 * it reads the jobs list, opens each job's applicants, downloads the Excel,
 * and uploads it to the recruitment system (which matches/creates the role and
 * de-dups automatically).
 *
 *   node sync.js --login   → sign in to Apna once by hand (password + OTP).
 *   node sync.js           → discover jobs + import applicants, then repeat on a timer.
 */
const fs = require("fs");
const path = require("path");

const SESSION_DIR = path.join(__dirname, "session");
const DOWNLOAD_DIR = path.join(__dirname, "downloads");
const CONFIG_PATH = path.join(__dirname, "config.json");

// Confirmed from the Apna applicants-page screenshots:
const EXPORT_BUTTON = "text=Download Excel";
const ALL_CANDIDATES_TAB = "text=All candidates";

function loadConfig() {
  if (!fs.existsSync(CONFIG_PATH)) {
    console.error("\n  Your settings file is missing.");
    console.error("  Fix: copy 'config.example.json' to 'config.json' in this folder, then run again.");
    console.error("  (The launcher start-apna-sync.bat does this for you automatically.)\n");
    process.exit(1);
  }
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
  } catch (e) {
    console.error("\n  Your settings file 'config.json' could not be read — it looks damaged.");
    console.error("  Fix: delete config.json, copy config.example.json to config.json again, and re-run.");
    console.error("  (Technical detail for your developer: " + e.message + ")\n");
    process.exit(1);
  }
}

const log = (msg) => console.log(`[${new Date().toLocaleString()}] ${msg}`);

// Load Playwright, but if Chromium/Playwright isn't installed give a plain-English
// message instead of a raw stack trace. Returns the chromium handle.
function loadChromium() {
  try {
    return require("playwright").chromium;
  } catch (e) {
    console.error("\n  The browser engine this helper needs isn't installed yet.");
    console.error("  Fix: close this window and double-click 'start-apna-sync.bat' — it installs it for you the first time.");
    console.error("  (If you are running by hand, run:  npm install  then  npx playwright install chromium )\n");
    process.exit(1);
  }
}

// Turn common low-level errors into a plain-English explanation for the owner.
// `where` is a short phrase like "while talking to the HR system".
function explainError(e, cfg) {
  const msg = (e && e.message) || String(e);
  // Browser/Chromium not installed or won't launch.
  if (/Executable doesn't exist|playwright install|browserType\.launch|Failed to launch/i.test(msg)) {
    return "The browser engine could not start. Close this window and double-click 'start-apna-sync.bat' so it can install it for you (or run: npx playwright install chromium).";
  }
  // Session expired / not logged in.
  if (/Session expired|not signed in|login|signin/i.test(msg)) {
    return "Your Apna sign-in has expired. Run the sign-in step again:  node sync.js --login";
  }
  // Backend unreachable (fetch failed / DNS / connection refused).
  if (/fetch failed|ECONNREFUSED|ENOTFOUND|EAI_AGAIN|network|getaddrinfo|ETIMEDOUT/i.test(msg)) {
    const url = cfg ? cfg.backendUrl : "(the HR system address in config.json)";
    return "Could not reach the HR system at " + url + ". Check that the address in config.json is correct and that this PC has internet, then try again.";
  }
  return msg;
}

// ── Capture the portal's own candidate API (for the JSON integration) ──────
// Arms a network listener, lets you click into a candidate list, and saves the
// matching request+response to api-capture.json — with secret header VALUES
// redacted, so the file is safe to share. The assistant uses it to build the
// real API mapping; your token stays on this PC.
async function captureApi(cfg) {
  const chromium = loadChromium();
  const ctx = await chromium.launchPersistentContext(SESSION_DIR, { headless: false, acceptDownloads: true });
  const page = ctx.pages()[0] || (await ctx.newPage());

  const SECRET_HEADERS = ["authorization", "raven-token", "x-csrf-token", "cookie"];
  const redact = (headers) => {
    const out = {};
    for (const [k, v] of Object.entries(headers || {})) {
      out[k] = SECRET_HEADERS.includes(k.toLowerCase()) ? "<redacted>" : v;
    }
    return out;
  };

  const captured = [];
  page.on("response", async (resp) => {
    const url = resp.url();
    if (!/cerebro\/api|white-collar-search|applicant|candidate/i.test(url)) return;
    try {
      const req = resp.request();
      let body = null;
      try { body = await resp.json(); } catch { /* non-JSON */ }
      captured.push({
        url,
        method: req.method(),
        request_headers: redact(req.headers()),
        request_payload: req.postData() || null,
        status: resp.status(),
        response_sample: body, // saved for schema mapping
      });
      log(`captured: ${req.method()} ${url.split("?")[0]} (${resp.status()})`);
    } catch { /* ignore */ }
  });

  await page.goto(cfg.jobsUrl, { waitUntil: "domcontentloaded" });
  console.log("\n  → Click 'Database', or open a job's applicants, so a candidate list loads.");
  console.log("    When you see candidates on screen, come back here and press ENTER.\n");
  await new Promise((resolve) => process.stdin.once("data", resolve));

  // Trim large responses to the first 2 candidates so the file stays small/shareable.
  for (const c of captured) {
    const r = c.response_sample;
    if (r && Array.isArray(r.results)) r.results = r.results.slice(0, 2);
    else if (r && Array.isArray(r.data)) r.data = r.data.slice(0, 2);
    else if (Array.isArray(r)) c.response_sample = r.slice(0, 2);
  }
  fs.writeFileSync(path.join(__dirname, "api-capture.json"), JSON.stringify(captured, null, 2));
  await ctx.close();
  log(`Saved ${captured.length} call(s) to api-capture.json — secrets redacted. Send me that file.`);
}

// ── One-time manual login ──────────────────────────────────────────────────
async function doLogin(cfg) {
  const chromium = loadChromium();
  log("Opening Apna login. Sign in by hand (password + OTP if asked).");
  const ctx = await chromium.launchPersistentContext(SESSION_DIR, { headless: false, acceptDownloads: true });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto(cfg.loginUrl, { waitUntil: "domcontentloaded" });
  console.log("\n  → Finish logging in, then come back here and press ENTER.\n");
  await new Promise((resolve) => process.stdin.once("data", resolve));
  await ctx.close();
  log("Session saved. You can now run:  node sync.js");
}

// ── Upload a downloaded Excel to the recruitment system ────────────────────
async function uploadFile(cfg, filePath, jobTitle) {
  const buf = fs.readFileSync(filePath);
  const form = new FormData(); // Node 18+ global
  form.append("file", new Blob([buf]), path.basename(filePath));
  form.append("job_title", jobTitle || "Apna applicants");
  form.append("source", "APNA");
  form.append("auto_outreach", "true");
  const url = `${cfg.backendUrl.replace(/\/$/, "")}/api/v1/import/apna`;
  let res;
  try {
    res = await fetch(url, { method: "POST", body: form });
  } catch (e) {
    // Network-level failure (no internet / wrong address / site down).
    throw new Error(`Could not reach the HR system at ${url} — check the address in config.json and this PC's internet. (${e.message})`);
  }
  const text = await res.text();
  if (!res.ok) throw new Error(`import failed HTTP ${res.status}: ${text.slice(0, 200)}`);
  return JSON.parse(text);
}

// ── Sourcing: scrape candidate cards from an Apna results page ──────────────
// Parses the visible card text (built from the applicants/Database card layout):
//   "Name … M, 60 yr  15yrs 5mos  ₹ 17,500 / mos  Udhna, Surat, GJ
//    Current / Latest: <role> at <employer> …  Skills: …  Education: …"
function parseCard(text) {
  const lines = text.split("\n").map((s) => s.trim()).filter(Boolean);
  const cand = { name: (lines[0] || "").replace(/view full profile.*/i, "").trim(), source: "APNA", skills: [] };
  for (const ln of lines) {
    // Experience uses the plural "Xyrs" form so we never mistake the age ("60 yr").
    const exp = ln.match(/(\d+)\s*yrs(?:\s*(\d+)\s*mos)?/i);
    if (exp && cand.experience_years === undefined) cand.experience_years = Number(exp[1]) + (exp[2] ? Number(exp[2]) / 12 : 0);
    const sal = ln.match(/₹\s*([\d,]+)\s*\/\s*mos/i);
    if (sal) cand.current_salary = Number(sal[1].replace(/,/g, "")) * 12; // monthly → annual
    const loc = ln.match(/\/\s*mos\s*\|?\s*([A-Za-z][A-Za-z .,'’-]+)$/);
    if (loc) cand.location = loc[1].trim();
    // Labelled rows: match only lines that START with the label (skips the
    // "Matching: … Skills Education …" badge row, which starts with "Matching").
    if (/^Current\b/i.test(ln) && !cand.current_role) {
      const m = ln.replace(/^Current\s*\/?\s*Latest[:\s]*/i, "").match(/^(.+?)\s+at\s+(.+?)(?:\s+[A-Z][a-z]{2,}\s*\d{4}|\s*\||$)/i);
      if (m) { cand.current_role = m[1].trim(); cand.current_employer = m[2].trim(); }
    }
    if (/^Skills\b/i.test(ln)) {
      cand.skills = ln.replace(/^Skills[:\s]*/i, "").split(/[,•]/).map((s) => s.trim()).filter(Boolean).slice(0, 15);
    }
  }
  // Fallback for location when it's on its own line (e.g. "Surat, GJ").
  if (!cand.location) {
    const loc = lines.find((l) =>
      l.length < 40 &&
      /^[A-Za-z][\w .'’-]*(,\s*[A-Za-z .'’-]+){1,3}$/.test(l) &&
      !/^(skills|current|education|language|previous|matching)/i.test(l)
    );
    if (loc) cand.location = loc.trim();
  }
  return cand;
}

async function scrapeCandidates(page) {
  const blocks = await page.evaluate(() => {
    const leaves = [...document.querySelectorAll("*")].filter(
      (e) => /view full profile/i.test(e.textContent || "") && e.querySelectorAll("*").length <= 4
    );
    const seen = new Set(), out = [];
    for (const n of leaves) {
      let card = n;
      for (let i = 0; i < 10 && card; i++) {
        card = card.parentElement;
        if (card && /yr/i.test(card.innerText || "") && /(skills|education)/i.test(card.innerText || "")) break;
      }
      if (card && !seen.has(card)) { seen.add(card); out.push(card.innerText); }
    }
    return out;
  });
  return blocks.map(parseCard).filter((c) => c.name);
}

async function postCandidates(cfg, jobTitle, candidates) {
  const url = `${cfg.backendUrl.replace(/\/$/, "")}/api/v1/import/apna-candidates`;
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_title: jobTitle, candidates }),
    });
  } catch (e) {
    // Network-level failure (no internet / wrong address / site down).
    throw new Error(`Could not reach the HR system at ${url} — check the address in config.json and this PC's internet. (${e.message})`);
  }
  const text = await res.text();
  if (!res.ok) throw new Error(`source import failed HTTP ${res.status}: ${text.slice(0, 200)}`);
  return JSON.parse(text);
}

async function sourceFromApna(page, cfg) {
  for (const search of cfg.databaseSearches || []) {
    try {
      log(`Sourcing "${search.jobTitle}" — opening search…`);
      await page.goto(search.url, { waitUntil: "domcontentloaded" });
      if (/login|signin/i.test(page.url())) throw new Error("Session expired — run:  node sync.js --login");
      await page.waitForTimeout(3000); // let results render
      const cands = await scrapeCandidates(page);
      if (!cands.length) {
        log(`"${search.jobTitle}": no candidate cards found on this page. Apna may have changed how the page looks. Please take a screenshot of the search results and send it to me so I can fix it.`);
        continue;
      }
      const r = await postCandidates(cfg, search.jobTitle, cands);
      log(`"${search.jobTitle}": scraped ${cands.length} → ${r.inserted} new, ${r.duplicates_skipped} already had, ${r.auto_shortlisted} shortlisted.`);
    } catch (e) {
      // One bad search must never stop the others.
      log(`Could not source "${search.jobTitle}": ${explainError(e, cfg)}`);
    }
  }
}

// ── Find every job that has applicants, with its title ─────────────────────
// Best-effort discovery: follow links that point at an applicants/candidates
// page and grab the nearest heading as the job title. Confirmed/tuned on the
// first real run against the live portal.
async function discoverJobs(page, cfg) {
  await page.goto(cfg.jobsUrl, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  return page.$$eval('a[href*="applicant"], a[href*="candidate"]', (els) => {
    const seen = new Set();
    const out = [];
    for (const a of els) {
      const url = a.href;
      if (!url || seen.has(url)) continue;
      seen.add(url);
      let node = a, title = "";
      for (let i = 0; i < 6 && node; i++) {
        node = node.parentElement;
        if (!node) break;
        const h = node.querySelector('h1,h2,h3,h4,[class*="title"],[class*="Title"]');
        if (h && h.textContent.trim()) { title = h.textContent.trim(); break; }
      }
      out.push({ url, title });
    }
    return out;
  });
}

async function importJob(page, cfg, job) {
  log(`"${job.title || job.url}": opening applicants…`);
  await page.goto(job.url, { waitUntil: "domcontentloaded" });
  if (/login|signin/i.test(page.url())) throw new Error("Session expired — run:  node sync.js --login");

  // Export downloads only the visible tab, so switch to "All candidates" first.
  try {
    await page.click(ALL_CANDIDATES_TAB, { timeout: 8000 });
    await page.waitForTimeout(1500);
  } catch { /* fall through — export whatever is shown */ }

  const downloadPath = path.join(DOWNLOAD_DIR, `apna-${Date.now()}.xlsx`);
  let download;
  try {
    [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 30000 }),
      page.click(EXPORT_BUTTON, { timeout: 10000 }),
    ]);
  } catch {
    log(`"${job.title || job.url}": no Download Excel button here — skipping.`);
    return;
  }
  await download.saveAs(downloadPath);

  const r = await uploadFile(cfg, downloadPath, job.title);
  log(`"${job.title}": ${r.inserted} new, ${r.duplicates_skipped} already had, ${r.auto_shortlisted} shortlisted.`);
  fs.unlinkSync(downloadPath);
}

async function runOnce(cfg) {
  const chromium = loadChromium();
  let ctx;
  try {
    ctx = await chromium.launchPersistentContext(SESSION_DIR, { headless: true, acceptDownloads: true });
  } catch (e) {
    log("Could not start the browser: " + explainError(e, cfg));
    return;
  }
  const page = ctx.pages()[0] || (await ctx.newPage());
  try {
    // 1) Source candidates from Apna's Database search (the main engine).
    // sourceFromApna already guards each search on its own.
    if ((cfg.databaseSearches || []).length) await sourceFromApna(page, cfg);

    // 2) Also pull anyone who applied to existing job posts (free, no credits).
    // Guard this whole step so a problem here never stops the run or skips cleanup.
    let jobs = [];
    try {
      jobs = await discoverJobs(page, cfg);
    } catch (e) {
      log("Could not read your Apna jobs page: " + explainError(e, cfg));
    }
    if (!jobs.length) {
      log("No applicant links found on the jobs page (that's fine if you only source from the Database).");
    } else {
      log(`Found ${jobs.length} job(s) with an applicant list. Importing…`);
      for (const job of jobs) {
        // One failing job must never stop the rest.
        try { await importJob(page, cfg, job); }
        catch (e) { log(`Could not import "${job.title || job.url}": ${explainError(e, cfg)}`); }
      }
    }
  } finally {
    try { await ctx.close(); } catch { /* already closed */ }
  }
}

async function main() {
  const cfg = loadConfig();
  if (process.argv.includes("--login")) { await doLogin(cfg); process.exit(0); }
  if (process.argv.includes("--capture")) { await captureApi(cfg); process.exit(0); }
  if (!fs.existsSync(SESSION_DIR) || fs.readdirSync(SESSION_DIR).length === 0) {
    console.error("\n  You are not signed in to Apna yet.");
    console.error("  Fix: run the one-time sign-in step first:  node sync.js --login");
    console.error("  (The launcher start-apna-sync.bat does this for you automatically.)\n");
    process.exit(1);
  }
  fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });

  // Never let a single run crash the whole process — explain and carry on.
  await runOnce(cfg).catch((e) => log("This sync had a problem: " + explainError(e, cfg)));
  const hours = Number(cfg.intervalHours || 0);
  if (hours > 0) {
    log(`Next sync in ${hours}h. Leave this window open.`);
    setInterval(() => runOnce(cfg).catch((e) => log("This sync had a problem: " + explainError(e, cfg))), hours * 3600 * 1000);
  } else {
    log("Done (one-time run because the schedule is set to 0). You can close this window.");
    process.exit(0);
  }
}

main().catch((e) => {
  // Last-resort safety net: still plain English, no raw stack trace.
  console.error("\n  Sorry — the helper hit a problem it could not recover from:");
  console.error("  " + explainError(e));
  console.error("  If this keeps happening, send me a screenshot of this window.\n");
  process.exit(1);
});
