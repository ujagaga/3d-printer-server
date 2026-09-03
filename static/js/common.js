function toggleRelay(relayId, checked) {
  const state = checked ? 1 : 0;
  const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

  fetch("/relay", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,      // 👈 Add this header
    },
    credentials: "same-origin",       // 👈 Include cookies/session
    body: JSON.stringify({ relay_id: relayId, state: state }),
  })
  .then(response => response.json())
  .then(data => {
    if (data.status !== "ok") {
      console.log("Failed to set relay: " + data.error);
      document.getElementById(relayId).checked = !checked;
    }
  })
  .catch(err => {
    console.log("Error: " + err);
    document.getElementById(relayId).checked = !checked;
  });
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
