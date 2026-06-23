==============================================================
  K. GIRDHARLAL — AI RECRUITMENT SYSTEM
  Setting up on this PC? Read this. It takes ~5 minutes.
==============================================================

You have two ways to set it up. Pick ONE.

--------------------------------------------------------------
 OPTION A — Let Claude do it (easiest, if you have Claude Code)
--------------------------------------------------------------
 1. Unzip this folder somewhere (e.g. Desktop).
 2. Open this folder in Claude Code (same Claude login you use).
 3. Type:   set up and run everything
 4. Claude will build everything and start the app + WhatsApp.
    You only do two things it can't:
       - install Python once (Claude will give you the link), and
       - scan the WhatsApp QR code with your phone.

 (Tip: first run  "vercel login"  with your usual email so Claude
  can fetch your keys automatically. If not, Claude will ask you
  to paste them once.)

--------------------------------------------------------------
 OPTION B — One double-click (no Claude needed)
--------------------------------------------------------------
 1. Unzip this folder.
 2. Install Python 3.11+ once from https://www.python.org/downloads/
    IMPORTANT: tick the box "Add Python to PATH" during install.
 3. Double-click   START-HERE.bat
       - First run: it builds everything (a few minutes).
       - Then it opens your dashboard and starts WhatsApp.
 4. A QR code appears in the WhatsApp window — on your phone open
    WhatsApp > Settings > Linked Devices > Link a Device > scan it.
 5. Keep the windows open. To start again later, double-click
    START-HERE.bat again.

--------------------------------------------------------------
 GOOD TO KNOW
--------------------------------------------------------------
 - Your DASHBOARD also lives online (no PC needed):
       https://kgirdharlal-recruitment.vercel.app/ui/
   Double-click OPEN-DASHBOARD.bat to open it.

 - To make the copy on THIS PC show your REAL live candidates,
   open the file ".env" and set DATABASE_URL to your Neon URL
   (details in MIGRATION.md, "Step 3"). Otherwise it starts empty.

 - Only ONE PC can run the WhatsApp link at a time. If you move to
   this PC, stop the WhatsApp window on the old one.

 - Full instructions + troubleshooting:  MIGRATION.md
==============================================================
