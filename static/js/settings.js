document.addEventListener('DOMContentLoaded', function () {
  const statusElement = document.getElementById('status_message');
  const uploadForm = document.querySelector('.upload_form');
  const fileInput = document.getElementById('gcode_file');
  const uploadButton = document.querySelector('.upload_btn');
  document.querySelectorAll('.delete_file_form').forEach(form => {
    form.addEventListener('submit', event => {
      const filename = form.elements.filename.value;
      if (!window.confirm(`Delete ${filename} from the SD card? This cannot be undone.`)) {
        event.preventDefault();
      }
    });
  });

  fileInput.addEventListener('change', function () {
    if (!fileInput.files.length) return;
    uploadButton.classList.add('disabled');
    uploadButton.removeAttribute('tabindex');
    uploadForm.submit();
  });

  uploadButton.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      fileInput.click();
    }
  });

  function refreshUploadStatus() {
    fetch('/printer/upload/status', {credentials: 'same-origin'})
      .then(response => response.json())
      .then(status => {
        if (!status || status.state === 'idle') return;
        statusElement.hidden = false;
        if (status.state === 'receiving' || status.state === 'uploading') {
          const percent = status.size ? Math.floor(status.written * 100 / status.size) : 0;
          statusElement.textContent = `Uploading ${status.filename}: ${percent}%`;
          setTimeout(refreshUploadStatus, 2000);
        } else if (status.state === 'complete') {
          statusElement.textContent = `Upload complete: ${status.filename}`;
          if (document.getElementById('upload_pending')) window.location.reload();
        } else if (status.state === 'error') {
          const pending = document.getElementById('upload_pending');
          if (pending) pending.textContent = 'Upload stopped. Refresh to see the SD files.';
          statusElement.textContent = status.error || 'Upload failed.';
        }
      })
      .catch(() => {});
  }

  refreshUploadStatus();
});
