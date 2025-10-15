// zoom.js — fixed selectors + forced first slide + safe re-mount

import PhotoSwipeLightbox from './photoswipe-lightbox.esm.min.js';
import PhotoSwipe from './photoswipe.esm.min.js';

// ---- helpers ----
function resetToFirst(main, thumbs) {
  if (!main) return;
  try {
    main.update && main.update();
    thumbs && thumbs.update && thumbs.update();
    main.slideTo(0, 0, false);
    if (thumbs) thumbs.slideTo(0, 0, false);
  } catch (e) {}
}

let _mounted = [];
function destroyMounted() {
  _mounted.forEach(pair => {
    try {
      pair.main && pair.main.destroy(true, true);
      pair.thumbs && pair.thumbs.destroy(true, true);
    } catch (e) {}
  });
  _mounted = [];
}

// ---- core init ----
function initOneGallery(mainSel, thumbsSel, colorSync) {
  const $main = document.querySelector(mainSel);
  const $thumbs = document.querySelector(thumbsSel);
  if (!$main || !$thumbs) return { main: null, thumbs: null };

  // Direction via data-direction (optional)
  const dirData = $thumbs.getAttribute('data-direction');

  const thumbs = new Swiper(thumbsSel, {
    initialSlide: 0,
    freeMode: true,
    watchSlidesProgress: true,

    // keep it horizontal on mobile; vertical on desktop if data says so
    slidesPerView: 'auto',
    spaceBetween: 10,
    breakpoints: {
      0:   { direction: 'horizontal' },
      1150:{ direction: dirData || 'vertical' }
    },

    observer: true, observeParents: true, observeSlideChildren: true,
  });

  const main = new Swiper(mainSel, {
    initialSlide: 0,
    speed: 300,
    navigation: { nextEl: '.thumbs-next', prevEl: '.thumbs-prev' },
    thumbs: { swiper: thumbs },

    observer: true, observeParents: true, observeSlideChildren: true,
  });

  // Force first slide (multiple safety points)
  resetToFirst(main, thumbs);
  window.addEventListener('load', () => resetToFirst(main, thumbs), { once: true });
  setTimeout(() => resetToFirst(main, thumbs), 0);

  // Optional: color swatch sync only for primary gallery
  if (colorSync) {
    const updateBtn = (idx) => {
      const slides = $main.querySelectorAll('.swiper-slide');
      const current = slides[idx];
      const col = current && current.getAttribute('data-color');
      if (!col) return;
      document.querySelectorAll('.color-btn').forEach(b => b.classList.remove('active'));
      const btn = document.querySelector(`.color-btn[data-color="${col}"]`);
      if (btn) btn.classList.add('active');
      const a = document.querySelector('.value-currentColor'); if (a) a.textContent = col;
      const b = document.querySelector('.select-currentColor'); if (b) b.textContent = col;
    };

    main.on('slideChange', function () { updateBtn(this.activeIndex); });

    document.querySelectorAll('.color-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const col = btn.getAttribute('data-color');
        const slides = Array.from($main.querySelectorAll('.swiper-slide'));
        const i = slides.findIndex(s => s.getAttribute('data-color') === col);
        if (i >= 0) {
          main.slideTo(i, 300, false);
          thumbs.slideTo(i, 300, false);
        }
      });
    });

    updateBtn(main.activeIndex);
  }

  return { main, thumbs };
}

function mountAllGalleries() {
  destroyMounted();

  // Primary gallery (exact selectors your template uses)
  if (document.querySelector('.thumbs-slider') && document.querySelector('.tf-product-media-main')) {
    const p = initOneGallery('.tf-product-media-main', '.thumbs-slider', true);
    if (p.main) _mounted.push(p);
  }

  // Additional numbered galleries (if you use them)
  const numbered = [
    { t: '.thumbs-slider1', m: '.tf-product-media-main1' },
    { t: '.thumbs-slider2', m: '.tf-product-media-main2' },
    { t: '.thumbs-slider3', m: '.tf-product-media-main3' },
    { t: '.thumbs-slider4', m: '.tf-product-media-main4' },
  ];
  numbered.forEach(({t,m}) => {
    if (document.querySelector(t) && document.querySelector(m)) {
      const p = initOneGallery(m, t, false);
      if (p.main) _mounted.push(p);
    }
  });
}

// Initial mount
document.addEventListener('DOMContentLoaded', mountAllGalleries);

// Re-mount hook if gallery HTML is swapped via AJAX
document.addEventListener('variant:images:updated', mountAllGalleries);


