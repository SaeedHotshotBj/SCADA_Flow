(function () {
    "use strict";

    let chart = null;
    let selected = { tag: "", title: "", unit: "" };

    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            if (document.querySelector('script[src="' + src + '"]')) {
                resolve();
                return;
            }
            const s = document.createElement("script");
            s.src = src;
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    function makeModal() {
        if (document.getElementById("machineTrendModal")) return;

        const style = document.createElement("style");
        style.id = "machineTrendModalStyle";
        style.textContent = `
#machineTrendModal{position:fixed;inset:0;z-index:99999;background:rgba(2,6,23,.78);display:none;align-items:center;justify-content:center;padding:20px;overflow:auto}
#machineTrendModal.open{display:flex}
.machineTrendWindow{width:min(1200px,96vw);height:min(820px,94vh);background:#f8fafc;color:#0f172a;border-radius:18px;box-shadow:0 25px 80px rgba(0,0,0,.45);display:flex;flex-direction:column;overflow:hidden}
.machineTrendHeader{display:flex;align-items:center;justify-content:space-between;background:#0f172a;color:#fff;padding:14px 18px}
.machineTrendTitle{font-size:20px;font-weight:800}
.machineTrendClose{border:0;background:#374151;color:#fff;width:38px;height:38px;border-radius:10px;font-size:22px;cursor:pointer}
.machineTrendControls{padding:14px 18px;background:#fff;border-bottom:1px solid #e2e8f0;display:flex;gap:10px;flex-wrap:wrap;align-items:end}
.machineTrendField{background:#f8fafc;border-radius:10px;padding:9px}
.machineTrendField label{display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:6px;text-align:right}
.machineTrendField input{width:225px;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px;direction:ltr;text-align:center;background:#fff}
.machineTrendBtn{border:0;background:#2563eb;color:#fff;padding:10px 16px;border-radius:9px;font-weight:700;cursor:pointer}
.machineTrendBtn.secondary{background:#475569}
.machineTrendStatus{width:100%;color:#64748b;font-size:13px;min-height:20px}
.machineTrendChart{position:relative;flex:1;min-height:0;padding:15px;background:#f8fafc}
.machineTrendChart canvas{width:100%!important;height:100%!important}
.machine-parameter,[id^="widget_"]{cursor:pointer}
.machine-parameter:hover,[id^="widget_"]:hover{outline:1px solid #60a5fa}
`;
        document.head.appendChild(style);

        const modal = document.createElement("div");
        modal.id = "machineTrendModal";
        modal.setAttribute("aria-hidden", "true");
        modal.innerHTML = `
<div class="machineTrendWindow">
  <div class="machineTrendHeader">
    <div class="machineTrendTitle" id="machineTrendTitle">Trend</div>
    <button class="machineTrendClose" type="button" id="machineTrendClose">×</button>
  </div>
  <div class="machineTrendControls">
    <div class="machineTrendField">
      <label>از تاریخ و ساعت</label>
      <input id="machineTrendStart" type="text" placeholder="1405/01/01 00:00">
    </div>
    <div class="machineTrendField">
      <label>تا تاریخ و ساعت</label>
      <input id="machineTrendEnd" type="text" placeholder="1405/01/31 23:59">
    </div>
    <button class="machineTrendBtn" type="button" id="machineTrendLoad">📈 نمایش نمودار</button>
    <button class="machineTrendBtn secondary" type="button" id="machineTrendReset">🔄 Reset Zoom</button>
    <div class="machineTrendStatus" id="machineTrendStatus"></div>
  </div>
  <div class="machineTrendChart"><canvas id="machineTrendCanvas"></canvas></div>
</div>`;
        document.body.appendChild(modal);

        document.getElementById("machineTrendClose").addEventListener("click", closeModal);
        document.getElementById("machineTrendLoad").addEventListener("click", loadTrend);
        document.getElementById("machineTrendReset").addEventListener("click", function () {
            if (chart && chart.resetZoom) chart.resetZoom();
        });

        modal.addEventListener("click", function (e) {
            if (e.target === modal) closeModal();
        });

        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") closeModal();
        });
    }

    function setupJalaliPicker() {
        if (!window.jQuery || !window.jQuery.fn || !window.jQuery.fn.persianDatepicker) {
            return;
        }

        const options = {
            format: "YYYY/MM/DD HH:mm",
            initialValue: false,
            autoClose: true,
            observer: true,
            timePicker: {
                enabled: true,
                second: false,
                meridian: { enabled: false }
            }
        };

        window.jQuery("#machineTrendStart").persianDatepicker(options);
        window.jQuery("#machineTrendEnd").persianDatepicker(options);
    }

    function bindClicks() {
        document.querySelectorAll('[id^="widget_"], .machine-parameter[data-tag]').forEach(function (el) {
            if (el.dataset.machineTrendBound === "1") return;

            const tag = (el.getAttribute("data-tag") || (el.id.indexOf("widget_") === 0 ? el.id.slice(7) : "")).trim();
            if (!tag) return;

            el.dataset.machineTrendBound = "1";

            const open = function () {
                const titleElement = el.querySelector(".widget-title, .machine-parameter-label");
                const unitElement = el.querySelector(".widget-unit, .machine-parameter-unit");
                openModal(
                    tag,
                    (el.getAttribute("data-label") || (titleElement ? titleElement.textContent.trim() : tag)),
                    (el.getAttribute("data-unit") || (unitElement ? unitElement.textContent.trim() : ""))
                );
            };

            el.addEventListener("click", open);
            el.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    open();
                }
            });
        });
    }

    function openModal(tag, title, unit) {
        makeModal();
        selected = { tag: tag, title: title || tag, unit: unit || "" };

        document.getElementById("machineTrendTitle").textContent = selected.title + (selected.unit ? " (" + selected.unit + ")" : "");

        const modal = document.getElementById("machineTrendModal");
        modal.classList.add("open");
        modal.setAttribute("aria-hidden", "false");

        setupJalaliPicker();
        document.getElementById("machineTrendStart").value = "";
        document.getElementById("machineTrendEnd").value = "";
        document.getElementById("machineTrendStatus").textContent = "در حال خواندن داده...";

        loadTrend();
    }

    function closeModal() {
        const modal = document.getElementById("machineTrendModal");
        if (!modal) return;

        modal.classList.remove("open");
        modal.setAttribute("aria-hidden", "true");

        if (chart) {
            chart.destroy();
            chart = null;
        }
    }

    async function loadTrend() {
        if (!selected.tag) return;

        const status = document.getElementById("machineTrendStatus");
        status.textContent = "در حال خواندن داده از همان موتور Trend...";

        try {
            const start = document.getElementById("machineTrendStart").value.trim();
            const end = document.getElementById("machineTrendEnd").value.trim();

            const response = await fetch("/flow_trend?_=" + Date.now(), {
                method: "POST",
                credentials: "same-origin",
                cache: "no-store",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    TrendRequest: {
                        Tag: selected.tag,
                        Tags: [selected.tag],
                        Start: start || null,
                        End: end || null,
                        Calendar: "Jalali",
                        DatePicker: "JalaliPicker"
                    }
                })
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.message || data.error || ("HTTP " + response.status));
            }

            draw(data);
        } catch (err) {
            console.error("MACHINE TREND ERROR:", err);
            status.textContent = "خطا: " + err.message;

            if (chart) {
                chart.destroy();
                chart = null;
            }
        }
    }

    function draw(data) {
        const canvas = document.getElementById("machineTrendCanvas");
        const status = document.getElementById("machineTrendStatus");
        const ds = (data.datasets || []).find(function (item) {
            const tag = item && item.tag != null ? String(item.tag) : "";
            return tag.toLowerCase() === selected.tag.toLowerCase();
        }) || (data.datasets || [])[0];

        if (!ds || !(ds.data || []).length) {
            status.textContent = "برای این پارامتر در بازه انتخاب‌شده داده‌ای وجود ندارد.";

            if (chart) {
                chart.destroy();
                chart = null;
            }
            return;
        }

        const points = ds.data.map(function (p) {
            return {
                x: Number(p.x),
                y: Number(p.y),
                label: p.label || ""
            };
        }).filter(function (p) {
            return Number.isFinite(p.x) && Number.isFinite(p.y);
        });

        if (chart) chart.destroy();

        chart = new Chart(canvas, {
            type: "line",
            data: {
                datasets: [{
                    label: ds.title || ds.tag || selected.tag,
                    data: points,
                    borderWidth: 2,
                    pointRadius: 2,
                    pointHoverRadius: 5,
                    tension: 0,
                    stepped: "after",
                    parsing: false,
                    fill: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: {
                    mode: "nearest",
                    intersect: false
                },
                scales: {
                    x: {
                        type: "linear",
                        title: {
                            display: true,
                            text: "زمان"
                        },
                        ticks: {
                            maxTicksLimit: 12,
                            callback: function (value) {
                                const point = points.find(function (p) {
                                    return p.x === value;
                                });
                                return point ? point.label : "";
                            }
                        }
                    },
                    y: {
                        title: {
                            display: true,
                            text: selected.unit || "مقدار"
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: true
                    },
                    tooltip: {
                        callbacks: {
                            title: function (items) {
                                return items.length && items[0].raw ? items[0].raw.label || "" : "";
                            }
                        }
                    }
                }
            }
        });

        status.textContent = "تعداد نقاط: " + points.length + (data.resolutions && selected.tag && data.resolutions[selected.tag] ? " | Resolution: " + data.resolutions[selected.tag] : "");
    }

    async function boot() {
        try {
            await loadScript("https://cdn.jsdelivr.net/npm/chart.js");
            await loadScript("https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js");
            await loadScript("https://cdn.jsdelivr.net/npm/persian-date@1.1.0/dist/persian-date.min.js");
            await loadScript("https://cdn.jsdelivr.net/npm/persian-datepicker@1.2.0/dist/js/persian-datepicker.min.js");

            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = "https://cdn.jsdelivr.net/npm/persian-datepicker@1.2.0/dist/css/persian-datepicker.min.css";
            document.head.appendChild(link);
        } catch (e) {
            console.error("Trend dependency load failed", e);
        }

        makeModal();
        bindClicks();

        const observer = new MutationObserver(function () {
            bindClicks();
        });

        const target = document.getElementById("dashboard");
        if (target) {
            observer.observe(target, {
                childList: true,
                subtree: true
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
