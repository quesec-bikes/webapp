// FBT — single source (robust + configurable)
// - Auto hides entire section if there are NO linked items
// - Renders self + linked when present
// - API URL from data-api-fbt (fallback: /api/fbt/)
// - Cart batch endpoint from data-cart-batch (fallback: /cart/batch-add/)
(function () {
  const qs  = (s, c=document) => c.querySelector(s);
  const qsa = (s, c=document) => Array.from(c.querySelectorAll(s));
  const hide = (el) => { if (el) el.style.display = 'none'; };
  const show = (el) => { if (el) el.style.display = ''; };
  const inr = (n) => `₹${Math.round(Number(n || 0)).toLocaleString('en-IN')}`;

  const getVariantId = (root) =>
    root.getAttribute('data-source-variant') ||
    new URL(location.href).searchParams.get('variant') || '';

  const getApiFbt = (root) => root.getAttribute('data-api-fbt') || '/api/fbt/';
  const getCartBatch = (root) => root.getAttribute('data-cart-batch') || '/cart/batch-add/';

  const csrf = () => (qs('[name=csrfmiddlewaretoken]')?.value || '');

  // ---------- tiny view helpers ----------
  const priceBlock = (mrp, price) => {
    const M = Number(mrp || 0), P = Number(price || 0);
    if (M && P && P < M) {
      return `<div class="compare-at-price">${inr(M)}</div>
              <div class="price-on-sale">${inr(P)}</div>`;
    }
    return `<div class="price-on-sale">${inr(P || M || 0)}</div>`;
  };
  const rowHTML = (it) => {
    const title = [it.product_title, it.variant_title ? `(${it.variant_title})` : ""]
      .filter(Boolean).join(' ').trim();
    return `
      <div class="tf-bundle-product-item item-has-checkox check mb_15">
          <div class="form-check mt-1">
            <input type="checkbox" class="form-check-input fbt-check" checked data-vid="${Number(it.variant_id)}">
          </div>
          <div class="tf-product-bundle-image">
              ${it.image ? `<a href="${it.product_url || it.url || '#'}">
                  <img src="${it.image}" alt="${title}">
              </a>` : ''}
          </div>
          <div class="tf-product-bundle-infos">
              <a href="${it.product_url || it.url || '#'}" class="tf-product-bundle-title">${title}</a>
              <div class="tf-product-bundle-price">
                ${priceBlock(it.compare_at_price, it.price)}
              </div>
          </div>
      </div>
    `;
  };

  const renderBox = (rows) => `
    
      <div class="title">Frequently Bought Together</div>
      <form class="tf-product-form-bundle">
          <div class="tf-bundle-products">
              <div id="fbtItems">${rows.map(rowHTML).join('')}</div>
          </div>
          <div class="tf-product-bundle-total-submit">
              <span class="text">Total price:</span>
              <div id="fbtTotalSale" class="compare-at-price"></div>
              <div id="fbtTotalCompare" class="price-on-sale"></div>
          </div>
          <button id="fbtAddBtn"
              class="tf-btn w-100 radius-3 justify-content-center btn-primary animate-hover-btn">Add selected to cart</button>
      </form>
    
  `;

  // ---------- main ----------
  document.addEventListener('DOMContentLoaded', async () => {
    const root = qs('[data-fbt-root]');
    if (!root) return;

    const variantId = getVariantId(root);
    if (!variantId) { hide(root); return; }

    const apiFbt = getApiFbt(root);
    const cartBatch = getCartBatch(root);

    // detect optional skeleton
    let itemsWrap  = qs('#fbtItems', root);
    let totalsWrap = qs('#fbtTotals', root);
    let saleEl     = qs('#fbtTotalSale', root);
    let compEl     = qs('#fbtTotalCompare', root);
    let addBtn     = qs('#fbtAddBtn', root);

    // fetch payload
    let payload;
    try {
      const res = await fetch(`${apiFbt}?variant=${encodeURIComponent(variantId)}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin'
      });
      if (!res.ok) throw 0;
      payload = await res.json();
    } catch {
      hide(root); return;
    }

    const all = Array.isArray(payload.items) ? payload.items : [];

    // If API ever returns self + linked, mark linked by !_isMain.
    // If API returns only linked, fallback handle below still works.
    const linked = all.filter(x => !x._isMain);
    const hasLinked = linked.length ? true : all.length > 0 && !all.some(x => x._isMain);

    if (!hasLinked) { hide(root); return; }

    // If no skeleton, render our own box
    if (!itemsWrap || !saleEl || !compEl || !addBtn || !totalsWrap) {
      root.innerHTML = renderBox(all);
      itemsWrap  = qs('#fbtItems', root);
      totalsWrap = qs('#fbtTotals', root);
      saleEl     = qs('#fbtTotalSale', root);
      compEl     = qs('#fbtTotalCompare', root);
      addBtn     = qs('#fbtAddBtn', root);
    } else {
      // skeleton present — fill rows
      itemsWrap.innerHTML = all.map(rowHTML).join('');
    }

    // totals + interactions
    const recompute = () => {
      const vids = qsa('.fbt-check:checked', root).map(c => Number(c.dataset.vid));
      const picked = all.filter(r => vids.includes(Number(r.variant_id)));
      const sumSale = picked.reduce((a,b)=> a + Number(b.price || 0), 0);
      const sumComp = picked.reduce((a,b)=> a + Number(b.compare_at_price || 0), 0);
      saleEl.textContent = inr(sumSale);
      compEl.textContent = (sumComp > sumSale) ? inr(sumComp) : '';
      show(totalsWrap);
      addBtn.disabled = vids.length === 0;
    };
    recompute();
    root.addEventListener('change', (e) => {
      if (e.target.classList.contains('fbt-check')) recompute();
    });

    // batch add
    addBtn.addEventListener('click', async () => {
      const lines = qsa('.fbt-check:checked', root).map(c => ({
        variant_id: Number(c.dataset.vid), qty: 1
      }));
      if (!lines.length) return;
      try {
        const r = await fetch(cartBatch, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': csrf()
          },
          credentials: 'same-origin',
          body: JSON.stringify({ lines })
        });
        if (!r.ok) throw 0;
        window.location.href = '/cart/';
      } catch { /* optionally toast */ }
    });
  });
})();
