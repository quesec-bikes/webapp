(function() {
  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return decodeURIComponent(parts.pop().split(';').shift());
  }
  const csrftoken = getCookie('csrftoken');

  function container() {
    return document.getElementById('reviews-area');
  }
  function slug() {
    return container()?.dataset.slug;
  }
  function variantId() {
    return container()?.dataset.variant;
  }

  async function loadReviews(params = {}) {
    const s = params.sort || document.getElementById('reviews-sort')?.value || 'recent';
    const p = params.page || 1;
    const url = `/products/${slug()}/reviews/?variant=${variantId()}&sort=${s}&page=${p}`;
    const res = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
    const html = await res.text();
    container().innerHTML = html;
    bindHandlers(); // re-bind on replaced DOM
  }

  async function submitReview(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const fd = new FormData(form);
    const res = await fetch(form.action, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrftoken, 'X-Requested-With': 'XMLHttpRequest' },
      body: fd
    });
    const msg = document.getElementById('review-form-msg');
    if (res.ok) {
      if (msg) {
        msg.style.display = 'block';
        msg.classList.remove('text-danger'); msg.classList.add('text-success');
        msg.textContent = 'Thanks! Your review has been submitted.';
      }
      // reload reviews to reflect new/updated one
      await loadReviews({ sort: 'recent', page: 1 });
      // collapse form after submit
      toggleReviewForm(false);
    } else {
      const data = await res.json().catch(()=>({error:'Error'}));
      if (msg) {
        msg.style.display = 'block';
        msg.classList.remove('text-success'); msg.classList.add('text-danger');
        msg.textContent = data.error || 'Something went wrong.';
      }
    }
  }

  function onSortChange(e) {
    loadReviews({ sort: e.target.value, page: 1 });
  }

  function onPaginate(e) {
    // supports both .page-link (bootstrap) and .pagination-link (theme)
    const target = e.target.closest('a.page-link, a.pagination-link');
    if (!target) return;
    e.preventDefault();
    const url = new URL(target.href, window.location.origin);
    const page = url.searchParams.get('page') || 1;
    loadReviews({ page: page });
  }

  // ---- Theme toggler for Write/Cancel review ----
  function toggleReviewForm(show) {
    const area = container();
    if (!area) return;
    const btnWrite  = area.querySelector('.btn-write-review');
    const btnCancel = area.querySelector('.btn-cancel-review');
    const formWrap  = area.querySelector('.form-write-review');   // form section
    const listWrap  = area.querySelector('.cancel-review-wrap');  // comments list

    if (show) {
      if (formWrap) formWrap.style.display = '';
      if (listWrap) listWrap.style.display = 'none';
      if (btnWrite) btnWrite.style.display = 'none';
      if (btnCancel) btnCancel.style.display = '';
    } else {
      if (formWrap) formWrap.style.display = 'none';
      if (listWrap) listWrap.style.display = '';
      if (btnWrite) btnWrite.style.display = '';
      if (btnCancel) btnCancel.style.display = 'none';
    }
  }

  function bindHandlers() {
    const area = container();
    if (!area) return;

    // sort
    const sortSel = document.getElementById('reviews-sort');
    if (sortSel) sortSel.addEventListener('change', onSortChange);

    // pagination
    // supports theme ul.tf-pagination-list and bootstrap ul.pagination
    const pager = area.querySelector('#reviews-pagination, .tf-pagination-list, .pagination');
    if (pager) pager.addEventListener('click', onPaginate);

    // form submit
    const form = document.getElementById('review-form');
    if (form) form.addEventListener('submit', submitReview);

    // toggler buttons
    const btnWrite  = area.querySelector('.btn-write-review');
    const btnCancel = area.querySelector('.btn-cancel-review');

    // initial: hide form, show list (if both exist)
    const formWrap = area.querySelector('.form-write-review');
    const listWrap = area.querySelector('.cancel-review-wrap');
    if (formWrap && listWrap) toggleReviewForm(false);

    if (btnWrite) {
      btnWrite.addEventListener('click', function(e){
        e.preventDefault();
        toggleReviewForm(true);
      });
    }
    if (btnCancel) {
      btnCancel.addEventListener('click', function(e){
        e.preventDefault();
        toggleReviewForm(false);
      });
    }
  }

  // Expose a small API so your variant switcher can call setVariant
  window.QSReviews = {
    setVariant: function(newVariantId) {
      const c = container();
      if (!c) return;
      c.dataset.variant = newVariantId;
      loadReviews({ sort: 'recent', page: 1 });
    }
  };

  document.addEventListener('DOMContentLoaded', bindHandlers);
})();
