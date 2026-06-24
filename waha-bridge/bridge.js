/**
 * K. Girdharlal International — WhatsApp Bridge (Baileys)
 *
 * Architecture (no public URL needed — works over port 443 only):
 *   OUTGOING: polls GET /api/v1/wa/poll every 3 s → sends via Baileys → POSTs ACK
 *   INCOMING: Baileys receives → POSTs to POST /api/v1/wa/inbound
 *
 * First-time setup:
 *   - Run: node bridge.js
 *   - A QR code appears in the terminal
 *   - On phone: WhatsApp → Settings → Linked Devices → Link a Device → scan QR
 *   - Session is saved to ./session/ and reconnects automatically on next run
 */

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  downloadMediaMessage,
} = require('@whiskeysockets/baileys');
const express = require('express');
const axios = require('axios');
const FormData = require('form-data');
const path = require('path');
const fs = require('fs');
const pino = require('pino');
const qrcode = require('qrcode-terminal');

const SESSION_DIR = path.join(__dirname, 'session');
const VERCEL = (process.env.VERCEL_URL || 'https://kgirdharlal-recruitment.vercel.app').replace(/\/$/, '');
const BRIDGE_KEY = process.env.BRIDGE_API_KEY || 'kgirdharlal-bridge-secret';
const POLL_INTERVAL_MS = 3000;

// Self-update: pull the latest bridge from GitHub on every start, so fixes
// apply automatically and this file never has to be replaced by hand again.
const SELF_UPDATE_URL = 'https://raw.githubusercontent.com/Web-Portfolio1/Kairos.1.0/master/waha-bridge/bridge.js';
async function selfUpdate() {
  try {
    const res = await axios.get(SELF_UPDATE_URL, { timeout: 10000, responseType: 'text' });
    const remote = typeof res.data === 'string' ? res.data : String(res.data);
    // Safety: only accept a clearly valid, self-updating build.
    if (remote.length < 3000 || !remote.includes('makeWASocket') || !remote.includes('selfUpdate')) {
      console.log('[UPDATE] downloaded file failed safety check — keeping current.');
      return;
    }
    const current = fs.readFileSync(__filename, 'utf8');
    if (remote === current) { console.log('[UPDATE] already up to date.'); return; }
    fs.writeFileSync(__filename + '.bak', current);   // backup for recovery
    fs.writeFileSync(__filename, remote);
    console.log('[UPDATE] ✅ New version installed — restarting to apply…');
    process.exit(0);   // start-whatsapp.bat loops and relaunches with the new code
  } catch (e) {
    console.error('[UPDATE] check failed (continuing with current version):', e.message);
  }
}

const headers = { 'x-bridge-key': BRIDGE_KEY, 'Content-Type': 'application/json' };

let sock = null;
let isConnected = false;
let pollTimer = null;
let reconnectCount = 0;
let messagesSentToday = 0;

const logger = pino({ level: 'silent' });

// Reset daily message counter at midnight
function scheduleDailyReset() {
  const now = new Date();
  const msUntilMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1) - now;
  setTimeout(() => {
    messagesSentToday = 0;
    console.log('[BRIDGE] Daily message counter reset');
    scheduleDailyReset();
  }, msUntilMidnight);
}
scheduleDailyReset();

// Heartbeat: log status every 60 seconds
setInterval(() => {
  console.log(`[BRIDGE] ✅ Running — ${messagesSentToday} messages sent today (reconnects: ${reconnectCount})`);
}, 60000);

// ── Memory sync: refresh the agent's memory tree every 20 minutes ───────────
// The bridge is the always-on machine, so it drives the 20-min sync (Vercel
// Hobby crons only run once/day). Side-effect-free on the server (sends nothing).
const MEMORY_SYNC_MS = 20 * 60 * 1000;
async function memorySync() {
  try {
    const { data } = await axios.post(`${VERCEL}/api/v1/memory/sync`, {}, { timeout: 15000 });
    console.log(`[MEMORY] synced — replies:${data.new_replies} wa:${data.whatsapp_sent} email:${data.email_sent} interested:${data.interested_now}`);
  } catch (e) {
    console.log('[MEMORY] sync failed (will retry in 20 min):', e.message);
  }
}
setTimeout(memorySync, 30000);            // first sync 30s after start
setInterval(memorySync, MEMORY_SYNC_MS);  // then every 20 minutes

// ── Local health server (optional, port 3001) ──────────────────────────────
const app = express();
app.use(express.json());
app.get('/health', (_, res) => res.json({ connected: isConnected }));
app.get('/api/sessions/default', (_, res) => res.json({
  name: 'default',
  status: isConnected ? 'WORKING' : 'STARTING',
  engine: { state: isConnected ? 'CONNECTED' : 'CONNECTING' }
}));
app.listen(3001, () => console.log('[BRIDGE] Local health server on :3001'));

// ── Switch number (relink): log out current, clear session, show fresh QR ──
let relinking = false;
async function doRelink() {
  if (relinking) return;
  relinking = true;
  console.log('[RELINK] Switching WhatsApp number — logging out current device…');
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  try { if (sock) await sock.logout(); } catch (e) { console.error('[RELINK] logout:', e.message); }
  try {
    if (fs.existsSync(SESSION_DIR)) fs.rmSync(SESSION_DIR, { recursive: true, force: true });
  } catch (e) { console.error('[RELINK] clear session:', e.message); }
  isConnected = false;
  console.log('[RELINK] Cleared. Generating a new QR code to scan…');
  setTimeout(() => { relinking = false; connectToWhatsApp().catch(console.error); }, 1500);
}

// ── Check Vercel for one-shot commands (e.g. RELINK) ───────────────────────
async function checkCommand() {
  try {
    const { data } = await axios.get(`${VERCEL}/api/v1/wa/command`, { headers, timeout: 8000 });
    if (data && data.command === 'RELINK') await doRelink();
  } catch (err) {
    if (err.response?.status !== 401) console.error('[CMD ERR]', err.message);
  }
}

// ── Poll Vercel for outgoing messages ─────────────────────────────────────
async function pollAndSend() {
  if (!isConnected) return;
  await checkCommand();
  if (relinking) return;
  try {
    const { data: messages } = await axios.get(`${VERCEL}/api/v1/wa/poll`, { headers, timeout: 8000 });
    if (!messages.length) return;

    const results = [];
    for (const msg of messages) {
      try {
        // If the server gives us a full JID (e.g. a privacy "<id>@lid" from an
        // inbound reply), send to it AS-IS so the message actually reaches the
        // person. Otherwise build the JID from the phone digits.
        let jid;
        if ((msg.phone || '').includes('@')) {
          jid = msg.phone;
        } else {
          const phone = (msg.phone || '').replace(/\D/g, '');
          jid = phone.length === 10 ? `91${phone}@s.whatsapp.net` : `${phone}@s.whatsapp.net`;
        }
        const sent = await sock.sendMessage(jid, { text: msg.message });
        messagesSentToday++;
        console.log(`[SEND] → ${jid}: ${msg.message.substring(0, 50)}`);
        results.push({ id: msg.id, status: 'sent', msg_id: sent?.key?.id });
      } catch (err) {
        console.error(`[SEND ERR] id=${msg.id}: ${err.message}`);
        results.push({ id: msg.id, status: 'failed', error: err.message });
      }
    }

    // ACK back to Vercel
    await axios.post(`${VERCEL}/api/v1/wa/ack`, { results }, { headers, timeout: 8000 });
  } catch (err) {
    if (err.response?.status !== 401) {
      console.error('[POLL ERR]', err.message);
    }
  }
}

// ── Baileys WhatsApp connection ────────────────────────────────────────────
async function connectToWhatsApp() {
  if (!fs.existsSync(SESSION_DIR)) fs.mkdirSync(SESSION_DIR, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    logger,
    auth: state,
    printQRInTerminal: false,
    browser: ['K. Girdharlal HR', 'Chrome', '120.0'],
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log('\n[WA] QR code ready — open your dashboard to scan:');
      console.log('     https://kgirdharlal-recruitment.vercel.app/ui/whatsapp\n');
      qrcode.generate(qr, { small: true });
      axios.post(`${VERCEL}/api/v1/wa/qr`, { qr }, { headers, timeout: 8000 })
        .then(() => console.log('[QR] Pushed to dashboard ✓'))
        .catch(e => console.error('[QR PUSH]', e.message));
    }

    if (connection === 'open') {
      isConnected = true;
      reconnectCount++;
      console.log(`[WA] ✅ Connected to WhatsApp — starting poll loop (connection #${reconnectCount})`);
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(pollAndSend, POLL_INTERVAL_MS);

      // Notify Vercel: connected (clears QR from dashboard)
      axios.post(`${VERCEL}/api/v1/wa/qr`, { status: 'CONNECTED' }, { headers }).catch(() => {});

    } else if (connection === 'close') {
      isConnected = false;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
      console.log('[WA] Disconnected. Reconnecting:', shouldReconnect);
      axios.post(`${VERCEL}/api/v1/wa/disconnect`, {}, { headers }).catch(() => {});
      if (shouldReconnect) {
        console.log('[BRIDGE] Restarting in 5 seconds…');
        setTimeout(() => connectToWhatsApp().catch(console.error), 5000);
      }
    }
  });

  // ── Inbound messages → Vercel ────────────────────────────────────────────
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      let from = msg.key.remoteJid;
      if (!from || from.endsWith('@g.us')) continue;

      // WhatsApp privacy "LID": incoming chats can report a privacy id
      // (<id>@lid) instead of the real phone number, which won't match the
      // candidate we sent TO. Resolve it back to the phone number when WhatsApp
      // provides it, so replies match the right candidate and we reply to the
      // real number. Falls back to the raw value if no mapping is available.
      const k = msg.key;
      const pn = k.remoteJidAlt || k.senderPn || k.participantPn || null;
      if (from.endsWith('@lid') && pn) {
        console.log(`[LID] resolved ${from} -> ${pn}`);
        from = pn;
      }

      const body = msg.message?.conversation || msg.message?.extendedTextMessage?.text || '';

      // ── CV / resume attachment → Resume Bank ──────────────────────────────
      // A document (PDF/DOC/DOCX/TXT) sent by a candidate is downloaded and
      // pushed to the Resume Bank, tagged with their phone number. Text messages
      // fall through to the normal reply handler below.
      const docMsg = msg.message?.documentMessage
                  || msg.message?.documentWithCaptionMessage?.message?.documentMessage;
      if (docMsg) {
        const fname = docMsg.fileName || 'cv';
        const mime = docMsg.mimetype || '';
        const looksLikeCv = /\.(pdf|docx?|txt)$/i.test(fname)
                          || /pdf|word|officedocument|msword|text\/plain/i.test(mime);
        if (looksLikeCv) {
          try {
            const buf = await downloadMediaMessage(
              msg, 'buffer', {}, { reuploadRequest: sock.updateMediaMessage }
            );
            const fd = new FormData();
            fd.append('file', buf, { filename: fname });
            fd.append('source', 'WHATSAPP');
            fd.append('from_contact', from);
            await axios.post(`${VERCEL}/api/v1/resumes/ingest`, fd, {
              headers: fd.getHeaders(), timeout: 30000,
            });
            console.log(`[CV] ← ${from}: ${fname} → Resume Bank`);
          } catch (err) {
            console.error('[CV ERR]', err.message);
          }
          continue;  // handled as a CV; don't treat as a text reply
        }
      }

      if (!body) continue;

      console.log(`[RECV] ← ${from}: ${body.substring(0, 60)}`);

      try {
        await axios.post(`${VERCEL}/api/v1/wa/inbound`, {
          from, body, session: 'default',
          push_name: msg.pushName || null,
          raw_jid: msg.key.remoteJid,   // original (lid) for diagnostics
          message_id: msg.key.id || null,   // idempotency key — dedup re-deliveries
        }, { headers, timeout: 10000 });
      } catch (err) {
        console.error('[FORWARD ERR]', err.message);
      }
    }
  });
}

// ── Entry point ───────────────────────────────────────────────────────────
// Check for updates first, then connect. (selfUpdate exits if it installed a
// new version; start-whatsapp.bat relaunches with the new code.)
selfUpdate().then(() => connectToWhatsApp().catch(console.error));

process.on('unhandledRejection', err => console.error('[UNHANDLED]', err?.message));
process.on('uncaughtException', err => {
  console.error('[UNCAUGHT]', err?.message);
  console.log('[BRIDGE] Restarting in 5 seconds…');
  setTimeout(() => connectToWhatsApp().catch(console.error), 5000);
});
