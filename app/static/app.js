// Shared utilities for all UI pages

function esc(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function api(url, options = {}) {
  const opts = {
    method: options.method || 'GET',
    headers: { 'Content-Type': 'application/json' },
  };
  if (options.body) opts.body = JSON.stringify(options.body);
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.detail || JSON.stringify(j); } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

function showModal(id) { document.getElementById(id).style.display = 'block'; }
function hideModal(id) { document.getElementById(id).style.display = 'none'; }

// Close modal on backdrop click
document.addEventListener('click', e => {
  if (e.target.style && e.target.style.position === 'fixed' && e.target.style.zIndex === '200') {
    e.target.style.display = 'none';
  }
});

function scoreColor(score) {
  if (score >= 65) return '#34d399';
  if (score >= 40) return '#fbbf24';
  return '#f87171';
}
