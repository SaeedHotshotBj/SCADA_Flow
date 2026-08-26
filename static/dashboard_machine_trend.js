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

    function esc(v) {
        const d = document.createElement("div");
        d.textContent = v == null ? "" : String(v);
        return d.innerHTML;
    }

    function makeModal() {
        if (document.getElementById("machineTrendModal")) return;

        const style = document.createElement("style");
        style.id = "machineTrendModalStyle";
        style.textContent = `
#machineTrendModal{position:fixed;inset:0;z-index:99999;background:rgba(2,6,23,.78);display:none;align-items:center;justify-content:center;padding:20px}
#machineTrendModal.open{display:flex}
.machineTrendWindow{width:min(1200px,96vw);height:min(820px,94vh);background:#f8fafc;color:#0f172a;border-radius:18px;box-shadow:0 25px 80px rgba(0,0,0,.45);display:flex;flex-direction:column;overflow:hidden}
.machineTrendHeader{display:flex;align-items:center;justify-content:space-between;background:#0f172a;color:#fff;padding:14px 18px}
.machineTrendTitle{font-size:20px;font-weight:800}
.machineTrendClose{border:0;background:#374151;color:#fff;width:38px;height:38px;border-radius:10px;font-size:22px;cursor:pointer}
.machineTrendControls{padding:14px 18px;background:#fff;border-bottom:1px solid #e2e8f0;display:flex;gap:10px;flex-wrap:wrap;align-items:end}
.machineTrendField{background:#f8fafc;border-radius:10px;padding:9px}
.machineTrendField label{display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:6px}
.machineTrendField input{width:220px;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px;direction:ltr;text-align:center}
.machineTrendBtn{border:0;background:#2563eb;color:#fff;padding:10px 16px;border-radius:9px;font-weight:700;cursor:pointer}
.machineTrendBtn.secondary{background:#475569}
.machineTrendStatus{width:100%;color:#64748b;font-size:13px;min-height:20px}
.machineTrendChart{position:relative;flex:1;min-height:0;padding:15px;background:#f8fafc}
.machineTrendChart canvas{width:100%!important;height:100%!important}
[id^="widget_"]{cursor:pointer}
[id^="widget_"]:hover{outline:1px solid #60a5fa}
`;
        document.head.appendChild(style);

        const modal = document.createElement("div");
        modal.id = "machineTrendModal";
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

    function extractTag(element) {
        if (!element) return "";
        const attr = element.getAttribute("data-tag");
        if (attr) return attr.trim();
        const id = element.id || "";
        if (id.indexOf("widget_") === 0) return id.slice(7).trim();
        return "";
    }

    function extractTitle(element, tag) {
        const attr = element && element.getAttribute("data-label");
        if (attr) return attr.trim();
        const title = element && element.querySelector(".widget-title, .machine-parameter-label");
        return title ? title.textContent.trim() : tag;
    }

    function extractUnit(element) {
        const attr = element && element.getAttribute("data-unit");
        if (attr) return attr.trim();
        const unit = element && element.querySelector(".widget-unit, .machine-parameter-unit");
        return unit ? unit.textContent.trim() : "";
    }

    function bindClicks() {
        document.querySelectorAll('[id^="widget_"], .machine-parameter[data-tag]').forEach(function (el) {
            if (el.dataset.machineTrendBound === "1") return;
            const tag = extractTag(el);
            if (!tag) return;
            el.dataset.machineTrendBound = "1";
            const open = function () {
                openModal(tag, extractTitle(el, tag), extractUnit(el));
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

        const now = new Date();
        const weekAgo = new Date(now.getTime() - 7 * 86400000);
        document.getElementById("machineTrendStart").value = toJalaliText(weekAgo, false);
        document.getElementById("machineTrendEnd").value = toJalaliText(now, true);
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

    function pad(n) { return String(n).padStart(2, "0"); }

    function gregorianToJalali(gy, gm, gd) {
        let jy, jm, jd;
        const gdm = [0,31,59,90,120,151,181,212,243,273,304,334];
        let gy2 = gy - 1600;
        let gm2 = gm - 1;
        let gd2 = gd - 1;
        let g_day_no = 365 * gy2 + Math.floor((gy2 + 3) / 4) - Math.floor((gy2 + 99) / 100) + Math.floor((gy2 + 399) / 400);
        g_day_no += gdm[gm2] + gd2;
        if (gm2 > 1 && ((gy % 4 === 0 && gy % 100 !== 0) || gy % 400 === 0)) g_day_no++;
        let j_day_no = g_day_no - 79;
        const j_np = Math.floor(j_day_no / 12053);
        j_day_no %= 12053;
        jy = 979 + 33 * j_np + 4 * Math.floor(j_day_no / 1461);
        j_day_no %= 1461;
        if (j_day_no >= 366) {
            jy += Math.floor((j_day_no - 1) / 365);
            j_day_no = (j_day_no - 1) % 365;
        }
        jm = j_day_no < 186 ? 1 + Math.floor(j_day_no / 31) : 7 + Math.floor((j_day_no - 186) / 30);
        jd = 1 + (j_day_no < 186 ? j_day_no % 31 : (j_day_no - 186) % 30);
        return [jy, jm, jd];
    }

    function toJalaliText(date, endOfMinute) {
        const j = gregorianToJalali(date.getFullYear(), date.getMonth() + 1, date.getDate());
        const hh = endOfMinute ? 23 : date.getHours();
        const mm = endOfMinute ? 59 : date.getMinutes();
        return j[0] + "/" + pad(j[1]) + "/" + pad(j[2]) + " " + pad(hh) + ":" + pad(mm);
    }

    async function loadTrend() {
        if (!selected.tag) return;
        const status = document.getElementById("machineTrendStatus");
        status.textContent = "در حال خواندن...";

        try {
            const body = {
                TrendRequest: {
                    Tag: selected.tag,
                    Tags: [selected.tag],
                    Start: document.getElementById("machineTrendStart").value.trim() || null,
                    End: document.getElementById("machineTrendEnd").value.trim() || null,
                    Calendar: "Jalali",
                    DatePicker: "JalaliPicker"
                }
            };

            const response = await fetch("/flow_trend?_=" + Date.now(), {
                method: "POST",
                credentials: "same-origin",
                cache: "no-store",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body)
            });

            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || data.message || ("HTTP " + response.status));
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
        const ds = (data.datasets || []).find(function (x) { return String(x.tag || "").toLowerCase() === selected.tag.toLowerCase(); }) || (data.datasets || [])[0];
        if (!ds) {
            status.textContent = "برای این پارامتر در بازه انتخاب‌شده داده‌ای وجود ندارد.";
            if (chart) {
                chart.destroy();
                chart = null;
            }
            return;
        }

        const points = (ds.data || []).map(function (p) {
            return { x: Number(p.x), y: Number(p.y), label: p.label || "" };
        }).filter(function (p) { return Number.isFinite(p.x) && Number.isFinite(p.y); });

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
                interaction: { mode: "nearest", intersect: false },
                scales: {
                    x: { type: "linear", title: { display: true, text: "Time" } },
                    y: { title: { display: true, text: selected.unit || "Value" } }
                },
                plugins: {
                    legend: { display: true },
                    tooltip: {
                        callbacks: {
                            title: function (items) {
                                return items.length && items[0].raw && items[0].raw.label ? items[0].raw.label : "";
                            }
                        }
                    }
                }
            }
        });

        status.textContent = "تعداد نقاط: " + points.length;
    }

    async function boot() {
        try {
            await loadScript("https://cdn.jsdelivr.net/npm/chart.js");
        } catch (e) {
            console.error("Chart.js load failed", e);
        }

        makeModal();
        bindClicks();

        const observer = new MutationObserver(function () {
            bindClicks();
        });
        const target = document.getElementById("dashboard");
        if (target) observer.observe(target, { childList: true, subtree: true });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
