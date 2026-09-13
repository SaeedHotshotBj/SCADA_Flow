(function () {
    const FIELD_IDS = [
        'contract_code', 'contract_name', 'product_code', 'product_name',
        'contract_date_from', 'contract_date_to', 'delivery_date_from', 'delivery_date_to',
        'min_ordered', 'max_ordered', 'description'
    ];

    function esc(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function value(id) {
        return document.getElementById(id)?.value?.trim() || '';
    }

    function ensureProductionDateFields() {
        const grid = document.querySelector('.filters .grid');
        if (!grid || document.getElementById('production_date_from')) return;

        const make = (id, label, placeholder) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'field';
            wrapper.innerHTML = `<label>${label}</label><input id="${id}" class="jalali-date" placeholder="${placeholder}" autocomplete="off" readonly>`;
            return wrapper;
        };

        grid.appendChild(make('production_date_from', 'تاریخ تولید از', '1405/06/01'));
        grid.appendChild(make('production_date_to', 'تاریخ تولید تا', '1405/06/31'));
        setTimeout(() => {
            if (typeof initAllJalaliPickers === 'function') initAllJalaliPickers();
        }, 0);
    }

    function ensureProductionContextPanel() {
        if (document.getElementById('productionContextPanel')) return;
        const wrap = document.querySelector('.wrap');
        if (!wrap) return;

        const panel = document.createElement('div');
        panel.className = 'toolbar';
        panel.id = 'productionContextPanel';
        panel.style.cssText = 'display:flex;align-items:flex-end;gap:10px;flex-wrap:wrap;background:#20262c;border:1px solid #3d4650;border-radius:10px;padding:16px;margin-bottom:16px;';
        panel.innerHTML = `
          <div class="field" style="min-width:220px;flex:1"><label>قرارداد فعال تولید</label><select id="production_contract_select"><option value="">انتخاب قرارداد</option></select></div>
          <div class="field" style="min-width:220px;flex:1"><label>محصول فعال تولید</label><select id="production_product_select"><option value="">انتخاب محصول</option></select></div>
          <button class="btn primary" type="button" id="production_context_write">ثبت در PLC</button>
          <div class="summary" id="production_context_status" style="width:100%;padding:0"></div>`;
        wrap.insertBefore(panel, wrap.firstElementChild);
    }

    async function loadProductionContextOptions() {
        const contractSelect = document.getElementById('production_contract_select');
        const productSelect = document.getElementById('production_product_select');
        if (!contractSelect || !productSelect) return;

        const response = await fetch('/management/production-context/options', {cache: 'no-store'});
        const data = await response.json();
        if (!response.ok || data.status !== 'ok') throw new Error(data.message || 'خطا در دریافت قراردادها و محصولات');

        const groups = {};
        (data.items || []).forEach(item => {
            const key = String(item.ContractCode || '').trim();
            if (!key) return;
            groups[key] = groups[key] || {name: item.ContractName || '', products: []};
            groups[key].products.push(item);
        });

        contractSelect.innerHTML = '<option value="">انتخاب قرارداد</option>' + Object.entries(groups).map(([code, group]) =>
            `<option value="${esc(code)}">${esc(code)} — ${esc(group.name)}</option>`
        ).join('');

        function refreshProducts() {
            const contract = contractSelect.value;
            const group = groups[contract];
            productSelect.innerHTML = '<option value="">انتخاب محصول</option>' + (group?.products || []).map(item =>
                `<option value="${esc(item.ProductCode)}">${esc(item.ProductCode)} — ${esc(item.ProductName)}</option>`
            ).join('');
        }

        contractSelect.onchange = refreshProducts;
        refreshProducts();
    }

    async function writeProductionContext() {
        const contract = value('production_contract_select');
        const product = value('production_product_select');
        const status = document.getElementById('production_context_status');
        if (!contract || !product) {
            if (status) status.textContent = 'ابتدا قرارداد و محصول را انتخاب کنید.';
            return;
        }

        const button = document.getElementById('production_context_write');
        if (button) button.disabled = true;
        if (status) status.textContent = 'در حال نوشتن در PLC...';
        try {
            const response = await fetch('/management/production-context/write', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ContractCode: contract, ProductCode: product})
            });
            const data = await response.json();
            if (!response.ok || data.status !== 'ok') throw new Error(data.message || 'خطای PLC');
            if (status) status.textContent = `ثبت شد: قرارداد ${data.ContractCode} / محصول ${data.ProductCode} — PLC ${data.PLC_ID}`;
        } catch (error) {
            console.error('[PRODUCTION CONTEXT]', error);
            if (status) status.textContent = error.message;
        } finally {
            if (button) button.disabled = false;
        }
    }

    async function loadProductionData() {
        const params = new URLSearchParams();
        FIELD_IDS.forEach(id => params.set(id, value(id)));
        params.set('production_date_from', value('production_date_from'));
        params.set('production_date_to', value('production_date_to'));

        try {
            const response = await fetch('/management/production-data?' + params.toString(), {cache: 'no-store'});
            const data = await response.json();
            if (!response.ok || data.status === 'error') throw new Error(data.message || 'خطا در دریافت داده‌های تولید');
            renderProductionData(data);
        } catch (error) {
            const summary = document.getElementById('summary');
            if (summary) summary.textContent = error.message;
            console.error('[MANAGEMENT PRODUCTION]', error);
        }
    }

    function renderProductionData(data) {
        const thead = document.getElementById('thead');
        const tbody = document.getElementById('tbody');
        const summary = document.getElementById('summary');
        if (!thead || !tbody) return;

        const columns = Array.isArray(data.columns) ? data.columns : [];
        const rows = Array.isArray(data.rows) ? data.rows : [];
        thead.innerHTML = '<tr>' + columns.map(col => `<th>${esc(col.label || col.key || '')}</th>`).join('') + '</tr>';
        tbody.innerHTML = '';

        rows.forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = columns.map(col => {
                const v = row[col.key];
                return `<td>${v === null || v === undefined ? '' : esc(typeof v === 'number' ? Number(v).toLocaleString() : v)}</td>`;
            }).join('');
            tbody.appendChild(tr);
        });

        if (!rows.length) {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td colspan="${Math.max(columns.length, 1)}" class="muted">تولیدی مطابق فیلترها پیدا نشد.</td>`;
            tbody.appendChild(tr);
        }
        if (summary) summary.textContent = `${rows.length} ردیف`;
    }

    async function init() {
        ensureProductionDateFields();
        ensureProductionContextPanel();
        window.loadData = loadProductionData;
        const writeButton = document.getElementById('production_context_write');
        if (writeButton) writeButton.onclick = writeProductionContext;
        try {
            await loadProductionContextOptions();
        } catch (error) {
            const status = document.getElementById('production_context_status');
            if (status) status.textContent = error.message;
        }
        loadProductionData();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, {once: true});
    } else {
        init();
    }
})();
