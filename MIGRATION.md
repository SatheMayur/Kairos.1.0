# Move the AI Recruitment System to another PC

This guide sets the whole system up on a new computer, **ready to use**, in a
self-contained virtual environment. Written in plain steps — no prior coding needed.

---

## First, what actually lives where (important)

Your system has three parts:

| Part | Where it runs | Migrating? |
|------|---------------|------------|
| **The website / app** (dashboard, candidates, outreach, briefing) | The cloud (Vercel) — `kgirdharlal-recruitment.vercel.app` | Stays in the cloud. It already runs 24/7 with no PC. You can *also* run a copy on this PC for local use. |
| **The database** (all your candidates, jobs, interviews) | The cloud (Neon) | Stays in the cloud. Your data is safe there. |
| **The WhatsApp bridge** | **Your always-on PC** | **This is the main thing that moves to the new PC.** It links your WhatsApp to the system. |

> **So in most cases "migrate to a new PC" = move the WhatsApp bridge** (Part 2 below).
> Setting up a full local copy of the app (Part 1) is optional — useful for testing
> or running it offline on your own machine.

---

## Before you start — install these once on the new PC
- **Python 3.11 or newer** — https://www.python.org/downloads/
  (During install on Windows, **tick "Add Python to PATH"**.)
- **Node.js 18 or newer** — https://nodejs.org  (needed only for the WhatsApp bridge)
- The project folder. Either:
  - **Copy** the whole `recruitment_system` folder from the old PC (USB / cloud drive), **or**
  - **Download** it with Git: `git clone https://github.com/SatheMayur/Kairos.1.0.git`

---

## Part 1 — Run the app on this PC (optional)

1. Open the `recruitment_system` folder.
2. **Double-click `setup.bat`** (Windows) — or run `bash setup.sh` (Mac/Linux).
   It builds the virtual environment and installs everything. Run it **once**.
3. **Open the `.env` file in Notepad** and fill in your keys — see **Step 3** below.
4. **Double-click `run.bat`** (or `bash run.sh`). When it says *"Application startup
   complete,"* open your browser at **http://127.0.0.1:8000/ui/**
5. To stop it, close that window.

### Step 3 — Filling in `.env` (your keys)
`setup` created a file called **`.env`** from the template. Open it and fill these.
The fastest way to get the secret values is from your Vercel project:
**vercel.com → kgirdharlal-recruitment → Settings → Environment Variables** (click
the eye icon to reveal each), or, if you have the Vercel app installed, run
`vercel env pull .env` and it fills them automatically.

| Setting | What to put | Needed? |
|---------|-------------|---------|
| `DATABASE_URL` | **Leave as the sqlite line** for a fresh, separate database on this PC. **Or** paste the Neon URL from Vercel to use the *same live data* as the website. | Yes |
| `GEMINI_API_KEY` | Your Google AI key (from aistudio.google.com) — same one the website uses | Yes (for AI) |
| `ANTHROPIC_API_KEY` | Only if you want the AI to run on Claude (console.anthropic.com) | Optional |
| `APIFY_API_TOKEN` | From apify.com → Settings → Integrations (for sourcing) | Optional |
| `BRIDGE_API_SECRET` | Leave the default unless you changed it; **must match** the bridge's `BRIDGE_API_KEY` | Yes |
| `APPS_SCRIPT_WEB_APP_URL` | Your email-sending Apps Script URL (for sending emails) | Optional |
| `CRON_SECRET` | Any long random text (only matters if you run the scheduled jobs) | Optional |

Everything else can stay at its default. Leave a line blank if you don't have it.

---

## Part 2 — Move the WhatsApp bridge (the main migration)

The bridge is what lets the system send/receive WhatsApp messages. It must run on
a PC that **stays on**.

1. Open the `recruitment_system\waha-bridge` folder.
2. **Double-click `setup-bridge.bat`** — installs the bridge (needs Node.js).
3. **Double-click `run-bridge.bat`.** The first time, it shows a **QR code**.
4. On your phone: **WhatsApp → Settings → Linked Devices → Link a Device →** scan the QR.
5. It prints *"✅ Connected to WhatsApp"*. That's it — the new PC is now the bridge.

> On the **old** PC, stop the old bridge so only one runs at a time
> (close its window, or run `pm2 delete whatsapp-bridge`).

### Keep it running 24/7 (recommended)
So it survives restarts and runs in the background:
```
cd waha-bridge
pm2 start bridge.js --name whatsapp-bridge
pm2 save
pm2 startup        REM follow the printed line so it auto-starts when the PC boots
```
Restart it anytime with: `pm2 restart whatsapp-bridge`

---

## Check it's all working
- App (if you set it up): open **http://127.0.0.1:8000/ui/** — the dashboard loads.
- WhatsApp: on the website, **WhatsApp** page (or the sidebar dot) shows **Connected**.
- Send yourself a test from the website's WhatsApp page — it arrives on your phone.

---

## Common problems (plain English)
- **"Python is not installed"** → install Python 3.11+, tick *Add to PATH*, re-run `setup.bat`.
- **`setup.bat` fails on install** → check internet, then double-click it again.
- **App opens but pages are empty / errors** → your `.env` keys aren't filled. Re-check Step 3.
- **WhatsApp won't connect / keeps asking for QR** → make sure only ONE bridge is running
  (stop the old PC's bridge), then run `run-bridge.bat` again and re-scan.
- **WhatsApp messages stop later** → on the bridge PC: open a terminal in `waha-bridge`
  and run `pm2 restart whatsapp-bridge` (or just double-click `run-bridge.bat` again).
- **Want the same data as the website** → put the Neon `DATABASE_URL` in `.env` (Option B).
  **Want a clean separate copy** → leave the sqlite line (Option A).

---

## What you do NOT need to move
The cloud parts keep working on their own — you don't touch them:
- the website (Vercel), the database (Neon), the email-sending Apps Script, and the
  Google/Gmail connections. They have no dependency on any single PC.

That's it — the system is portable: `setup.bat` → fill `.env` → `run.bat`, and the
bridge via `setup-bridge.bat` → `run-bridge.bat`.
