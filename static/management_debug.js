(function () {
  'use strict';

  function safeJson(value) {
    try { return JSON.stringify(value, null, 2); }
    catch (_) { return String(value); }
  }

  function createPanel() {
    if (document.getElementById('scada-management-debug')) return;

    const root = document.createElement('div');
    root.id = 'scada-management-debug';
    Object.assign(root.style, {
      position: 'fixed',
      right: '12px',
      bottom: '12px',
      width: 'min(900px, calc(100vw - 24px))',
      height: 'min(520px, calc(100vh - 24px))',
      zIndex: '2147483647',
      background: '#0b0f14',
      color: '#d7e0ea',
      border: '1px solid #52606d',
      borderRadius: '10px',
      boxShadow: '0 12px 35px rgba(0,0,0,.55)',
      fontFamily: 'Consolas, monospace',
      fontSize: '12px',
      display: 'flex',
      flexDirection: 'column',
      direction: 'ltr'
    });

    const head = document.createElement('div');
    Object.assign(head.style, {
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '8px 10px', background: '#111923', borderBottom: '1px solid #334155'
    });

    const title = document.createElement('b');
    title.textContent = 'SCADA Management Debug';

    const actions = document.createElement('div');
    Object.assign(actions.style, { display: 'flex', gap: '6px' });

    function addButton(label, handler) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      Object.assign(b.style, {
        border: '1px solid #52606d', background: '#1f2937', color: '#fff',
        borderRadius: '5px', padding: '4px 8px', cursor: 'pointer'
      });
      b.onclick = handler;
      actions.appendChild(b);
    }

    const body = document.createElement('pre');
    Object.assign(body.style, {
      margin: 0, padding: '10px', overflow: 'auto', whiteSpace: 'pre-wrap',
      lineHeight: '1.45', flex: '1'
    });

    head.appendChild(title);
    head.appendChild(actions);
    root.appendChild(head);
    root.appendChild(body);
    document.body.appendChild(root);

    const entries = [];
    function log(level, message, data) {
      const item = {
        time: new Date().toISOString(),
        level,
        message,
        data
      };
      entries.push(item);
      if (entries.length > 500) entries.shift();
      body.textContent = entries.map(e => {
        const d = e.data === undefined ? '' : ' | ' + safeJson(e.data);
        return '[' + e.time + '] ' + e.level + ' ' + e.message + d;
      }).join('\n');
      body.scrollTop = body.scrollHeight;
    }

    addButton('CLEAR', () => {
      entries.length = 0;
      body.textContent = '';
    });

    addButton('COPY', async () => {
      try {
        await navigator.clipboard.writeText(body.textContent);
        log('INFO', 'Log copied');
      } catch (e) {
        log('ERROR', 'Clipboard copy failed', String(e));
      }
    });

    addButton('DOWNLOAD', () => {
      const blob = new Blob([body.textContent], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'management-debug.log';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });

    addButton('INSPECT', inspect);
    addButton('HIDE', () => { root.style.display = 'none'; });

    async function inspect() {
      const companyId = window.COMPANY_ID ?? window.SCADA_MANAGEMENT_COMPANY_ID ?? null;
      const filters = {};
      document.querySelectorAll('input[id]').forEach(input => {
        if (input.id.startsWith('management_calc_filter_')) filters[input.id] = input.value;
      });

      const calcFields = Array.from(document.querySelectorAll('.management-calculation-filter')).map(el => ({
        name: el.dataset.calculationName || null,
        text: el.textContent.trim(),
        inputs: Array.from(el.querySelectorAll('input')).map(x => ({ id: x.id, value: x.value }))
      }));

      log('INFO', 'PAGE STATE', {
        companyId,
        loadConfigExists: typeof window.loadConfig === 'function',
        loadDataExists: typeof window.loadData === 'function',
        renderTableExists: typeof window.renderTable === 'function',
        calcFields,
        calcFilterInputs: filters
      });

      const urls = [
        '/management/config?company_id=' + encodeURIComponent(companyId || ''),
        '/management/data?company_id=' + encodeURIComponent(companyId || '')
      ];

      for (const url of urls) {
        try {
          const started = performance.now();
          const response = await fetch(url, { cache: 'no-store', headers: { Accept: 'application/json' } });
          const text = await response.text();
          let data;
          try { data = JSON.parse(text); } catch (_) { data = text.slice(0, 5000); }
          log(response.ok ? 'INFO' : 'ERROR', 'API RESULT', {
            url,
            status: response.status,
            ms: Math.round(performance.now() - started),
            data
          });
        } catch (e) {
          log('ERROR', 'API REQUEST FAILED', { url, error: String(e) });
        }
      }

      const table = {
        headers: Array.from(document.querySelectorAll('#thead th')).map(x => x.textContent.trim()),
        rowCount: document.querySelectorAll('#tbody tr').length,
        firstRow: Array.from(document.querySelectorAll('#tbody tr:first-child td')).map(x => x.textContent.trim())
      };
      log('INFO', 'TABLE STATE', table);
    }

    window.addEventListener('error', event => {
      log('ERROR', 'GLOBAL JS ERROR', {
        message: event.message,
        source: event.filename,
        line: event.lineno,
        column: event.colno
      });
    });

    window.addEventListener('unhandledrejection', event => {
      log('ERROR', 'UNHANDLED PROMISE', { reason: String(event.reason) });
    });

    window.SCADA_MANAGEMENT_DEBUG = { log, inspect };
    log('INFO', 'DEBUG LOGGER INSTALLED', {
      companyId: window.COMPANY_ID ?? window.SCADA_MANAGEMENT_COMPANY_ID ?? null,
      href: location.href
    });
    setTimeout(inspect, 400);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', createPanel, { once: true });
  } else {
    createPanel();
  }
})();
