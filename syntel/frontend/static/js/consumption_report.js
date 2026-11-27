(function () {
    // ۱) راه‌اندازی انتخابگر تاریخ شمسی و کنترل کشوی بازه دلخواه
    var form = document.getElementById('consumptionFilterForm');
    if (window.initJalaliDateGroups) {
        window.initJalaliDateGroups(form || document);
    }

    var dateToggleBtn = document.getElementById('toggleDateDrawerBtn');
    var dateDrawer = document.getElementById('customDateDrawer');
    if (dateToggleBtn && dateDrawer) {
        dateToggleBtn.addEventListener('click', function () {
            var opened = dateDrawer.classList.toggle('is-open');
            dateToggleBtn.classList.toggle('is-open', opened);
            dateToggleBtn.setAttribute('aria-expanded', opened ? 'true' : 'false');
        });
    }

    // ارسال خودکار فرم با تغییر دسته‌بندی (حذف نیاز به کلیک اضافی روی دکمه نمایش)
    var categorySelect = document.getElementById('categorySelect');
    if (categorySelect && form) {
        categorySelect.addEventListener('change', function () {
            form.submit();
        });
    }

    // ۲) جستجوی سریع و مرتب‌سازی جدول کالاها در سمت کاربر
    var quickInput = document.getElementById('productQuickSearch');
    var sortSelect = document.getElementById('productSortSelect');
    var table = document.getElementById('consumptionProductsTable');
    var visibleCountEl = document.getElementById('visibleProductCount');

    function applyTableFilter() {
        if (!table) return;
        var q = quickInput ? (quickInput.value || '').trim().toLowerCase() : '';
        var rows = table.querySelectorAll('tbody tr.cr-product-row');
        var shown = 0;
        rows.forEach(function (tr) {
            var hay = (tr.getAttribute('data-search-text') || '').toLowerCase();
            var match = !q || hay.indexOf(q) !== -1;
            tr.style.display = match ? '' : 'none';
            if (match) shown++;
        });
        if (visibleCountEl) visibleCountEl.textContent = shown;
    }

    if (quickInput && table) {
        quickInput.addEventListener('input', applyTableFilter);
    }

    if (sortSelect && table) {
        sortSelect.addEventListener('change', function () {
            var tbody = table.querySelector('tbody');
            if (!tbody) return;
            var rows = Array.from(tbody.querySelectorAll('tr.cr-product-row'));
            var mode = sortSelect.value;
            rows.sort(function (a, b) {
                if (mode === 'cost_desc') {
                    return (parseFloat(b.dataset.cost || '0') || 0) - (parseFloat(a.dataset.cost || '0') || 0);
                }
                if (mode === 'deliveries_desc') {
                    return (parseInt(b.dataset.deliveries || '0', 10) || 0) - (parseInt(a.dataset.deliveries || '0', 10) || 0);
                }
                if (mode === 'name_asc') {
                    return (a.dataset.name || '').localeCompare(b.dataset.name || '', 'fa');
                }
                return (parseInt(a.dataset.index || '0', 10) || 0) - (parseInt(b.dataset.index || '0', 10) || 0);
            });
            rows.forEach(function (tr) { tbody.appendChild(tr); });
            applyTableFilter();
        });
    }

    // ۳) انتخاب/لغو فیلتر سکشن با کلیک روی کارت سکشن (رفع باگ sPicker)
    window.toggleSectionFilter = function (sectionName) {
        var select = document.getElementById('sectionSelect');
        if (!select || !form) return;
        if (select.value === sectionName) {
            select.value = '';
        } else {
            var exists = Array.from(select.options).some(function (o) { return o.value === sectionName; });
            if (!exists) {
                var opt = document.createElement('option');
                opt.value = sectionName;
                opt.textContent = sectionName;
                select.appendChild(opt);
            }
            select.value = sectionName;
        }
        form.submit();
    };

    window.clearSectionFilter = function () {
        var select = document.getElementById('sectionSelect');
        if (!select || !form) return;
        if (!select.value) return;
        select.value = '';
        form.submit();
    };

    // ۴) مودال سوابق تحویلی کالا در تاریخ‌های مختلف
    var historiesEl = document.getElementById('consumption-product-histories-data');
    var preloadedHistories = {};
    if (historiesEl) {
        try {
            preloadedHistories = JSON.parse(historiesEl.textContent || '{}');
        } catch (e) {
            preloadedHistories = {};
        }
    }

    var currentProductId = null;
    var currentScope = 'range';
    var _crCfg = (document.getElementById('consumptionReportConfig') || {}).dataset || {};
    var historyApiTemplate = _crCfg.historyApiTemplate || '';
    var startIso = _crCfg.startIso || '';
    var endIso = _crCfg.endIso || '';
    var activeSection = _crCfg.activeSection || '';

    function fmtNumber(val) {
        if (val === null || val === undefined || val === '') return '—';
        var num = Number(String(val).replace(/,/g, ''));
        if (isNaN(num)) return String(val);
        return num.toLocaleString('en-US');
    }

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str === null || str === undefined ? '' : String(str);
        return div.innerHTML;
    }

    function renderModalPayload(data) {
        document.getElementById('phModalTitle').textContent = 'سوابق تحویلی: ' + (data.product_name || '');
        document.getElementById('phModalSubtitle').textContent =
            'دسته‌بندی: ' + (data.category_name || '—') + (data.price_source ? ' · منبع قیمت: ' + data.price_source : '');
        document.getElementById('phTotalQty').textContent = (data.total_qty || '0') + ' ' + (data.unit_name || '');
        document.getElementById('phDeliveryCount').textContent = (data.delivery_count || 0) + ' مرتبه';

        if (data.has_price && data.unit_price !== null) {
            document.getElementById('phUnitPrice').textContent =
                fmtNumber(data.unit_price) + ' ریال / ' + (data.price_unit || data.unit_name || '');
        } else {
            document.getElementById('phUnitPrice').textContent = 'ثبت نشده';
        }

        if (data.has_price && data.estimated_cost !== null) {
            document.getElementById('phTotalCost').textContent = fmtNumber(data.estimated_cost) + ' ریال';
        } else {
            document.getElementById('phTotalCost').textContent = '—';
        }

        var secWrap = document.getElementById('phSectionsWrap');
        secWrap.innerHTML = '';
        if (data.sections && data.sections.length) {
            var lbl = document.createElement('span');
            lbl.style.cssText = 'font-size:0.74rem; font-weight:700; color:var(--text-muted);';
            lbl.textContent = 'تحویل به سکشن‌ها:';
            secWrap.appendChild(lbl);

            data.sections.forEach(function (s) {
                var chip = document.createElement('span');
                chip.className = 'cr-sec-chip';
                var qtyText = s.qty_fmt !== undefined ? s.qty_fmt : s.qty;
                chip.innerHTML =
                    '<span>' + escapeHtml(s.section) + '</span> ' +
                    '<strong>' + escapeHtml(qtyText) + '</strong> ' +
                    '<span style="color:var(--text-muted);">' + escapeHtml(data.unit_name || '') + ' (' + escapeHtml(s.cnt) + ')</span>';
                secWrap.appendChild(chip);
            });
        }

        var tbody = document.getElementById('phRecordsTbody');
        var records = data.records || [];
        if (!records.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:1.75rem; color:var(--text-muted);">هیچ سابقه تحویلی یافت نشد.</td></tr>';
            return;
        }

        var html = '';
        records.forEach(function (rec) {
            var costHtml = (rec.estimated_cost !== null && rec.estimated_cost !== undefined)
                ? '<span class="cr-est-cost" style="font-size:0.82rem;">' + fmtNumber(rec.estimated_cost) + '<span class="cr-est-cost-unit">ریال</span></span>'
                : '<span class="cr-no-price">—</span>';
            var warehouseName = escapeHtml(rec.warehouse_name || '—');
            var reqInfo = escapeHtml(rec.requester_name || '—') +
                (rec.ref_label ? ' <span style="color:var(--text-muted); font-size:0.72rem;">[' + escapeHtml(rec.ref_label) + ']</span>' : '');
            html += '<tr>' +
                '<td style="font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap;">' + escapeHtml(rec.jalali_date) + ' <span style="color:var(--text-muted); font-size:0.73rem; margin-right:0.25rem;">' + escapeHtml(rec.time_str) + '</span></td>' +
                '<td><span class="cr-sec-chip"><strong>' + escapeHtml(rec.section_name) + '</strong></span></td>' +
                '<td style="font-weight:700; font-variant-numeric:tabular-nums; white-space:nowrap;">' + escapeHtml(rec.quantity) + ' <span style="font-size:0.73rem; font-weight:500; color:var(--text-secondary);">' + escapeHtml(rec.unit_name) + '</span></td>' +
                '<td>' + warehouseName + '</td>' +
                '<td>' + reqInfo + '</td>' +
                '<td>' + costHtml + '</td>' +
                '</tr>';
        });
        tbody.innerHTML = html;
    }

    function fetchProductHistoryFromApi(productId, scope) {
        var url = historyApiTemplate.replace('999999', String(productId)) + '?scope=' + encodeURIComponent(scope);
        if (scope === 'range') {
            url += '&start_date=' + encodeURIComponent(startIso) + '&end_date=' + encodeURIComponent(endIso);
            if (activeSection) {
                url += '&section=' + encodeURIComponent(activeSection);
            }
        }
        var noteEl = document.getElementById('phStatusNote');
        if (noteEl) noteEl.textContent = 'در حال دریافت سوابق...';
        fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (noteEl) noteEl.textContent = scope === 'all' ? 'کل تاریخچه تحویل' : 'در بازه انتخابی';
                if (data && data.success) {
                    renderModalPayload(data);
                }
            })
            .catch(function () {
                if (noteEl) noteEl.textContent = '';
            });
    }

    window.openProductHistoryModal = function (productId) {
        currentProductId = productId;
        currentScope = 'range';
        document.getElementById('phScopeRangeBtn').classList.add('active');
        document.getElementById('phScopeAllBtn').classList.remove('active');
        document.getElementById('phStatusNote').textContent = 'در بازه انتخابی';

        var modal = document.getElementById('productHistoryModal');
        modal.classList.add('open');

        var preloaded = preloadedHistories[String(productId)];
        if (preloaded) {
            renderModalPayload(preloaded);
        } else {
            fetchProductHistoryFromApi(productId, 'range');
        }
    };

    window.switchProductHistoryScope = function (scope) {
        if (!currentProductId) return;
        currentScope = scope;
        document.getElementById('phScopeRangeBtn').classList.toggle('active', scope === 'range');
        document.getElementById('phScopeAllBtn').classList.toggle('active', scope === 'all');

        if (scope === 'range' && preloadedHistories[String(currentProductId)]) {
            document.getElementById('phStatusNote').textContent = 'در بازه انتخابی';
            renderModalPayload(preloadedHistories[String(currentProductId)]);
        } else {
            fetchProductHistoryFromApi(currentProductId, scope);
        }
    };

    window.closeProductHistoryModal = function () {
        var modal = document.getElementById('productHistoryModal');
        if (modal) modal.classList.remove('open');
    };

    var backdrop = document.getElementById('productHistoryModal');
    if (backdrop) {
        backdrop.addEventListener('click', function (e) {
            if (e.target === backdrop) closeProductHistoryModal();
        });
    }
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeProductHistoryModal();
    });
})();
