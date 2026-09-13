(function () {
    function ensureFilters() {
        const filter = document.querySelector('.filter');
        if (!filter || document.getElementById('contract_code_filter')) return;

        const make = (id, label) => {
            const box = document.createElement('div');
            box.className = 'item';
            box.innerHTML = `<label>${label}</label><input id="${id}" type="text">`;
            return box;
        };

        const button = filter.lastElementChild;
        filter.insertBefore(make('contract_code_filter', 'کد قرارداد'), button);
        filter.insertBefore(make('product_code_filter', 'کد محصول'), button);
    }

    async function loadFilteredReport() {
        if (typeof ready !== 'undefined' && !ready) return;
        const start = document.getElementById('start')?.value || '';
        const end = document.getElementById('end')?.value || '';
        const contractCode = document.getElementById('contract_code_filter')?.value.trim() || '';
        const productCode = document.getElementById('product_code_filter')?.value.trim() || '';
        if (!start || !end) return;

        const status = document.getElementById('status');
        if (status) status.textContent = 'در حال دریافت گزارش...';

        try {
            const response = await fetch('/flow_report', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ReportRequest: {
                        Start: start,
                        End: end,
                        Calendar: (typeof calendarMode !== 'undefined' ? calendarMode : 'Jalali'),
                        ContractCode: contractCode,
                        ProductCode: productCode
                    }
                }),
                cache: 'no-store'
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.message || 'Report error');
            if (typeof renderReport === 'function') renderReport(data);
            if (status) status.textContent = 'گزارش دریافت شد';
        } catch (error) {
            console.error(error);
            if (status) status.textContent = 'خطا در دریافت گزارش: ' + error.message;
        }
    }

    function init() {
        ensureFilters();
        window.loadReport = loadFilteredReport;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, {once: true});
    } else {
        init();
    }
})();
