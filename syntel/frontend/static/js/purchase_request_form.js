    // ردیف‌هایی که سرور در آخرین ارسال دیده — اگر ارسال رد شد (مثلاً کالای
    // بدون رسپی)، همان ردیف‌ها برمی‌گردند تا کاربر از صفر شروع نکند.
    let items = [];
    try {
        const seedEl = document.getElementById('submittedItemsData');
        const seed = seedEl ? JSON.parse(seedEl.textContent || '[]') : [];
        items = (Array.isArray(seed) ? seed : []).map((row, i) => ({ ...row, id: i + 1 }));
    } catch (err) {
        items = [];
    }
    let nextItemId = items.length + 1;
    let editingId = null;
    let pendingProductItemId = null;
    let isCheckingStock = false; // جلوگیری از ثبت دوباره هنگام کلیک/اینتر مکرر در طول بررسی موجودی
    let selectedProductName = ''; // نام کالایی که کاربر از فهرست انتخاب یا جدید ثبت کرده است

    const _prCfg = (document.getElementById('purchaseRequestConfig') || {}).dataset || {};
    const AUTOCOMPLETE_URL = _prCfg.autocompleteUrl || '';
    const ALL_CATEGORY_PRODUCTS_URL = _prCfg.allCategoryProductsUrl || '';
    const QUICK_ADD_URL    = _prCfg.quickAddUrl || '';
    const AVAILABILITY_URL = _prCfg.availabilityUrl || '';
    const CSRF_TOKEN       = _prCfg.csrfToken || (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
    // کاربران درخواست‌دهنده نباید هنگام ثبت درخواست، خطا یا پیامی درباره موجودی کالا ببینند
    const IS_REQUESTER = _prCfg.isRequester === 'true';

    // ── زمان تحویل: باکس کشویی — ۷ روز آینده فقط یکشنبه تا پنجشنبه (بدون جمعه و شنبه) ──
    let selectedDeliveryDate = '';

    function toggleDeliveryBox() {
        document.getElementById('deliverySection').classList.toggle('open');
    }

    function selectDeliveryChip(el) {
        document.querySelectorAll('.delivery-chip').forEach(c => c.classList.remove('active'));
        el.classList.add('active');
        selectedDeliveryDate = el.getAttribute('data-date') || '';
        document.getElementById('deliveryDate').value = selectedDeliveryDate;
        // نمایش خلاصه انتخاب‌شده در هدر باکس و بستن باکس بعد از انتخاب
        const summary = document.getElementById('deliverySummary');
        if (summary) {
            summary.textContent = el.textContent.trim();
        }
        document.getElementById('deliverySection').classList.remove('open');
    }

    function buildUnitOptions(selected) {
        const units = ['عدد','کیلوگرم','گرم','لیتر','بسته','کارتن','جعبه','شیشه','قوطی','بطری','پاکت','متر','دست','جفت'];
        if (selected && !units.includes(selected)) {
            units.unshift(selected);
        }
        return units.map(u => `<option value="${escapeHtml(u)}" ${u === selected ? 'selected' : ''}>${escapeHtml(u)}</option>`).join('');
    }

    // Init entry unit select
    document.getElementById('entryUnit').innerHTML = buildUnitOptions('عدد');

    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function escapeJs(str) {
        return String(str).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
    }

    function fmtQty(val) {
        if (window.SyntelComma && window.SyntelComma.formatDisplayNumber) {
            return window.SyntelComma.formatDisplayNumber(val);
        }
        var s = String(val == null ? '' : val).replace(/,/g, '').trim();
        if (!s || isNaN(Number(s))) return String(val == null ? '' : val);
        var parts = s.split('.');
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        return parts[1] ? parts[0] + '.' + parts[1].replace(/0+$/, '') : parts[0];
    }

    // ── CRUD ────────────────────────────────────────────────────────────

    // ذخیره یک یا چند رکورد در لیست (افزودن جدید یا جایگزینی قلم در حال ویرایش)
    let pendingAdd = null;       // رکورد در انتظار تصمیم کاربر در مودال موجودی
    let pendingAddEditId = null; // اگر در حال ویرایش بودیم، شناسه قلم

    function commitItems(records) {
        const list = Array.isArray(records) ? records : [records];
        // در حالت ویرایش، قلمِ قبلی حذف و نسخه جدید در همان جای آن قرار می‌گیرد؛
        // شناسه قلمِ در حال ویرایش: pendingAddEditId (مسیر مودال موجودی) یا editingId
        const editId = pendingAddEditId !== null ? pendingAddEditId : editingId;
        let insertAt = -1;
        if (editId !== null) {
            const idx = items.findIndex(it => it.id === editId);
            if (idx !== -1) {
                items.splice(idx, 1);
                insertAt = idx;
            }
            pendingAddEditId = null;
            editingId = null;
            document.getElementById('addToListBtn').textContent = '+ افزودن';
            document.getElementById('cancelEditBtn').style.display = 'none';
        }
        const newRecs = list.map(rec => ({ id: nextItemId++, ...rec }));
        if (insertAt !== -1) items.splice(insertAt, 0, ...newRecs);
        else items.push(...newRecs);
        clearEntryForm();
        renderItemsTable();
    }

    function commitItem(rec) { commitItems([rec]); }

    async function addItemToList() {
        const nameInput = document.getElementById('entryProductName');
        const qtyInput = document.getElementById('entryQuantity');
        const name = nameInput.value.trim();
        const quantity = qtyInput.value;
        const unit = document.getElementById('entryUnit').value;
        const description = document.getElementById('entryDescription').value.trim();
        const productId = document.getElementById('entryProductId').value;

        // Validation
        if (!name) {
            nameInput.style.borderColor = '#ef4444';
            nameInput.focus();
            alert('⚠️ لطفاً نام کالا را وارد کنید');
            return;
        }
        if (IS_REQUESTER && (!productId || name !== selectedProductName)) {
            nameInput.style.borderColor = '#ef4444';
            nameInput.focus();
            alert('⚠️ لطفاً کالا را از لیست انتخاب کنید یا به عنوان کالای جدید ثبت نمایید.');
            return;
        }
        if (!quantity || parseFloat(quantity) <= 0) {
            qtyInput.style.borderColor = '#ef4444';
            qtyInput.focus();
            alert('⚠️ لطفاً مقدار معتبر وارد کنید');
            return;
        }

        // Reset validation styles
        nameInput.style.borderColor = '';
        qtyInput.style.borderColor = '';

        // بررسی موجودی انبار برای محصول انتخاب‌شده
        // (فقط وقتی محصول از لیست پیشنهادها انتخاب شده باشد — برای درخواست‌دهندگان انجام نمی‌شود)
        let stockInfo = null;
        if (productId && !IS_REQUESTER) {
            if (isCheckingStock) return; // کلیک/اینتر مکرر حین بررسی موجودی نادیده گرفته می‌شود
            isCheckingStock = true;
            try {
                const checkUrl = AVAILABILITY_URL + '?product_id=' + encodeURIComponent(productId) +
                                 '&qty=' + encodeURIComponent(quantity) +
                                 '&unit=' + encodeURIComponent(unit);
                try {
                    const resp = await fetch(checkUrl);
                    if (resp.ok) stockInfo = await resp.json();
                } catch (e) { /* خطای شبکه: بررسی موجودی نادیده گرفته می‌شود */ }
                showEntryStockHint(stockInfo);
            } finally {
                isCheckingStock = false;
            }
        }

        const record = { name, quantity, unit, description, productId, stockInfo };

        // اگر موجودی انبار به اندازه درخواست نبود، مسدود می‌شویم و انتخاب می‌دهیم:
        // سفارش به اندازه موجودی + بقیه برای خرید / افزودن به لیست خرید
        if (stockInfo && !stockInfo.available) {
            pendingAdd = record;
            pendingAddEditId = editingId;
            openAvailabilityModal(stockInfo);
            return;
        }

        commitItem(record);
    }

    // ── مودال «موجودی انبار کافی نیست» ─────────────────────────────────────

    function closeAvailabilityModal() {
        document.getElementById('availabilityModal').classList.remove('open');
        pendingAdd = null;
        pendingAddEditId = null;
    }

    function openAvailabilityModal(stockInfo) {
        const body = document.getElementById('availModalBody');
        const actions = document.getElementById('availModalActions');

        let html = '<div class="avail-modal-msg">کالای «' + escapeHtml(pendingAdd.name) + '»' +
            ' به مقدار <strong>' + escapeHtml(fmtQty(pendingAdd.quantity)) + '</strong> ' + escapeHtml(pendingAdd.unit) +
            ' درخواست شده است اما موجودی انبار فقط <strong>' + escapeHtml(fmtQty(stockInfo.stock_trade)) + ' ' + escapeHtml(stockInfo.unit_label) + '</strong> است.</div>';

        actions.innerHTML = '';
        const stockNum = parseFloat(stockInfo.stock_trade);
        if (stockNum > 0) {
            const splitBtn = document.createElement('button');
            splitBtn.type = 'button';
            splitBtn.className = 'modal-btn-save avail-alt-btn';
            splitBtn.textContent = '✂️ به اندازه موجودی (' + fmtQty(stockInfo.stock_trade) + ' ' + stockInfo.unit_label +
                ') سفارش بده و بقیه به لیست خرید اضافه شود';
            splitBtn.onclick = () => confirmSplitAdd(stockInfo);
            actions.appendChild(splitBtn);
        }

        // افزودن همان کالا به لیست خرید (تامین از خرید، نه از انبار)
        const buyBtn = document.createElement('button');
        buyBtn.type = 'button';
        buyBtn.className = 'modal-btn-save avail-alt-btn';
        buyBtn.style.background = '#3b82f6';
        buyBtn.textContent = '🛒 افزودن به لیست خرید';
        buyBtn.onclick = () => confirmAsIsAdd();
        actions.appendChild(buyBtn);

        body.innerHTML = html;
        document.getElementById('availabilityModal').classList.add('open');
    }

    function confirmAsIsAdd() {
        closeAvailabilityModal();
        const rec = { ...pendingAdd };
        commitItem(rec);
    }

    function _unitFactor(unitName) {
        const u = String(unitName || '').trim().toLowerCase().replace(/[\s\u200c_-]+/g, '');
        if (u === 'کیلوگرم' || u === 'کیلو' || u === 'kg' || u === 'kilo') return { fam: 'mass', factor: 1000 };
        if (u === 'گرم' || u === 'g' || u === 'gr' || u === 'gram') return { fam: 'mass', factor: 1 };
        if (u === 'لیتر' || u === 'l' || u === 'lit' || u === 'liter') return { fam: 'vol', factor: 1000 };
        if (u === 'میلیلیتر' || u === 'میل' || u === 'ml' || u === 'cc') return { fam: 'vol', factor: 1 };
        return { fam: u, factor: 1 };
    }

    function confirmSplitAdd(stockInfo) {
        closeAvailabilityModal();
        const rawStockVal = parseFloat(stockInfo.stock_trade);
        const reqVal = parseFloat(pendingAdd.quantity);
        if (!(rawStockVal > 0)) { confirmAsIsAdd(); return; }
        const sInfo = _unitFactor(stockInfo.unit_label);
        const rInfo = _unitFactor(pendingAdd.unit);
        let stockInReqUnit = rawStockVal;
        if (sInfo.fam && sInfo.fam === rInfo.fam && rInfo.factor > 0) {
            stockInReqUnit = rawStockVal * (sInfo.factor / rInfo.factor);
        }
        const stockRounded = Math.round(stockInReqUnit * 10000) / 10000;
        if (!(stockRounded > 0) || !(reqVal > stockRounded)) { confirmAsIsAdd(); return; }
        const rem = Math.round((reqVal - stockRounded) * 10000) / 10000;
        const desc = pendingAdd.description || '';
        const partA = {
            ...pendingAdd,
            quantity: String(stockRounded),
            description: (desc ? desc + ' — ' : '') + '✅ تامین از انبار (بخشی از درخواست)',
        };
        const partB = {
            ...pendingAdd,
            quantity: String(rem),
            stockInfo: null,
            description: (desc ? desc + ' — ' : '') + '🛒 باقیمانده برای لیست خرید',
        };
        commitItems([partA, partB]);
    }

    // ── بررسی موجودی انبار ───────────────────────────────

    function showEntryStockHint(stockInfo) {
        const hint = document.getElementById('entryStockHint');
        if (!hint) return;
        hint.className = 'entry-stock-hint';
        hint.style.display = 'none';
        if (!stockInfo) return;
        if (stockInfo.available) {
            hint.className = 'entry-stock-hint ok';
            hint.textContent = '✅ موجودی انبار: ' + fmtQty(stockInfo.stock_trade) + ' ' + stockInfo.unit_label +
                (stockInfo.warehouses && stockInfo.warehouses.length ? ' (' + stockInfo.warehouses.join('، ') + ')' : '');
        } else {
            hint.className = 'entry-stock-hint err';
            hint.textContent = '⚠️ این کالا در انبار موجود نیست.';
        }
        hint.style.display = 'block';
    }

    function editItem(id) {
        const item = items.find(it => it.id === id);
        if (!item) return;

        // Fill entry form with item data
        document.getElementById('entryProductName').value = item.name;
        document.getElementById('entryProductId').value = item.productId || '';
        selectedProductName = item.productId ? item.name : '';
        document.getElementById('entryQuantity').value = item.quantity;
        document.getElementById('entryUnit').innerHTML = buildUnitOptions(item.unit);
        document.getElementById('entryDescription').value = item.description || '';

        // Set editing mode
        editingId = id;
        document.getElementById('addToListBtn').textContent = '💾 ذخیره تغییرات';
        document.getElementById('cancelEditBtn').style.display = 'inline-block';

        // Scroll to form
        document.querySelector('.entry-form-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
        document.getElementById('entryProductName').focus();
    }

    function deleteItem(id) {
        if (items.length <= 1) {
            if (!confirm('آیا مایل به حذف آخرین کالا هستید؟ پس از حذف، لیست خالی می‌شود.')) return;
        }
        items = items.filter(it => it.id !== id);
        renderItemsTable();

        // If we were editing this item, cancel edit
        if (editingId === id) {
            cancelEdit();
        }
    }

    function cancelEdit() {
        editingId = null;
        document.getElementById('addToListBtn').textContent = '+ افزودن';
        document.getElementById('cancelEditBtn').style.display = 'none';
        clearEntryForm();
    }

    function clearEntryForm() {
        document.getElementById('entryProductName').value = '';
        document.getElementById('entryProductId').value = '';
        selectedProductName = '';
        document.getElementById('entryCategoryName').value = '';
        document.getElementById('entryCategoryId').value = '';
        const catSel = document.getElementById('entryCategorySelect');
        if (catSel) catSel.value = '';
        catBrowseSearch = '';
        document.getElementById('entryQuantity').value = '';
        document.getElementById('entryUnit').innerHTML = buildUnitOptions('عدد');
        document.getElementById('entryDescription').value = '';
        const hint = document.getElementById('entryStockHint');
        if (hint) { hint.style.display = 'none'; hint.className = 'entry-stock-hint'; }
        document.getElementById('entryProductName').style.borderColor = '';
        document.getElementById('entryQuantity').style.borderColor = '';
        document.getElementById('entryProductName').focus();
    }

    function renderItemsTable() {
        const tbody = document.getElementById('itemsTableBody');
        const emptyState = document.getElementById('emptyState');

        tbody.innerHTML = '';

        if (items.length === 0) {
            emptyState.style.display = 'block';
            return;
        }

        emptyState.style.display = 'none';

        items.forEach((item, idx) => {
            const tr = document.createElement('tr');
            const displayName = item.name;
            let stockHtml = '';
            if (item.stockInfo) {
                if (item.stockInfo.available) {
                    stockHtml = `<div class="row-stock-line ok">✅ موجودی انبار: ${escapeHtml(fmtQty(item.stockInfo.stock_trade))} ${escapeHtml(item.stockInfo.unit_label)}</div>`;
                } else {
                    stockHtml = '<div class="row-stock-line warn">⚠️ این کالا در انبار موجود نیست.</div>';
                }
            }
            tr.innerHTML = `
                <td class="col-num">${idx + 1}</td>
                <td>${escapeHtml(displayName)}${stockHtml}</td>
                <td class="col-qty"><span dir="ltr">${escapeHtml(fmtQty(item.quantity))}</span></td>
                <td class="col-unit">${escapeHtml(item.unit)}</td>
                <td class="col-desc" title="${escapeHtml(item.description || '')}">${item.description ? escapeHtml(item.description) : '—'}</td>
                <td class="col-actions">
                    <button type="button" class="action-icon-btn edit-btn" onclick="editItem(${item.id})" title="ویرایش">✏️</button>
                    <button type="button" class="action-icon-btn delete-btn" onclick="deleteItem(${item.id})" title="حذف">🗑️</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    }

    // ── Autocomplete (single entry form) ────────────────────────────────

    let acTimer = null;
    let acResults = [];
    let acHighlightIdx = -1;

    function onAcInput(input) {
        const q = input.value.trim();
        document.getElementById('entryProductId').value = '';
        selectedProductName = '';
        showEntryStockHint(null);
        clearTimeout(acTimer);
        // اگر دسته (یا «همه کالاها») انتخاب شده باشد، تایپ در فیلد اصلی نام کالا = فیلتر درون همان لیست
        if (document.getElementById('entryCategoryId').value) {
            catBrowseSearch = q;
            acTimer = setTimeout(() => fetchCategoryProducts(), q ? 200 : 0);
            return;
        }
        if (q.length < 1) { closeDropdown(); return; }
        acTimer = setTimeout(() => fetchSuggestions(q), 250);
    }

    function fetchSuggestions(q) {
        fetch(`${AUTOCOMPLETE_URL}?q=${encodeURIComponent(q)}`)
            .then(r => r.json())
            .then(data => {
                acResults = data.results || [];
                renderDropdown(q);
            }).catch(() => closeDropdown());
    }

    function renderDropdown(q) {
        const drop = document.getElementById('entryAcDrop');
        if (!drop) return;
        let html = acResults.map((p, idx) =>
            `<div class="ac-item" data-idx="${idx}" onmousedown="selectProduct(${idx})">
                <div class="ac-name">${escapeHtml(p.label || p.name)}</div>
                ${p.category ? `<div class="ac-cat">${escapeHtml(p.category)}</div>` : ''}
            </div>`
        ).join('');
        html += `<div class="ac-item ac-add-new" data-q="${escapeHtml(q)}" onmousedown="openAddProductModal(this.dataset.q)">
            ➕ افزودن «${escapeHtml(q)}» به عنوان کالای جدید
        </div>`;
        drop.innerHTML = html;
        drop.classList.add('open');
        acHighlightIdx = -1;
    }

    function closeDropdown() {
        const drop = document.getElementById('entryAcDrop');
        if (drop) drop.classList.remove('open');
        acHighlightIdx = -1;
    }

    function selectProduct(idx) {
        const p = acResults[idx];
        if (!p) return;
        document.getElementById('entryProductName').value = p.name;
        document.getElementById('entryProductId').value = p.id || '';
        selectedProductName = p.name || '';
        // دسته‌بندی خودکار از محصول پر می‌شود
        setEntryCategory(p.category_id || '', p.category || '', { silent: true });
        document.getElementById('entryUnit').innerHTML = buildUnitOptions(p.unit || 'عدد');
        closeDropdown();
        document.getElementById('entryQuantity').focus();
    }

    function onAcKeydown(e) {
        const drop = document.getElementById('entryAcDrop');
        if (!drop || !drop.classList.contains('open')) {
            // If dropdown is closed, Enter moves to quantity
            if (e.key === 'Enter' && e.target.id === 'entryProductName') {
                e.preventDefault();
                document.getElementById('entryQuantity').focus();
            }
            return;
        }
        const items = drop.querySelectorAll('.ac-item');
        if (!items.length) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            acHighlightIdx = Math.min((acHighlightIdx === -1 ? 0 : acHighlightIdx + 1), items.length - 1);
            items.forEach(el => el.classList.remove('highlighted'));
            items[acHighlightIdx]?.classList.add('highlighted');
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            acHighlightIdx = Math.max((acHighlightIdx === -1 ? items.length - 1 : acHighlightIdx - 1), 0);
            items.forEach(el => el.classList.remove('highlighted'));
            items[acHighlightIdx]?.classList.add('highlighted');
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (acHighlightIdx >= 0 && acHighlightIdx < items.length) {
                items[acHighlightIdx].dispatchEvent(new MouseEvent('mousedown'));
            }
        } else if (e.key === 'Escape') {
            closeDropdown();
        }
    }

    // Close dropdowns when clicking outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.autocomplete-wrap')) {
            closeDropdown();
        }
    });

    // ── مرور کالاهای دسته‌بندی (و همه کالاها) از روی فیلد اصلی «نام کالا» ──
    // وقتی دسته‌بندی یا «همه کالاها» انتخاب شده باشد، لیست کالاها مستقیماً زیر
    // همین فیلد باز می‌شود و تایپ در خود فیلد نام کالا آن را فیلتر می‌کند.

    let catBrowseSearch = '';  // عبارت فیلتر از فیلد اصلی نام کالا

    function setEntryCategory(catId, catName, opts) {
        opts = opts || {};
        const sel = document.getElementById('entryCategorySelect');
        let effectiveId = catId ? String(catId) : '';
        let effectiveName = catName || '';
        if (sel) {
            sel.value = effectiveId;
            if (effectiveId && sel.value !== effectiveId) {
                effectiveId = '';
                effectiveName = '';
            }
        }
        document.getElementById('entryCategoryId').value = effectiveId;
        document.getElementById('entryCategoryName').value = effectiveName;
        // عبارت سرچ ریست شود
        catBrowseSearch = '';
        if (!effectiveId) {
            closeDropdown();
        } else if (!opts.silent) {
            document.getElementById('entryProductName').focus();
        }
    }

    // تغییر دسته‌بندی توسط کاربر: اگر کالایی در فرم وارد/انتخاب شده باشد پاک می‌شود
    function onEntryCategorySelect(sel) {
        const prevCatId = document.getElementById('entryCategoryId').value;
        const newCatId = sel.value || '';
        const nameInput = document.getElementById('entryProductName');
        const hasProduct = nameInput.value.trim() || document.getElementById('entryProductId').value;
        if (hasProduct && newCatId !== prevCatId) {
            // پاک کردن کالای واردشده چون دسته‌بندی تغییر کرد
            nameInput.value = '';
            document.getElementById('entryProductId').value = '';
            selectedProductName = '';
            document.getElementById('entryUnit').innerHTML = buildUnitOptions('عدد');
            showEntryStockHint(null);
            catBrowseSearch = '';
        }
        setEntryCategory(newCatId, sel.selectedOptions[0] ? sel.selectedOptions[0].textContent.trim() : '');
    }

    // فوکوس/کلیک روی فیلد نام کالا: اگر دسته یا «همه کالاها» انتخاب شده باشد،
    // لیست کالاها زیر همین فیلد باز می‌شود؛ در غیر این صورت رفتار autocomplete عادی
    function onProductFocus(input) {
        const catId = document.getElementById('entryCategoryId').value;
        if (!catId) return;  // بدون دسته: autocomplete عادی هنگام تایپ باز می‌شود
        catBrowseSearch = input.value.trim();
        fetchCategoryProducts();
    }

    function fetchCategoryProducts() {
        const catId = document.getElementById('entryCategoryId').value;
        if (!catId) return;
        const q = catBrowseSearch;
        let url;
        if (catId === 'all') {
            url = ALL_CATEGORY_PRODUCTS_URL + (q ? `?q=${encodeURIComponent(q)}` : '');
        } else {
            // URL دسته‌ای: /<cafe_code>/api/categories/<id>/products/
            const parts = window.location.pathname.split('/').filter(Boolean);
            const base = '/' + (parts.slice(0, 1).join('/'));
            url = `${base}/api/categories/${catId}/products/` + (q ? `?q=${encodeURIComponent(q)}` : '');
        }
        fetch(url)
            .then(r => r.json())
            .then(data => {
                const results = data.results || [];
                window._catProducts = results;
                renderCategoryProductsDropdown(results, q);
            }).catch(() => {});
    }

    function renderCategoryProductsDropdown(results, q) {
        const catId = document.getElementById('entryCategoryId').value;
        const drop = document.getElementById('entryAcDrop');
        if (!drop) return;

        let html = '';
        if (!results.length) {
            html += '<div class="cat-empty">' +
                (catId === 'all' ? 'کالایی' : 'کالایی در این دسته') +
                (q ? ' با این عبارت' : '') + ' یافت نشد.</div>';
        } else {
            html += results.map((p, idx) => {
                const subParts = [];
                if (catId === 'all' && p.category) subParts.push(escapeHtml(p.category));
                if (p.unit) subParts.push('واحد: ' + escapeHtml(p.unit));
                return `<div class="ac-item" onmousedown="selectCategoryProduct(${idx})">
                    <div class="ac-name">${escapeHtml(p.label || p.name)}</div>
                    ${subParts.length ? `<div class="ac-cat">${subParts.join(' · ')}</div>` : ''}
                </div>`;
            }).join('');
        }
        if (q) {
            html += `<div class="ac-item ac-add-new" data-q="${escapeHtml(q)}" onmousedown="openAddProductModal(this.dataset.q)">
                ➕ افزودن «${escapeHtml(q)}» به عنوان کالای جدید
            </div>`;
        }
        drop.innerHTML = html;
        drop.classList.add('open');
        acHighlightIdx = -1;
    }

    function selectCategoryProduct(idx) {
        const p = (window._catProducts || [])[idx];
        if (!p) return;
        document.getElementById('entryProductName').value = p.name;
        document.getElementById('entryProductId').value = p.id || '';
        selectedProductName = p.name || '';
        // پس از انتخاب کالا، دسته‌بندی خودکار روی دستهٔ واقعی کالا تنظیم می‌شود
        if (p.category_id) {
            setEntryCategory(p.category_id, p.category || '', { silent: true });
        } else if (document.getElementById('entryCategoryId').value === 'all') {
            setEntryCategory('', '', { silent: true });
        }
        document.getElementById('entryUnit').innerHTML = buildUnitOptions(p.unit || 'عدد');
        closeDropdown();
        document.getElementById('entryQuantity').focus();
    }

    // ── Add New Product Modal ─────────────────────────────────────────

    function openAddProductModal(prefillName = '') {
        pendingProductItemId = 'entry';
        document.getElementById('newProductName').value = prefillName;
        document.getElementById('newProductDescription').value = '';
        document.getElementById('newProductErr').textContent = '';
        document.getElementById('addProductModal').classList.add('open');
        setTimeout(() => document.getElementById('newProductName').focus(), 100);
    }

    function closeAddProductModal() {
        document.getElementById('addProductModal').classList.remove('open');
        pendingProductItemId = null;
    }

    function submitNewProduct() {
        const name = document.getElementById('newProductName').value.trim();
        const unit = document.getElementById('newProductUnit').value;
        const description = document.getElementById('newProductDescription').value.trim();
        const errEl = document.getElementById('newProductErr');
        if (!name) { errEl.textContent = 'نام کالا اجباری است'; return; }
        // توضیحات برای کالای جدید اجباری است
        if (!description) {
            errEl.textContent = '⚠️ توضیحات اجباری است — لطفاً توضیح کوتاهی بنویس.';
            document.getElementById('newProductDescription').focus();
            return;
        }
        const btn = document.querySelector('.modal-btn-save');
        btn.disabled = true; btn.textContent = 'در حال ذخیره...';
        fetch(QUICK_ADD_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
            body: JSON.stringify({ name, unit, description })
        }).then(r => r.json()).then(data => {
            btn.disabled = false; btn.textContent = 'ذخیره و انتخاب';
            if (data.success) {
                document.getElementById('entryProductName').value = data.product.name;
                document.getElementById('entryProductId').value = data.product.id;
                selectedProductName = data.product.name || '';
                // دسته قبلی نباید انتخاب‌مانده بماند
                setEntryCategory('', '', { silent: true });
                if (data.product.unit) {
                    document.getElementById('entryUnit').innerHTML = buildUnitOptions(data.product.unit);
                }
                // توضیحات کالای جدید → فیلد توضیحات درخواست در فرم اصلی
                if (description && !document.getElementById('entryDescription').value.trim()) {
                    document.getElementById('entryDescription').value = description;
                }
                closeAddProductModal();
            } else {
                errEl.textContent = data.error || 'خطا در افزودن کالا';
            }
        }).catch(() => {
            btn.disabled = false; btn.textContent = 'ذخیره و انتخاب';
            errEl.textContent = 'خطای شبکه. لطفاً دوباره تلاش کنید.';
        });
    }

    document.getElementById('newProductName').addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); submitNewProduct(); }
    });

    document.getElementById('addProductModal').addEventListener('click', function(e) {
        if (e.target === this) closeAddProductModal();
    });

    document.getElementById('availabilityModal').addEventListener('click', function(e) {
        if (e.target === this) closeAvailabilityModal();
    });

    // ── Form submission ────────────────────────────────────────────────

    function populateHiddenFields() {
        const container = document.getElementById('hiddenFieldsContainer');
        container.innerHTML = '';

        items.forEach(item => {
            const fields = [
                { name: 'product_name', value: item.name },
                { name: 'item_product_id', value: item.productId || '' },
                { name: 'item_quantity', value: item.quantity },
                { name: 'item_unit', value: item.unit },
                { name: 'item_description', value: item.description || '' },
            ];
            fields.forEach(f => {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = f.name;
                input.value = f.value;
                container.appendChild(input);
            });
        });
    }

    document.getElementById('purchaseForm').addEventListener('submit', function(e) {
        // Validate delivery time: یک روز از هفته آینده (یکشنبه تا پنجشنبه)
        if (!selectedDeliveryDate) {
            e.preventDefault();
            alert('⚠️ لطفاً زمان تحویل را انتخاب کنید (یکی از روزهای هفته آینده).');
            const sec = document.querySelector('.delivery-section');
            if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return;
        }

        // Validate at least one item
        if (items.length === 0) {
            e.preventDefault();
            alert('⚠️ لطفاً حداقل یک کالا اضافه کنید');
            return;
        }

        // Check for unsaved data in entry form
        const entryName = document.getElementById('entryProductName').value.trim();
        if (entryName) {
            if (!confirm('در فرم افزودن کالا اطلاعاتی وجود دارد که ذخیره نشده است. آیا مایل به ادامه هستید؟')) {
                e.preventDefault();
                return;
            }
        }

        populateHiddenFields();
    });

    // ── Init ──────────────────────────────────────────────────────────
    // انتخاب پیش‌فرض: فردا (اولین روز لیست تحویل — اگر فردا جمعه/شنبه باشد اولین روز کاری بعدی مثل یکشنبه)
    const firstDeliveryChip = document.querySelector('.delivery-chip[data-date]');
    if (firstDeliveryChip) selectDeliveryChip(firstDeliveryChip);
    renderItemsTable();
