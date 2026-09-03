var socket;
var videoReadyTimer;

function resizeVideo() {
    const videoContainer = document.getElementById('video_container');
    if (!videoContainer) return false;

    const videoImage = videoContainer.querySelector('img');
    const footer = document.querySelector('.footer');
    const availableWidth = videoContainer.parentElement.clientWidth;
    const containerTop = videoContainer.getBoundingClientRect().top;
    const availableBottom = footer ? footer.getBoundingClientRect().top : window.innerHeight;
    const availableHeight = Math.max(1, availableBottom - containerTop - 10);

    const imageNaturalWidth = videoImage.naturalWidth;
    const imageNaturalHeight = videoImage.naturalHeight;
    if (!imageNaturalWidth || !imageNaturalHeight) return false;

    const aspectRatio = imageNaturalWidth / imageNaturalHeight;

    let videoContainerWidth = availableWidth;
    let videoContainerHeight = availableWidth / aspectRatio;

    if (videoContainerHeight > availableHeight) {
        videoContainerHeight = availableHeight;
        videoContainerWidth = availableHeight * aspectRatio;
    }

    videoContainer.style.width = `${videoContainerWidth}px`;
    videoContainer.style.height = `${videoContainerHeight}px`;

    // Explicitly set iframe dimensions to match container
    videoImage.style.width = '100%';
    videoImage.style.height = '100%';
    return true;
}

function resizeVideoWhenReady() {
    if (resizeVideo()) return;
    if (videoReadyTimer) return;

    let attempts = 0;
    videoReadyTimer = setInterval(() => {
        attempts += 1;
        if (resizeVideo() || attempts >= 300) {
            clearInterval(videoReadyTimer);
            videoReadyTimer = null;
        }
    }, 100);
}

window.addEventListener('load', resizeVideoWhenReady);

window.addEventListener('resize', resizeVideo);
if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', resizeVideo);
}

document.addEventListener('DOMContentLoaded', function() {
    const resolutionDropdown = document.getElementById('resolution');
    const deviceDropdown = document.getElementById('device');
    const stopPrintButton = document.getElementById('stop_print');
    const videoImage = document.querySelector('#video_container img');
    const printProgress = document.getElementById('print_progress');
    const printProgressBar = document.getElementById('print_progress_bar');
    const printProgressText = document.getElementById('print_progress_text');
    const printerSettingsButton = document.getElementById('printer_settings_btn');

    function refreshPrintProgress() {
      if (!printProgress) return;
      fetch('/printer/print/status', {credentials: 'same-origin'})
        .then(response => response.json())
        .then(status => {
          const online = status.state === 'idle' || status.state === 'printing';
          const printing = status.state === 'printing';
          if (printerSettingsButton) printerSettingsButton.hidden = !online;
          printProgress.hidden = !printing;
          if (stopPrintButton) stopPrintButton.hidden = !printing;
          if (printing) {
            const percent = Number(status.percent) || 0;
            printProgressBar.style.width = `${percent}%`;
            printProgressText.textContent = `${percent}%`;
          }
          resizeVideo();
        })
        .catch(() => {})
        .finally(() => setTimeout(refreshPrintProgress, 5000));
    }

    refreshPrintProgress();

    if (videoImage) {
      videoImage.addEventListener('load', resizeVideo);
      resizeVideoWhenReady();
    }

    if (stopPrintButton) {
      stopPrintButton.addEventListener('click', function() {
        if (!confirm('Stop the running print? This cannot be resumed.')) return;

        const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
        stopPrintButton.disabled = true;
        fetch('/printer/stop', {
          method: 'POST',
          headers: {'X-CSRFToken': csrfToken},
          credentials: 'same-origin'
        })
        .then(async response => {
          const data = await response.json();
          if (!response.ok || data.status !== 'ok') {
            throw new Error(data.error || 'Could not stop the print');
          }
          alert('Print stopped.');
        })
        .catch(error => alert(error.message))
        .finally(() => { stopPrintButton.disabled = false; });
      });
    }

    if (resolutionDropdown) {
    resolutionDropdown.addEventListener('change', function() {
        const selectedResolution = this.value;
        const newURL = window.location.pathname + '?resolution=' + selectedResolution;
        window.location.href = newURL;
    });
    }

    if (deviceDropdown) {
        deviceDropdown.addEventListener('change', function() {
            const selectedDevice = this.value;
            const newURL = window.location.pathname + '?device=' + selectedDevice;
            window.location.href = newURL;
        });
    }

});
