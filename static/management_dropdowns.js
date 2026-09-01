(function () {
  'use strict';

  const companyId = window.SCADA_MANAGEMENT_COMPANY_ID;
  if (companyId == null) {
    console.error('[MANAGEMENT DROPDOWN] Company ID is missing.');
    return;
  }

  const state = {
    options: {
      contract_codes: [],
      contract_names: [],
      product_codes: [],
      product_names: []
    },
    managementCalculations: [],
    menus: new WeakMap(),
    initialized: new WeakSet(),
    calcFilterInitialized: false
  };

  function log(...args) {
    console.log('[MANAGEMENT DROPDOWN]', ...args);
  }

  function error(...args) {
    console.error('[MANAGEMENT DROPDOWN]', ...args);
  }

  function normalize(value) {
    return String(value ?? '')
      .replace(/[۰-۹]/g, d => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d)))
      .replace(/[٠-٩]/g, d => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d)))
      .trim()
      .toLowerCase();
  }

  function normalizeCode(value) {
    const text = normalize(value);
    if (!text) return '';
    if (/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(text)) {
      const numberValue = Number(text);
      if (Number.isFinite(numberValue)) return String(numberValue);
    }
    return text;
  }

  function codeMatches(value, filter) {
    const left = normalizeCode(value);
    const right = normalizeCode(filter);
    if (!right) return true;
    if (left === right) return true;
    return normalize(value).includes(normalize(filter));
  }

  function uniqueSorted(values) {
    const map = new Map();
    (Array.isArray(values) ? values : []).forEach((value) => {
      const text = String(value ?? '').trim();
      if (!text) return;
      const key = normalize(text);
      if (!map.has(key)) map.set(key, text);
    });
    return [...map.values()].sort((a, b) => a.localeCompare(b, 'fa'));
  }

  function safeKey(value) {
    return String(value ?? '')
      .trim()
      .replace(/[^a-zA-Z0-9_\-]/g, '_') || 'calc';
  }

  function calcFilterId(calculation) {
    return 'management_calc_filter_' + safeKey(calculation.name);
  }

  async function loadOptions() {
    const url = '/management/options?company_id=' + encodeURIComponent(companyId);
    log('Loading DB options:', url);

    const response = await fetch(url, { cache: 'no-store' });
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json')
      ? await response.json()
      : { message: await response.text() };

    if (!response.ok) {
      throw new Error(data.message || ('HTTP ' + response.status));
    }

    state.options.contract_codes = uniqueSorted(data.contract_codes);
    state.options.contract_names = uniqueSorted(data.contract_names);
    state.options.product_codes = uniqueSorted(data.product_codes);
    state.options.product_names = uniqueSorted(data.product_names);

    log('Loaded DB options:', {
      contract_codes: state.options.contract_codes.length,
      contract_names: state.options.contract_names.length,
      product_codes: state.options.product_codes.length,
      product_names: state.options.product_names.length
    });
  }

  async function loadManagementConfig() {
    const url = '/management/config?company_id=' + encodeURIComponent(companyId);
    log('Loading ManagementPanel config:', url);

    const response = await fetch(url, { cache: 'no-store' });
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json')
      ? await response.json()
      : { message: await response.text() };

    if (!response.ok) {
      throw new Error(data.message || ('HTTP ' + response.status));
    }

    state.managementCalculations = (Array.isArray(data.calculations) ? data.calculations : [])
      .filter((item) => item && String(item.name || '').trim() && String(item.expression || '').trim())
      .map((item) => ({
        name: String(item.name).trim(),
        label: String(item.label || item.name).trim() || String(item.name).trim(),
        expression: String(item.expression).trim(),
        unit: String(item.unit || '').trim()
      }));

    log('Loaded ManagementPanel calculations:', state.managementCalculations);
    renderCalculationFilters();
    installDynamicCalculationFiltering();
  }

  function getOptionsForInput(input) {
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

  function createMenu(input) {
    const wrapper = document.createElement('div');
    wrapper.className = 'db-dropdown-wrapper';
    wrapper.style.position = 'relative';
    wrapper.style.width = '100%';

    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    const menu = document.createElement('div');
    menu.className = 'db-dropdown-menu';
    menu.hidden = true;
    Object.assign(menu.style, {
      position: 'absolute',
      top: 'calc(100% + 4px)',
      left: '0',
      right: '0',
      maxHeight: '240px',
      overflowY: 'auto',
      background: '#101418',
      border: '1px solid #52606d',
      borderRadius: '7px',
      boxShadow: '0 10px 25px rgba(0,0,0,.35)',
      zIndex: '100000',
      padding: '4px'
    });
    wrapper.appendChild(menu);

    return { wrapper, menu };
  }

  function renderMenu(input) {
    const record = state.menus.get(input);
    if (!record) return;

    const options = getOptionsForInput(input) || [];
    const query = normalize(input.value);
    const filtered = query
      ? options.filter((item) => normalize(item).includes(query))
      : options;

    record.menu.innerHTML = '';

    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.textContent = 'موردی پیدا نشد';
      empty.style.padding = '8px 10px';
      empty.style.color = '#8e99a3';
      record.menu.appendChild(empty);
    } else {
      filtered.forEach((value) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.textContent = value;
        Object.assign(item.style, {
          display: 'block',
          width: '100%',
          padding: '8px 10px',
          border: '0',
          borderRadius: '5px',
          background: 'transparent',
          color: '#fff',
          textAlign: 'right',
          cursor: 'pointer'
        });
        item.onmouseenter = () => { item.style.background = '#27313a'; };
        item.onmouseleave = () => { item.style.background = 'transparent'; };
        item.onclick = () => {
          input.value = value;
          closeMenu(input);
          input.dispatchEvent(new Event('change', { bubbles: true }));
          input.dispatchEvent(new Event('input', { bubbles: true }));
          log('Selected', input.id || input.getAttribute('data-k'), value);
        };
        record.menu.appendChild(item);
      });
    }

    record.menu.hidden = false;
  }

  function closeMenu(input) {
    const record = state.menus.get(input);
    if (record) record.menu.hidden = true;
  }

  function initInput(input) {
    if (!input || state.initialized.has(input)) return;
    if (!getOptionsForInput(input)) return;
    if (input.type === 'number' || input.classList.contains('jalali-date')) return;

    state.initialized.add(input);
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('data-db-dropdown-ready', '1');

    const record = createMenu(input);
    state.menus.set(input, record);

    input.addEventListener('focus', () => renderMenu(input));
    input.addEventListener('click', () => renderMenu(input));
    input.addEventListener('input', () => renderMenu(input));
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeMenu(input);
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        renderMenu(input);
        const first = record.menu.querySelector('button');
        if (first) first.focus();
      }
    });
  }

  function scan(root = document) {
    const selectors = [
      '#contract_code',
      '#contract_name',
      '#product_code',
      '#product_name',
      '#new_contract_code',
      '#new_contract_name',
      '#contractProducts input[data-k="product_code"]',
      '#contractProducts input[data-k="product_name"]'
    ];

    selectors.forEach((selector) => {
      root.querySelectorAll(selector).forEach(initInput);
    });
  }

  function setupOutsideClick() {
    document.addEventListener('click', (event) => {
      document.querySelectorAll('.db-dropdown-wrapper').forEach((wrapper) => {
        const input = wrapper.querySelector('input[data-db-dropdown-ready]');
        if (input && !wrapper.contains(event.target)) closeMenu(input);
      });
    });
  }

  function renderCalculationFilters() {
    const grid = document.querySelector('.filters .grid');
    if (!grid) {
      log('Management filter grid not found yet.');
      return;
    }

    grid.querySelectorAll('.management-calculation-filter').forEach((el) => el.remove());

    state.managementCalculations.forEach((calculation) => {
      const field = document.createElement('div');
      field.className = 'field management-calculation-filter';
      field.dataset.calculationName = calculation.name;

      const label = document.createElement('label');
      label.textContent = calculation.label + (calculation.unit ? ' (' + calculation.unit + ')' : '');

      const input = document.createElement('input');
      input.id = calcFilterId(calculation);
      input.type = 'text';
      input.placeholder = 'فیلتر ' + calculation.label;
      input.autocomplete = 'off';
      input.setAttribute('data-management-calculation-filter', calculation.name);

      input.addEventListener('input', () => {
        log('Management calculation filter changed:', calculation.name, input.value);
      });

      field.appendChild(label);
      field.appendChild(input);
      grid.appendChild(field);
    });

    state.calcFilterInitialized = true;
    log('Rendered ManagementPanel calculation filters:', state.managementCalculations.map(x => x.name));
  }

  function getCalculationFilterValues() {
    const result = {};
    state.managementCalculations.forEach((calculation) => {
      const input = document.getElementById(calcFilterId(calculation));
      if (!input) return;
      const value = input.value.trim();
      if (value) result[calculation.name] = value;
    });
    return result;
  }

  function calculationValueMatches(value, filter) {
    const wanted = normalize(filter);
    if (!wanted) return true;

    if (value === null || value === undefined || value === '') return false;

    const valueText = normalize(value);
    if (valueText === wanted || valueText.includes(wanted)) return true;

    const valueNumber = Number(value);
    const filterNumber = Number(normalize(filter));
    if (Number.isFinite(valueNumber) && Number.isFinite(filterNumber)) {
      return Math.abs(valueNumber - filterNumber) < 1e-9;
    }

    return false;
  }

  function applyCalculationFilters(data) {
    const filters = getCalculationFilterValues();
    const names = Object.keys(filters);
    if (!names.length) return data;
    if (!data || !Array.isArray(data.rows) || !Array.isArray(data.columns)) return data;

    const filteredRows = data.rows.filter((row) => {
      return names.every((name) => calculationValueMatches(row[name], filters[name]));
    });

    log('Management calculation filters applied:', {
      filters,
      before: data.rows.length,
      after: filteredRows.length
    });

    return Object.assign({}, data, {
      rows: filteredRows,
      count: filteredRows.length
    });
  }

  function installDynamicCalculationFiltering() {
    if (typeof window.renderTable !== 'function' || window.renderTable.__managementCalculationFilter) {
      return;
    }

    const originalRenderTable = window.renderTable;
    const wrappedRenderTable = function (data) {
      const filteredData = applyCalculationFilters(data);
      window.__SCADA_MANAGEMENT_LAST_DATA = data;
      window.__SCADA_MANAGEMENT_LAST_FILTERED_DATA = filteredData;
      const result = originalRenderTable.call(this, filteredData);

      const summary = document.getElementById('summary');
      if (summary && getCalculationFilterValues() && Object.keys(getCalculationFilterValues()).length) {
        summary.textContent = 'تعداد ردیف: ' + (filteredData.count ?? 0);
      }
      return result;
    };

    wrappedRenderTable.__managementCalculationFilter = true;
    wrappedRenderTable.__managementOriginal = originalRenderTable;
    window.renderTable = wrappedRenderTable;
    log('ManagementPanel calculation filtering installed.');
  }

  function installProductFilterGuard() {
    if (typeof window.loadData !== 'function' || window.loadData.__productFilterGuard) return;

    const originalLoadData = window.loadData;
    const guardedLoadData = async function () {
      const result = await originalLoadData.apply(this, arguments);

      const filterInput = document.getElementById('product_code');
      const filterValue = filterInput ? filterInput.value.trim() : '';
      if (!filterValue) return result;

      const tbody = document.getElementById('tbody');
      if (!tbody) return result;

      const rows = Array.from(tbody.querySelectorAll('tr'));
      let visible = 0;

      rows.forEach((row) => {
        const cells = row.querySelectorAll('td');
        const cell = cells.length >= 4 ? cells[3].textContent.trim() : '';
        const match = codeMatches(cell, filterValue);
        row.style.display = match ? '' : 'none';
        if (match) visible += 1;
      });

      const summary = document.getElementById('summary');
      if (summary) {
        summary.textContent = 'نتیجه فیلتر کد محصول: ' + visible + ' ردیف';
      }

      console.log('[MANAGEMENT] PRODUCT CODE FILTER GUARD:', {
        requested: filterValue,
        visibleRows: visible,
        totalRows: rows.length
      });

      return result;
    };

    guardedLoadData.__productFilterGuard = true;
    window.loadData = guardedLoadData;
    log('Product-code filter guard installed.');
  }

  function setupCalculationFilterClear() {
    const originalClear = window.clearFilters;
    if (typeof originalClear !== 'function' || originalClear.__managementCalculationClear) return;

    const wrappedClear = function () {
      state.managementCalculations.forEach((calculation) => {
        const input = document.getElementById(calcFilterId(calculation));
        if (input) input.value = '';
      });
      return originalClear.apply(this, arguments);
    };

    wrappedClear.__managementCalculationClear = true;
    window.clearFilters = wrappedClear;
    log('Management calculation filters clear hook installed.');
  }

  async function refresh() {
    try {
      await loadOptions();
      scan(document);
      installProductFilterGuard();
      setupCalculationFilterClear();
      await loadManagementConfig();
      scan(document);
      installProductFilterGuard();
      setupCalculationFilterClear();
      log('DB-backed dropdowns and Flow-defined Management filters initialized.');
    } catch (err) {
      error('Failed to initialize management helpers:', err);
    }
  }

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach((node) => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        scan(node);
        if (!state.calcFilterInitialized && node.querySelector && node.querySelector('.filters .grid')) {
          renderCalculationFilters();
          installDynamicCalculationFiltering();
        }
      });
    }
  });

  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  }

  setupOutsideClick();
  refresh();
})();
