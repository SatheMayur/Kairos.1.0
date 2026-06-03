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
} = require('@whiskeysockets/baileys');
const express = require('express');
const axios = require('axios');
const path = require('path');
const fs = require('fs');
const pino = require('pino');

const SESSION_DIR = path.join(__dirname, 'session');
const VERCEL = (process.env.VERCEL_URL || 'https://kgirdharlal-recruitment.vercel.app').replace(/\/$/, '');
const BRIDGE_KEY = process.env.BRIDGE_API_KEY || 'kgirdharlal-bridge-secret';
const POLL_INTERVAL_MS = 3000;

const headers = { 'x-bridge-key': BRIDGE_KEY, 'Content-Type': 'application/json' };

let sock = null;
let isConnected = false;
let pollTimer = null;

const logger = pino({ level: 'silent' });

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

// ── Poll Vercel for outgoing messages ─────────────────────────────────────
async function pollAndSend() {
  if (!isConnected) return;
  try {
    const { data: messages } = await axios.get(`${VERCEL}/api/v1/wa/poll`, { headers, timeout: 8000 });
    if (!messages.length) return;

    const results = [];
    for (const msg of messages) {
      try {
        const phone = msg.phone.replace(/\D/g, '');
        const jid = phone.length === 10 ? `91${phone}@s.whatsapp.net` : `${phone}@s.whatsapp.net`;
        const sent = await sock.sendMessage(jid, { text: msg.message });
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
    printQRInTerminal: true,
    browser: ['K. Girdharlal HR', 'Chrome', '120.0'],
  });

  if (!state.creds.registered) {
    console.log('\n' + '═'.repeat(54));
    console.log('  SCAN THE QR CODE WITH YOUR PHONE');
    console.log('  WhatsApp → Settings → Linked Devices');
    console.log('  → Link a Device → point camera at QR below');
    console.log('═'.repeat(54) + '\n');
  }

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', ({ connection, lastDisconnect }) => {
    if (connection === 'open') {
      isConnected = true;
      console.log('[WA] ✅ Connected to WhatsApp — starting poll loop');
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(pollAndSend, POLL_INTERVAL_MS);

      // Notify Vercel bridge is alive
      axios.post(`${VERCEL}/api/v1/wa/inbound`,
        { from: 'system@bridge', body: '__bridge_connected__', session: 'default' },
        { headers }
      ).catch(() => {});

    } else if (connection === 'close') {
      isConnected = false;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
      console.log('[WA] Disconnected. Reconnecting:', shouldReconnect);
      if (shouldReconnect) setTimeout(connectToWhatsApp, 5000);
    }
  });

  // ── Inbound messages → Vercel ────────────────────────────────────────────
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      const from = msg.key.remoteJid;
      if (!from || from.endsWith('@g.us')) continue;
      const body = msg.message?.conversation || msg.message?.extendedTextMessage?.text || '';
      if (!body) continue;

      console.log(`[RECV] ← ${from}: ${body.substring(0, 60)}`);

      try {
        await axios.post(`${VERCEL}/api/v1/wa/inbound`, {
          from, body, session: 'default'
        }, { headers, timeout: 10000 });
      } catch (err) {
        console.error('[FORWARD ERR]', err.message);
      }
    }
  });
}

// ── Entry point ───────────────────────────────────────────────────────────
connectToWhatsApp().catch(console.error);

process.on('unhandledRejection', err => console.error('[UNHANDLED]', err?.message));
process.on('uncaughtException', err => { console.error('[UNCAUGHT]', err?.message); process.exit(1); });
