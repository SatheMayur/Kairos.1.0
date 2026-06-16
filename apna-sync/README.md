# Apna Sync — automatic applicant import

This small program runs on your office PC and brings new Apna applicants into the
HR system on its own, so you never export and upload Excel files by hand.

## What it does
1. You sign in to Apna **once** (in a browser it opens).
2. From then on, every few hours it opens each of your Apna jobs, clicks
   **Export**, downloads the applicant list, and uploads it to the HR system.
3. The HR system scores them, shortlists, and skips anyone already imported.

## Honest limitations (please read)
- **It only runs while this PC is on.** Like the WhatsApp helper, it cannot run in
  the cloud — a logged-in Apna session has to live on your computer.
- **It can break when Apna changes their website.** If it stops, run `--login` again
  or tell me and I'll fix the page details.
- **Use your own employer account.** Automated downloading could, in theory, get an
  account flagged by Apna. This is the trade-off for full automation.
- Your Apna password is **never** sent anywhere or stored by the HR system. You type
  it yourself in step 1; only the resulting session is saved, on this PC.

## One-time setup
1. Install Node.js from https://nodejs.org (the big green button).
2. Copy `config.example.json` to `config.json`.
3. Fill in `config.json`:
   - `loginUrl` — the Apna employer sign-in page.
   - For each job: `ourJobId` (its number in the HR system) and `applicantsUrl`
     (the page showing that job's applicants).
   - `exportButtonSelector` — leave the placeholder and **send me a screenshot of the
     applicants page**; I'll fill this in for you.
4. Double-click `start-apna-sync.bat`. A browser opens — sign in to Apna (password +
   OTP). Press ENTER in the black window when done.

## Daily use
Just leave `start-apna-sync.bat` running. To start it automatically when the PC
boots, use the same Task Scheduler steps as the WhatsApp helper (see
`../waha-bridge/RUN-24-7.md`).
