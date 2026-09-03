document.addEventListener('DOMContentLoaded', function () {

  // ---------- Image Modal ----------
  const imgModal = document.getElementById('imgModal');
  const modalImage = document.getElementById('modalImage');
  const imgClose = imgModal.querySelector('.close');

  document.querySelectorAll('.user_info_row .user_picture').forEach(img => {
    img.addEventListener('click', function() {
      modalImage.src = this.src;
      imgModal.style.display = 'flex';
    });
  });

  imgClose.addEventListener('click', () => {
    imgModal.style.display = 'none';
  });

  window.addEventListener('click', e => {
    if (e.target === imgModal) {
      imgModal.style.display = 'none';
    }
  });
});
