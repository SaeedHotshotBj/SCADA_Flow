(function () {
  'use strict';

  const companyId = window.SCADA_MANAGEMENT_COMPANY_ID;
  if (companyId == null) {
    console.error('[MANAGEMENT FLOW] Company ID is missing.');
    return;
  }

  const state = {
    options: {contract_codes: [], contract_names: [], product_codes: [], product_names: []},
    menus: new WeakMap(),
    initialized: new WeakSet()
  };

  const log = (...args) => console.log('[MANAGEMENT FLOW]', ...args);

  function normalize(value) {
    return String(value ?? '')
      .replace(/[۰-۹]/g, d => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d)))
      .replace(/[٠-٩]/g, d => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d)))
      .trim()
      .toLowerCase();
  }

  function uniqueSorted(values) {
    const map = new Map();
    (Array.isArray(values) ? values : []).forEach(value => {
      const text = String(value ?? '').trim();
      if (!text) return;
      const key = normalize(text);
      if (!map.has(key)) map.set(key, text);
    });
    return [...map.values()].sort((a, b) => a.localeCompare(b, 'fa'));
  }

  async function json(url) {
    const response = await fetch(url, {cache: 'no-store', headers: {'Accept': 'application/json'}});
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); }
    catch (_) { throw new Error('پاسخ نامعتبر از ' + url); }
    if (!response.ok) throw new Error(data.message || ('HTTP ' + response.status));
    return data;
  }

  async function loadOptions() {
    const data = await json('/management/options?company_id=' + encodeURIComponent(companyId));
    state.options.contract_codes = uniqueSorted(data.contract_codes);
    state.options.contract_names = uniqueSorted(data.contract_names);
    state.options.product_codes = uniqueSorted(data.product_codes);
    state.options.product_names = uniqueSorted(data.product_names);
    log('DB options:', state.options);
  }

  function optionsFor(input) {
    const id = input.id || '';
    const key = input.getAttribute('data-db-dropdown');
    if (key && state.options[key]) return state.options[key];
    if (id === 'contract_code' || id === 'new_contract_code') return state.options.contract_codes;
    if (id === 'contract_name' || id === 'new_contract_name') return state.options.contract_names;
    if (id === 'product_code') return state.options.product_codes;
    if (id === 'product_name') return state.options.product_names;
    const dataKey = input.getAttribute('data-k');
    if (dataKey === 'product_code') return state.options.product_codes;
    if (dataKey === 'product_name') return state.options.product_names;
    return null;
  }

  function closeMenu(input) {
    const rec = state.menus.get(input);
    if (rec) rec.menu.hidden = true;
  }

  function renderMenu(input) {
    const rec = state.menus.get(input);
    if (!rec) return;
    const query = normalize(input.value);
    const options = optionsFor(input) || [];
    const filtered = query ? options.filter(v => normalize(v).includes(query)) : options;
    rec.menu.innerHTML = '';

    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.textContent = 'موردی پیدا نشد';
      empty.style.cssText = 'padding:8px 10px;color:#8e99a3;';
      rec.menu.appendChild(empty);
    } else {
      filtered.forEach(value => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = value;
        button.style.cssText = 'display:block;width:100%;padding:8px 10px;border:0;border-radius:5px;background:transparent;color:#fff;text-align:right;cursor:pointer;';
        button.onmouseenter = () => { button.style.background = '#27313a'; };
        button.onmouseleave = () => { button.style.background = 'transparent'; };
        button.onclick = () => {
          input.value = value;
          closeMenu(input);
          input.dispatchEvent(new Event('change', {bubbles: true}));
          input.dispatchEvent(new Event('input', {bubbles: true}));
          log('Dropdown selected:', input.id || input.getAttribute('data-k'), value);
        };
        rec.menu.appendChild(button);
      });
    }
    rec.menu.hidden = false;
  }

  function initDropdown(input) {
    if (!input || state.initialized.has(input) || !optionsFor(input)) return;
    if (input.type === 'number' || input.classList.contains('jalali-date')) return;

    state.initialized.add(input);
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('data-db-dropdown-ready', '1');

    const wrapper = document.createElement('div');
    wrapper.className = 'db-dropdown-wrapper';
    wrapper.style.cssText = 'position:relative;width:100%;';
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    const menu = document.createElement('div');
    menu.className = 'db-dropdown-menu';
    menu.hidden = true;
    menu.style.cssText = 'position:absolute;top:calc(100% + 4px);left:0;right:0;max-height:240px;overflow-y:auto;background:#101418;border:1px solid #52606d;border-radius:7px;box-shadow:0 10px 25px rgba(0,0,0,.35);z-index:100000;padding:4px;';
    wrapper.appendChild(menu);

    const rec = {wrapper, menu};
    state.menus.set(input, rec);
    input.addEventListener('focus', () => renderMenu(input));
    input.addEventListener('click', () => renderMenu(input));
    input.addEventListener('input', () => renderMenu(input));
    input.addEventListener('keydown', event => { if (event.key === 'Escape') closeMenu(input); });
  }

  function scan(root = document) {
    [
      '#contract_code','#contract_name','#product_code','#product_name',
      '#new_contract_code','#new_contract_name',
      '#contractProducts input[data-k="product_code"]',
      '#contractProducts input[data-k="product_name"]'
    ].forEach(selector => root.querySelectorAll(selector).forEach(initDropdown));
  }

  function setupOutsideClick() {
    document.addEventListener('click', event => {
      document.querySelectorAll('.db-dropdown-wrapper').forEach(wrapper => {
        const input = wrapper.querySelector('input[data-db-dropdown-ready]');
        if (input && !wrapper.contains(event.target)) closeMenu(input);
      });
    });
  }

  setupOutsideClick();

  const observer = new MutationObserver(mutations => {
    mutations.forEach(mutation => {
      mutation.addedNodes.forEach(node => {
        if (node.nodeType === Node.ELEMENT_NODE) scan(node);
      });
    });
  });

  if (document.body) observer.observe(document.body, {childList: true, subtree: true});

  (async function boot() {
    try {
      await loadOptions();
      scan(document);
    } catch (error) {
      console.error('[MANAGEMENT FLOW] Initialization error:', error);
    }
  })();
})();
