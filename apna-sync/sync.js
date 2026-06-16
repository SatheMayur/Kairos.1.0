/**
 * Apna applicant sync — runs on the owner's always-on PC.
 *
 * Two modes:
 *   node sync.js --login   → opens a browser so you can sign in to Apna ONCE by
 *                            hand (password + OTP). The session is saved to the
 *                            ./session folder and reused forever after.
 *   node sync.js           → uses the saved session to, for each configured job,
 *                            open the applicants page, click Export, download the
 *                            Excel, and upload it to the recruitment system's
 *                            /import/csv endpoint (source=APNA). Repeats on a timer.
 *
 * Design notes (technical):
 *  - launchPersistentContext keeps cookies/localStorage on disk → no password is
 *    ever stored by us, and OTP/2FA only has to be done once.
 *  - We click the SAME export button you click manually instead of scraping
 *    individual fields, so it survives most layout changes and reuses the
 *    server-side Apna Excel parser + de-dup that already exists.
 */
const fs = require("fs");
const path = require("path");

const SESSION_DIR = path.join(__dirname, "session");
const DOWNLOAD_DIR = path.join(__dirname, "downloads");
const CONFIG_PATH = path.join(__dirname, "config.json");

function loadConfig() {
  if (!fs.existsSync(CONFIG_PATH)) {
    console.error("\n  config.json not found. Copy config.example.json to config.json and fill it in.\n");
    process.exit(1);
  }
  return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
}

function log(msg) {
  console.log(`[${new Date().toLocaleString()}] ${msg}`);
}

// ── One-time manual login ──────────────────────────────────────────────────
async function doLogin(cfg) {
  const { chromium } = require("playwright");
  log("Opening Apna login. Sign in by hand (password + OTP if asked).");
  const ctx = await chromium.launchPersistentContext(SESSION_DIR, {
    headless: false,
    acceptDownloads: true,
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto(cfg.loginUrl, { waitUntil: "domcontentloaded" });
  console.log("\n  → Finish logging in, then come back here and press ENTER.\n");
  await new Promise((resolve) => process.stdin.once("data", resolve));
  await ctx.close();
  log("Session saved. You can now run:  node sync.js");
}

// ── Upload a downloaded Excel to the recruitment system ────────────────────
async function uploadFile(cfg, filePath, ourJobId) {
  const buf = fs.readFileSync(filePath);
  const form = new FormData(); // Node 18+ global
  form.append("file", new Blob([buf]), path.basename(filePath));
  form.append("job_id", String(ourJobId));
  form.append("source", "APNA");
  form.append("auto_outreach", "true");

  const res = await fetch(`${cfg.backendUrl.replace(/\/$/, "")}/api/v1/import/csv`, {
    method: "POST",
    body: form,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`import failed HTTP ${res.status}: ${text.slice(0, 200)}`);
  return JSON.parse(text);
}

// ── Export applicants for one job, then upload ─────────────────────────────
// THE ONE APNA-SPECIFIC PART: navigates to the applicants page and clicks the
// export button. URL + button selector come from config (filled once we've seen
// the real portal page — see config.example.json).
async function syncJob(page, cfg, job) {
  if (!job.applicantsUrl || job.applicantsUrl.includes("REPLACE_WITH") ||
      !job.exportButtonSelector || job.exportButtonSelector.includes("REPLACE_WITH")) {
    log(`SKIP "${job.label}" — applicantsUrl / exportButtonSelector not configured yet.`);
    return;
  }
  log(`"${job.label}": opening applicants page…`);
  await page.goto(job.applicantsUrl, { waitUntil: "domcontentloaded" });

  // Detect a kicked-out session early so we fail with a clear message.
  if (/login|signin/i.test(page.url())) {
    throw new Error("Session expired — run:  node sync.js --login");
  }

  const downloadPath = path.join(DOWNLOAD_DIR, `apna-${job.ourJobId}-${Date.now()}.xlsx`);
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 60000 }),
    page.click(job.exportButtonSelector),
  ]);
  await download.saveAs(downloadPath);
  log(`"${job.label}": downloaded export, uploading…`);

  const result = await uploadFile(cfg, downloadPath, job.ourJobId);
  log(`"${job.label}": done — ${result.inserted} new, ${result.duplicates_skipped} already had, ` +
      `${result.auto_shortlisted} shortlisted.`);
  fs.unlinkSync(downloadPath);
}

async function runOnce(cfg) {
  const { chromium } = require("playwright");
  const ctx = await chromium.launchPersistentContext(SESSION_DIR, {
    headless: true,
    acceptDownloads: true,
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  try {
    for (const job of cfg.jobs || []) {
      try {
        await syncJob(page, cfg, job);
      } catch (e) {
        log(`ERROR on "${job.label}": ${e.message}`);
      }
    }
  } finally {
    await ctx.close();
  }
}

async function main() {
  const cfg = loadConfig();
  if (process.argv.includes("--login")) {
    await doLogin(cfg);
    process.exit(0);
  }
  if (!fs.existsSync(SESSION_DIR) || fs.readdirSync(SESSION_DIR).length === 0) {
    console.error("\n  Not logged in yet. Run first:  node sync.js --login\n");
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

main().catch((e) => {
  console.error("Fatal:", e);
  process.exit(1);
});
