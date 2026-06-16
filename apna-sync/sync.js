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
    console.error("\n  config.json not found. Copy config.example.json to config.json first.\n");
    process.exit(1);
  }
  return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
}

const log = (msg) => console.log(`[${new Date().toLocaleString()}] ${msg}`);

// ── Capture the portal's own candidate API (for the JSON integration) ──────
// Arms a network listener, lets you click into a candidate list, and saves the
// matching request+response to api-capture.json — with secret header VALUES
// redacted, so the file is safe to share. The assistant uses it to build the
// real API mapping; your token stays on this PC.
async function captureApi(cfg) {
  const { chromium } = require("playwright");
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
  const { chromium } = require("playwright");
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
  const res = await fetch(`${cfg.backendUrl.replace(/\/$/, "")}/api/v1/import/apna`, { method: "POST", body: form });
  const text = await res.text();
  if (!res.ok) throw new Error(`import failed HTTP ${res.status}: ${text.slice(0, 200)}`);
  return JSON.parse(text);
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
  const { chromium } = require("playwright");
  const ctx = await chromium.launchPersistentContext(SESSION_DIR, { headless: true, acceptDownloads: true });
  const page = ctx.pages()[0] || (await ctx.newPage());
  try {
    const jobs = await discoverJobs(page, cfg);
    if (!jobs.length) {
      log("No applicant links found on the jobs page. (First run? Tell me what the page shows and I'll adjust.)");
      return;
    }
    log(`Found ${jobs.length} job(s) with an applicant list. Importing…`);
    for (const job of jobs) {
      try { await importJob(page, cfg, job); }
      catch (e) { log(`ERROR on "${job.title || job.url}": ${e.message}`); }
    }
  } finally {
    await ctx.close();
  }
}

async function main() {
  const cfg = loadConfig();
  if (process.argv.includes("--login")) { await doLogin(cfg); process.exit(0); }
  if (process.argv.includes("--capture")) { await captureApi(cfg); process.exit(0); }
  if (!fs.existsSync(SESSION_DIR) || fs.readdirSync(SESSION_DIR).length === 0) {
    console.error("\n  Not signed in yet. Run first:  node sync.js --login\n");
    process.exit(1);
  }
  fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });

  await runOnce(cfg);
  const hours = Number(cfg.intervalHours || 0);
  if (hours > 0) {
    log(`Next sync in ${hours}h. Leave this window open.`);
    setInterval(() => runOnce(cfg).catch((e) => log("Run error: " + e.message)), hours * 3600 * 1000);
  } else {
    log("Done (intervalHours=0, single run).");
    process.exit(0);
  }
}

main().catch((e) => { console.error("Fatal:", e); process.exit(1); });
