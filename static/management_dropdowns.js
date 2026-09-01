(function () {
  'use strict';

  const companyId = window.SCADA_MANAGEMENT_COMPANY_ID;
  if (companyId == null) {
    console.error('[MANAGEMENT FLOW] Company ID is missing.');
    return;
  }

  const state = {
    options: {contract_codes: [], contract_names: [], product_codes: [], product_names: []},
    calculations: [],
    menus: new WeakMap(),
    initialized: new WeakSet(),
    renderWrapped: false,
    clearWrapped: false
  };

  const log = (...args) => console.log('[MANAGEMENT FLOW]', ...args);

  function normalize(value) {
    return String(value ?? '')
      .replace(/[۰-۹]/g, d => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d)))
      .replace(/[٠-٩]/g, d => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d)))
      .trim()
      .toLowerCase();
  }

  function numericEqual(a, b) {
    const x = Number(normalize(a));
    const y = Number(normalize(b));
    return Number.isFinite(x) && Number.isFinite(y) && Math.abs(x - y) < 1e-9;
  }

  function matches(value, wanted) {
    const query = normalize(wanted);
    if (!query) return true;
    if (value == null) return false;
    const text = normalize(value);
    return text === query || text.includes(query) || numericEqual(value, wanted);
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

  function filterId(name) {
    return 'management_flow_calc_' + String(name ?? '').replace(/[^a-zA-Z0-9_-]/g, '_');
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

  async function loadFlowConfig() {
    const data = await json('/management/config?company_id=' + encodeURIComponent(companyId));
    state.calculations = (Array.isArray(data.calculations) ? data.calculations : [])
      .filter(item => item && String(item.name || '').trim() && String(item.expression || '').trim())
      .map(item => ({
        name: String(item.name).trim(),
        label: String(item.label || item.name).trim() || String(item.name).trim(),
        expression: String(item.expression).trim(),
        unit: String(item.unit || '').trim()
      }));
    log('FLOW calculations:', state.calculations);
    renderCalculationFilters();
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

  function renderCalculationFilters() {
    const grid = document.querySelector('.filters .grid');
    if (!grid) return false;

    grid.querySelectorAll('.management-flow-calculation-filter').forEach(el => el.remove());

    state.calculations.forEach(calc => {
      const field = document.createElement('div');
      field.className = 'field management-flow-calculation-filter';
      field.dataset.calculationName = calc.name;

      const label = document.createElement('label');
      label.textContent = calc.label + (calc.unit ? ' (' + calc.unit + ')' : '');

      const input = document.createElement('input');
      input.id = filterId(calc.name);
      input.type = 'text';
      input.autocomplete = 'off';
      input.placeholder = 'فیلتر ' + calc.label;
      input.dataset.managementCalculationFilter = calc.name;

      field.appendChild(label);
      field.appendChild(input);
      grid.appendChild(field);
    });

    log('Calculation filter controls:', state.calculations.map(x => x.name));
    return true;
  }

  function currentCalculationFilters() {
    const filters = {};
    state.calculations.forEach(calc => {
      const input = document.getElementById(filterId(calc.name));
      if (!input) return;
      const value = input.value.trim();
      if (value) filters[calc.name] = value;
    });
    return filters;
  }

  function applyCalculationFilters(data) {
    const filters = currentCalculationFilters();
    const names = Object.keys(filters);
    if (!names.length || !data || !Array.isArray(data.rows)) return data;

    const rows = data.rows.filter(row => names.every(name => matches(row[name], filters[name])));
    log('Calculated filters applied:', {filters, before: data.rows.length, after: rows.length});
    return Object.assign({}, data, {rows, count: rows.length});
  }

  function installRenderWrapper() {
    if (state.renderWrapped || typeof window.renderTable !== 'function') return;
    const original = window.renderTable;
    const wrapped = function (data) {
      const filtered = applyCalculationFilters(data);
      window.__SCADA_MANAGEMENT_FLOW_DATA = data;
      window.__SCADA_MANAGEMENT_FLOW_FILTERED_DATA = filtered;
      return original.call(this, filtered);
    };
    wrapped.__managementFlowCalculationWrapper = true;
    window.renderTable = wrapped;
    state.renderWrapped = true;
    log('renderTable wrapper installed.');
  }

  function installClearWrapper() {
    if (state.clearWrapped || typeof window.clearFilters !== 'function') return;
    const original = window.clearFilters;
    const wrapped = function () {
      state.calculations.forEach(calc => {
        const input = document.getElementById(filterId(calc.name));
        if (input) input.value = '';
      });
      return original.apply(this, arguments);
    };
    wrapped.__managementFlowClearWrapper = true;
    window.clearFilters = wrapped;
    state.clearWrapped = true;
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
    installRenderWrapper();
    installClearWrapper();
    if (!document.querySelector('.management-flow-calculation-filter')) renderCalculationFilters();
  });

  if (document.body) observer.observe(document.body, {childList: true, subtree: true});

  (async function boot() {
    try {
      await loadOptions();
      await loadFlowConfig();
      scan(document);
      installRenderWrapper();
      installClearWrapper();
    } catch (error) {
      console.error('[MANAGEMENT FLOW] Initialization error:', error);
    }
  })();
})();
