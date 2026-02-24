(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const overlay = $('[data-cart-overlay]');
  const drawer = $('[data-cart-drawer]');
  const body = $('[data-cart-body]');
  const badge = $('[data-cart-badge]');
  const subtotalEl = $('[data-cart-subtotal]');
  const totalEl = $('[data-cart-total]');
  const freeOverEl = $('[data-cart-freeover]');
  const toast = $('[data-cart-toast]');

  const openBtns = $$('[data-cart-open]');
  const closeBtns = $$('[data-cart-close]');
  const checkoutBtn = $('[data-cart-checkout]');

  // size modal
  const sizeOverlay = $('[data-size-overlay]');
  const sizeModal = $('[data-size-modal]');
  const sizeTitle = $('[data-size-title]');
  const sizePrice = $('[data-size-price]');
  const sizeOptions = $('[data-size-options]');
  const sizeClose = $('[data-size-close]');
  const sizeConfirm = $('[data-size-confirm]');

  let pending = null; // {productId, qty, title, price}

  function openCart() {
    overlay.classList.add('show');
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
  }

  function closeCart() {
    overlay.classList.remove('show');
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
  }

  function showToast(text) {
    toast.textContent = text || 'Adicionado ao carrinho!';
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 1600);
  }

  async function apiGetCart() {
    const res = await fetch('/api/cart', { headers: { 'Accept': 'application/json' } });
    return await res.json();
  }

  async function apiAdd(product_id, qty = 1, size = '') {
    const res = await fetch('/api/cart/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id, qty, size })
    });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok && data.ok, status: res.status, data };
  }

  async function apiUpdate(key, qty) {
    const res = await fetch('/api/cart/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, qty })
    });
    return await res.json();
  }

  async function apiRemove(key) {
    const res = await fetch('/api/cart/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key })
    });
    return await res.json();
  }

  function setBadge(count) {
    if (!badge) return;
    if (count > 0) {
      badge.style.display = 'inline-flex';
      badge.textContent = String(count);
    } else {
      badge.style.display = 'none';
      badge.textContent = '0';
    }
  }

  function escapeHtml(str) {
    return String(str || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function renderCart(cart) {
    setBadge(cart.count || 0);
    subtotalEl.textContent = cart.subtotal_brl || 'R$ 0,00';
    totalEl.textContent = cart.total_brl || 'R$ 0,00';
    if (freeOverEl) freeOverEl.textContent = cart.free_over_brl || 'R$ 0,00';

    if (!cart.items || cart.items.length === 0) {
      body.innerHTML = `<div class="cart-empty">Seu carrinho está vazio.</div>`;
      return;
    }

    body.innerHTML = cart.items.map(it => {
      const sizeTxt = it.size ? ` <span class="cart-size">(${it.size})</span>` : '';
      const img = it.image_url
        ? `<img src="${it.image_url}" alt="">`
        : `<div class="cart-thumb-ph">—</div>`;

      return `
        <div class="cart-item" data-key="${it.key}">
          <div class="cart-thumb">${img}</div>

          <div class="cart-info">
            <div class="cart-name">${escapeHtml(it.name)}${sizeTxt}</div>
            <div class="cart-line">${it.qty} x ${it.unit_price_brl}</div>
          </div>

          <button class="trash" type="button" data-remove title="Remover">🗑</button>

          <div class="qty">
            <button type="button" class="qty-btn" data-dec>-</button>
            <div class="qty-val">${it.qty}</div>
            <button type="button" class="qty-btn" data-inc>+</button>
          </div>

          <div class="cart-price">${it.line_total_brl}</div>
        </div>
      `;
    }).join('');

    $$('.cart-item', body).forEach(row => {
      const key = row.getAttribute('data-key');
      const dec = $('[data-dec]', row);
      const inc = $('[data-inc]', row);
      const rm = $('[data-remove]', row);

      dec.addEventListener('click', async () => {
        const current = parseInt($('.qty-val', row).textContent, 10) || 1;
        const next = Math.max(0, current - 1);
        const data = await apiUpdate(key, next);
        renderCart(data.cart);
      });

      inc.addEventListener('click', async () => {
        const current = parseInt($('.qty-val', row).textContent, 10) || 1;
        const next = Math.min(99, current + 1);
        const data = await apiUpdate(key, next);
        renderCart(data.cart);
      });

      rm.addEventListener('click', async () => {
        const data = await apiRemove(key);
        renderCart(data.cart);
      });
    });
  }

  function openSizeModal(title, price, sizes, onPick) {
    sizeTitle.textContent = title || 'Selecione o tamanho';
    sizePrice.textContent = price ? String(price) : '';
    sizeOptions.innerHTML = '';

    let chosen = '';

    sizes.forEach((s) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'size-pill';
      btn.textContent = s;
      btn.addEventListener('click', () => {
        $$('.size-pill', sizeOptions).forEach(x => x.classList.remove('active'));
        btn.classList.add('active');
        chosen = s;
      });
      sizeOptions.appendChild(btn);
    });

    sizeOverlay.classList.add('show');
    sizeModal.classList.add('show');

    sizeConfirm.onclick = () => {
      if (!chosen) {
        showToast('Escolha um tamanho.');
        return;
      }
      closeSizeModal();
      onPick(chosen);
    };
  }

  function closeSizeModal() {
    sizeOverlay.classList.remove('show');
    sizeModal.classList.remove('show');
    pending = null;
  }

  // Global: botão COMPRAR
  window.drawerAddToCart = async function (productId, qty = 1, opts = {}) {
    const title = opts.title || 'Produto';
    const price = opts.price || '';
    const result = await apiAdd(productId, qty, '');

    if (!result.ok && result.data && result.data.need_size) {
      pending = { productId, qty, title, price };
      const sizes = result.data.sizes || [];
      openSizeModal(title, price, sizes, async (chosen) => {
        const r2 = await apiAdd(productId, qty, chosen);
        if (r2.ok) {
          renderCart(r2.data.cart);
          openCart();
          showToast(r2.data.message || 'Adicionado ao carrinho!');
        } else {
          showToast((r2.data && r2.data.error) || 'Erro ao adicionar.');
        }
      });
      return;
    }

    if (result.ok) {
      renderCart(result.data.cart);
      openCart();
      showToast(result.data.message || 'Adicionado ao carrinho!');
    } else {
      showToast((result.data && result.data.error) || 'Erro ao adicionar.');
    }
  };

  // Bind open
  openBtns.forEach(btn => btn.addEventListener('click', async () => {
    const cart = await apiGetCart();
    renderCart(cart);
    openCart();
  }));

  // Bind close
  closeBtns.forEach(btn => btn.addEventListener('click', closeCart));
  overlay.addEventListener('click', closeCart);

  // Size modal close
  sizeOverlay.addEventListener('click', closeSizeModal);
  sizeClose.addEventListener('click', closeSizeModal);

  // Checkout
  if (checkoutBtn) {
    checkoutBtn.addEventListener('click', () => {
      window.location.href = '/checkout';
    });
  }

})();