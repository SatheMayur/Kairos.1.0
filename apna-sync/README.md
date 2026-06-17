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
The short version is in **SETUP.txt** (hand that to whoever sets up the PC). In brief:

1. Install Node.js from https://nodejs.org (the big green button).
2. Double-click `start-apna-sync.bat`. On the first run it installs everything it
   needs, makes your settings file (`config.json`) from the example automatically,
   and opens a browser so you can sign in to Apna once (password + OTP). Press ENTER
   in the black window when you're signed in.
3. That's it — it runs on its own from then on.

### About the settings file (`config.json`)
The launcher creates this for you. It already points at the right addresses (the
HR system and the Apna sign-in/jobs pages). The one thing I (the assistant) set up
with you is **`databaseSearches`** — your saved Apna Database searches per role. We
confirm those together on the first real run. You don't need to edit this file by hand.

## Daily use
Just leave `start-apna-sync.bat` running. To start it automatically when the PC
boots, use the same Task Scheduler steps as the WhatsApp helper (see
`../waha-bridge/RUN-24-7.md`).
