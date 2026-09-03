document.addEventListener('DOMContentLoaded', function () {
  const statusElement = document.getElementById('upload_status');
  const uploadForm = document.querySelector('.upload_form');
  const fileInput = document.getElementById('gcode_file');
  const uploadButton = document.querySelector('.upload_btn');

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
        } else if (status.state === 'error') {
          statusElement.classList.add('error');
          statusElement.textContent = status.error || 'Upload failed.';
        }
      })
      .catch(() => {});
  }

  refreshUploadStatus();
});
