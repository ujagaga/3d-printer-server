var socket;
var videoReadyTimer;
var printStartedAt = null;

function formatPrintEta(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return 'Estimating time remaining…';
    if (seconds < 60) return 'Estimated remaining: less than a minute';
    const minutes = Math.ceil(seconds / 60);
    const hours = Math.floor(minutes / 60);
    return `Estimated remaining: ${hours ? `${hours}h ` : ''}${minutes % 60}m`;
}

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
    const printEta = document.getElementById('print_eta');
    const printerSettingsButton = document.getElementById('printer_settings_btn');
    const powerOffOption = document.getElementById('power_off_when_done');
    const powerOffWrapper = document.getElementById('power_off_when_done_wrapper');
    const bedButtons = document.querySelectorAll('.bed_btn');
    let bedBusy = false;
    let bedIdle = false;

    function updateBedButtons() {
      bedButtons.forEach(button => { button.disabled = bedBusy || !bedIdle; });
    }

    let powerOffSaving = false;
    let powerOffRevision = 0;

    if (powerOffOption) {
      powerOffOption.addEventListener('change', async () => {
        const enabled = powerOffOption.checked;
        powerOffRevision += 1;
        powerOffSaving = true;
        powerOffOption.disabled = true;
        try {
          const response = await fetch('/printer/power-off-when-done', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content},
            body: JSON.stringify({enabled}),
          });
          if (!response.ok) throw new Error('Could not update automatic power off');
        } catch (error) {
          powerOffOption.checked = !enabled;
          showStatus(error.message);
        } finally {
          powerOffSaving = false;
          powerOffOption.disabled = false;
        }
      });
    }

    function refreshPrintProgress() {
      if (!printProgress) return;
      const revision = powerOffRevision;
      fetch('/printer/print/status', {credentials: 'same-origin'})
        .then(response => response.json())
        .then(status => {
          const online = status.state === 'idle' || status.state === 'printing';
          const printing = status.state === 'printing';
          if (powerOffWrapper) powerOffWrapper.hidden = !printing;
          if (powerOffOption && !powerOffSaving && revision === powerOffRevision) {
            powerOffOption.checked = status.power_off_when_done === true;
          }
          if (printerSettingsButton) {
            printerSettingsButton.classList.toggle('disabled', !online);
            printerSettingsButton.setAttribute('aria-disabled', String(!online));
            printerSettingsButton.tabIndex = online ? 0 : -1;
          }
          bedIdle = status.state === 'idle';
          updateBedButtons();
          printProgress.hidden = !printing;
          if (stopPrintButton) stopPrintButton.hidden = !printing;
          printStartedAt = printing ? status.started_at ?? null : null;
          if (printing) {
            const percent = Number(status.percent) || 0;
            printProgressBar.style.width = `${percent}%`;
            printProgressText.textContent = `${percent}%`;
            if (printEta) {
              printEta.textContent = printStartedAt === null
                ? 'Time remaining unavailable (start time unknown)'
                : formatPrintEta(status.remaining_seconds);
            }
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
        .catch(error => showStatus(error.message))
        .finally(() => { stopPrintButton.disabled = false; });
      });
    }

    bedButtons.forEach(button => {
      button.addEventListener('click', function() {
        bedBusy = true;
        updateBedButtons();
        fetch('/printer/bed', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content},
          body: JSON.stringify({position: button.dataset.position}),
        })
        .then(async response => {
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Could not move the bed');
          showStatus(button.dataset.position === 'front' ? 'Bed moved forward.' : 'Bed moved back.');
        })
        .catch(error => showStatus(error.message))
        .finally(() => { bedBusy = false; updateBedButtons(); });
      });
    });

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
