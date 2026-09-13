function showStatus(message) {
  document.getElementById('status_message').textContent = message;
}

async function toggleRelay(relayId, checked) {
  const toggle = document.getElementById(relayId);
  const state = checked ? 1 : 0;
  const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
  toggle.disabled = true;
  try {
    if (!checked && relayId.endsWith('_1')) {
      let printState;
      try {
        const response = await fetch('/printer/print/status', {
          credentials: 'same-origin',
          cache: 'no-store',
        });
        if (!response.ok) throw new Error('Could not check print status');
        printState = (await response.json()).state;
      } catch (_) {
        printState = 'unavailable';
      }
      const warning = printState === 'printing'
        ? 'A print is in progress. Turning off printer power will stop it. Turn off printer power?'
        : printState !== 'idle'
          ? 'Could not confirm that the printer is idle. Turning off power may stop a print. Turn off printer power?'
          : null;
      if (warning && !window.confirm(warning)) {
        toggle.checked = true;
        return;
      }
    }

    const response = await fetch("/relay", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,      // 👈 Add this header
    },
    credentials: "same-origin",       // 👈 Include cookies/session
    body: JSON.stringify({ relay_id: relayId, state: state }),
    });
    const data = await response.json();
    if (!response.ok || data.status !== "ok") {
      throw new Error(data.error || 'Failed to set relay');
    }
  } catch (err) {
    showStatus("Could not set relay: " + err.message);
    toggle.checked = !checked;
  } finally {
    toggle.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.ping_time').forEach(span => {
    const utcTime = span.dataset.iso;
    const date = new Date(utcTime);
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const year = date.getFullYear();
    const hour = String(date.getHours()).padStart(2, '0');
    const minute = String(date.getMinutes()).padStart(2, '0');
    span.textContent = `${day}.${month}.${year} ${hour}:${minute}`;
  });
});
