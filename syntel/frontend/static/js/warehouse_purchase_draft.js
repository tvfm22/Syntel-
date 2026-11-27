const _draftCfg = (document.getElementById('draftPageConfig') || {}).dataset || {};
const ALL_CATEGORY_PRODUCTS_URL = _draftCfg.allCategoryProductsUrl || '';
const QUICK_ADD_URL = _draftCfg.quickAddUrl || '';
const CSRF_TOKEN = _draftCfg.csrfToken || (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
const STD_UNITS = ['عدد','کیلوگرم','گرم','لیتر','بسته','کارتن','جعبه','شیشه','قوطی','بطری','پاکت','متر','دست','جفت'];

function getPersianDate() {
    return new Intl.DateTimeFormat('fa-IR-u-nu-latn', { year: 'numeric', month: 'long', day: 'numeric' }).format(new Date());
}
const persianDateEl = document.getElementById('persianDate');
if (persianDateEl) { persianDateEl.textContent = getPersianDate(); }

function escapeHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function buildUnitOptions(selected) {
    const units = STD_UNITS.slice();
    if (selected && !units.includes(selected)) {
        units.unshift(selected);
    }
    return units.map(u => `<option value="${escapeHtml(u)}" ${u === selected ? 'selected' : ''}>${escapeHtml(u)}</option>`).join('');
}

document.getElementById('entryUnit').innerHTML = buildUnitOptions('عدد');
document.getElementById('editUnit').innerHTML = buildUnitOptions('عدد');
document.getElementById('newProductUnit').innerHTML = buildUnitOptions('عدد');

// ── مدیریت مشترک Autocomplete و مرور دسته‌بندی ──
const acScopes = {
    entry: {
        nameId: 'entryProductName',
        productId: 'product-select',
        dropId: 'entryAcDrop',
        catSelId: 'entryCategorySelect',
        catId: 'entryCategoryId',
        catNameId: 'entryCategoryName',
        unitId: 'entryUnit',
        qtyId: 'entryQuantity',
        descId: 'entryDescription',
        hintId: 'entryProductHint',
        results: [],
        highlightIdx: -1,
        timer: null,
        searchText: ''
    },
    edit: {
        nameId: 'editProductName',
        productId: 'editProductId',
        dropId: 'editAcDrop',
        catSelId: 'editCategorySelect',
        catId: 'editCategoryId',
        catNameId: 'editCategoryName',
        unitId: 'editUnit',
        qtyId: 'editQuantity',
        descId: 'editDescription',
        hintId: null,
        results: [],
        highlightIdx: -1,
        timer: null,
        searchText: ''
    }
};

let activeAddModalScope = 'entry';

function showProductHint(scopeKey, kind, text) {
    const cfg = acScopes[scopeKey];
    if (!cfg || !cfg.hintId) return;
    const el = document.getElementById(cfg.hintId);
    if (!el) return;
    if (!kind) {
        el.className = 'selected-product-hint';
        el.textContent = '';
        return;
    }
    el.className = 'selected-product-hint ' + kind;
    el.textContent = text;
}

function setScopeCategory(scopeKey, catId, catName, opts) {
    opts = opts || {};
    const cfg = acScopes[scopeKey];
    const sel = document.getElementById(cfg.catSelId);
    let effectiveId = catId ? String(catId) : '';
    let effectiveName = catName || '';
    if (sel) {
        sel.value = effectiveId;
        if (effectiveId && sel.value !== effectiveId) {
            effectiveId = '';
            effectiveName = '';
        }
    }
    document.getElementById(cfg.catId).value = effectiveId;
    document.getElementById(cfg.catNameId).value = effectiveName;
    cfg.searchText = '';
    if (!effectiveId) {
        closeDropdown(scopeKey);
    } else if (!opts.silent) {
        document.getElementById(cfg.nameId).focus();
    }
}

function onCategorySelect(sel, scopeKey) {
    const cfg = acScopes[scopeKey];
    const prevCatId = document.getElementById(cfg.catId).value;
    const newCatId = sel.value || '';
    const nameInput = document.getElementById(cfg.nameId);
    const pidInput = document.getElementById(cfg.productId);
    if ((nameInput.value.trim() || pidInput.value) && newCatId !== prevCatId) {
        nameInput.value = '';
        pidInput.value = '';
        document.getElementById(cfg.unitId).innerHTML = buildUnitOptions('عدد');
        showProductHint(scopeKey, null, '');
        cfg.searchText = '';
    }
    const label = sel.selectedOptions[0] ? sel.selectedOptions[0].textContent.trim() : '';
    setScopeCategory(scopeKey, newCatId, label);
}

function buildProductsApiUrl(catId, q) {
    let base;
    if (!catId || catId === 'all') {
        base = ALL_CATEGORY_PRODUCTS_URL;
    } else if (ALL_CATEGORY_PRODUCTS_URL.includes('/all/')) {
        base = ALL_CATEGORY_PRODUCTS_URL.replace('/all/', `/${encodeURIComponent(catId)}/`);
    } else {
        base = ALL_CATEGORY_PRODUCTS_URL.replace(/\/?$/, '/') + `${encodeURIComponent(catId)}/`;
    }
    return base + (q ? `?q=${encodeURIComponent(q)}` : '');
}

function onAcInput(input, scopeKey) {
    const cfg = acScopes[scopeKey];
    const q = input.value.trim();
    document.getElementById(cfg.productId).value = '';
    showProductHint(scopeKey, null, '');
    clearTimeout(cfg.timer);
    const catId = document.getElementById(cfg.catId).value;
    if (catId) {
        cfg.searchText = q;
        cfg.timer = setTimeout(() => fetchProductsForScope(scopeKey, catId, q), q ? 180 : 0);
        return;
    }
    if (q.length < 1) {
        closeDropdown(scopeKey);
        return;
    }
    cfg.timer = setTimeout(() => fetchProductsForScope(scopeKey, 'all', q), 180);
}

function onProductFocus(input, scopeKey) {
    const cfg = acScopes[scopeKey];
    const catId = document.getElementById(cfg.catId).value;
    if (!catId) return;
    cfg.searchText = input.value.trim();
    fetchProductsForScope(scopeKey, catId, cfg.searchText);
}

function fetchProductsForScope(scopeKey, catId, q) {
    const cfg = acScopes[scopeKey];
    fetch(buildProductsApiUrl(catId, q))
        .then(r => r.json())
        .then(data => {
            cfg.results = data.results || [];
            renderScopeDropdown(scopeKey, catId, q);
        }).catch(() => closeDropdown(scopeKey));
}

function renderScopeDropdown(scopeKey, catId, q) {
    const cfg = acScopes[scopeKey];
    const drop = document.getElementById(cfg.dropId);
    if (!drop) return;

    let html = '';
    if (!cfg.results.length) {
        html += '<div class="cat-empty">' +
            (catId === 'all' ? 'کالایی' : 'کالایی در این دسته') +
            (q ? ' با این عبارت' : '') + ' یافت نشد.</div>';
    } else {
        html += cfg.results.map((p, idx) => {
            const subParts = [];
            if (p.category) subParts.push(escapeHtml(p.category));
            if (p.unit) subParts.push('واحد: ' + escapeHtml(p.unit));
            return `<div class="ac-item" data-idx="${idx}" onclick="selectScopeProduct('${scopeKey}', ${idx})">
                <div class="ac-name">${escapeHtml(p.label || p.name)}</div>
                ${subParts.length ? `<div class="ac-cat">${subParts.join(' · ')}</div>` : ''}
            </div>`;
        }).join('');
    }
    if (q) {
        html += `<div class="ac-item ac-add-new" onclick="openAddProductModal('${scopeKey}')">
            ➕ افزودن «${escapeHtml(q)}» به عنوان کالای جدید
        </div>`;
    }
    drop.innerHTML = html;
    drop.classList.add('open');
    cfg.highlightIdx = -1;
}

function selectScopeProduct(scopeKey, idx) {
    const cfg = acScopes[scopeKey];
    const p = cfg.results[idx];
    if (!p) return;
    document.getElementById(cfg.nameId).value = p.name;
    document.getElementById(cfg.productId).value = p.id || '';
    if (p.category_id) {
        setScopeCategory(scopeKey, p.category_id, p.category || '', { silent: true });
    } else if (document.getElementById(cfg.catId).value === 'all') {
        setScopeCategory(scopeKey, '', '', { silent: true });
    }
    document.getElementById(cfg.unitId).innerHTML = buildUnitOptions(p.unit || 'عدد');
    showProductHint(scopeKey, 'is-known', 'کالای انتخاب‌شده: ' + p.name);
    closeDropdown(scopeKey);
    document.getElementById(cfg.qtyId).focus();
}

function closeDropdown(scopeKey) {
    const cfg = acScopes[scopeKey];
    if (!cfg) return;
    const drop = document.getElementById(cfg.dropId);
    if (drop) drop.classList.remove('open');
    cfg.highlightIdx = -1;
}

function onAcKeydown(e, scopeKey) {
    const cfg = acScopes[scopeKey];
    const drop = document.getElementById(cfg.dropId);
    if (!drop || !drop.classList.contains('open')) return;
    const items = drop.querySelectorAll('.ac-item');
    if (!items.length) return;
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        cfg.highlightIdx = Math.min((cfg.highlightIdx === -1 ? 0 : cfg.highlightIdx + 1), items.length - 1);
        items.forEach(el => el.classList.remove('highlighted'));
        items[cfg.highlightIdx]?.classList.add('highlighted');
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        cfg.highlightIdx = Math.max((cfg.highlightIdx === -1 ? items.length - 1 : cfg.highlightIdx - 1), 0);
        items.forEach(el => el.classList.remove('highlighted'));
        items[cfg.highlightIdx]?.classList.add('highlighted');
    } else if (e.key === 'Enter') {
        if (cfg.highlightIdx >= 0 && cfg.highlightIdx < items.length) {
            e.preventDefault();
            items[cfg.highlightIdx].click();
        }
    } else if (e.key === 'Escape') {
        closeDropdown(scopeKey);
    }
}

document.addEventListener('click', (e) => {
    const wrap = e.target.closest('.autocomplete-wrap');
    if (!wrap) {
        closeDropdown('entry');
        closeDropdown('edit');
    } else {
        const s = wrap.getAttribute('data-ac-scope');
        if (s !== 'entry') closeDropdown('entry');
        if (s !== 'edit') closeDropdown('edit');
    }
});

// ── مودال ویرایش قلم ──
function openEditModal(btn) {
    const d = btn.dataset;
    document.getElementById('editAction').value = d.action || 'edit_item';
    document.getElementById('editItemId').value = d.itemId || '';
    document.getElementById('editRequestId').value = d.requestId || '';
    document.getElementById('editProductName').value = d.name || '';
    document.getElementById('editProductId').value = d.productId || '';
    document.getElementById('editQuantity').value = d.quantity || '';
    document.getElementById('editUnit').innerHTML = buildUnitOptions(d.unit || 'عدد');
    document.getElementById('editDescription').value = d.description || '';
    setScopeCategory('edit', '', '', { silent: true });
    closeDropdown('edit');
    document.getElementById('editItemModal').classList.add('open');
    setTimeout(() => document.getElementById('editQuantity').focus(), 80);
}

function closeEditModal() {
    closeDropdown('edit');
    document.getElementById('editItemModal').classList.remove('open');
}

document.getElementById('editItemModal').addEventListener('click', function(e) {
    if (e.target === this) closeEditModal();
});

// ── مودال تعریف کالای جدید ──
function openAddProductModal(scopeKey) {
    activeAddModalScope = scopeKey || 'entry';
    const cfg = acScopes[activeAddModalScope];
    const prefill = document.getElementById(cfg.nameId).value.trim();
    document.getElementById('newProductName').value = prefill;
    document.getElementById('newProductDescription').value = '';
    document.getElementById('newProductErr').textContent = '';
    document.getElementById('addProductModal').classList.add('open');
    setTimeout(() => document.getElementById('newProductName').focus(), 100);
}

function closeAddProductModal() {
    document.getElementById('addProductModal').classList.remove('open');
}

function submitNewProduct() {
    const name = document.getElementById('newProductName').value.trim();
    const unit = document.getElementById('newProductUnit').value;
    const description = document.getElementById('newProductDescription').value.trim();
    const errEl = document.getElementById('newProductErr');
    if (!name) { errEl.textContent = 'نام کالا اجباری است'; return; }
    if (!description) {
        errEl.textContent = 'توضیحات اجباری است.';
        document.getElementById('newProductDescription').focus();
        return;
    }
    const btn = document.getElementById('saveNewProductBtn');
    btn.disabled = true; btn.textContent = 'در حال ذخیره...';
    fetch(QUICK_ADD_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
        body: JSON.stringify({ name, unit, description })
    }).then(r => r.json()).then(data => {
        btn.disabled = false; btn.textContent = 'ذخیره و انتخاب';
        if (data.success) {
            const cfg = acScopes[activeAddModalScope];
            document.getElementById(cfg.nameId).value = data.product.name;
            document.getElementById(cfg.productId).value = data.product.id;
            setScopeCategory(activeAddModalScope, '', '', { silent: true });
            if (data.product.unit) {
                document.getElementById(cfg.unitId).innerHTML = buildUnitOptions(data.product.unit);
            }
            const descEl = document.getElementById(cfg.descId);
            if (description && descEl && !descEl.value.trim()) {
                descEl.value = description;
            }
            showProductHint(activeAddModalScope, 'is-new', 'کالای جدید ثبت شد: ' + data.product.name);
            closeDropdown(activeAddModalScope);
            closeAddProductModal();
            document.getElementById(cfg.qtyId).focus();
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

// ── انتخاب، جستجو و فیلتر اقلام پیش‌نویس ──
(function() {
    const form = document.getElementById('draft-send-form');
    if (!form) return;
    const selectAll = document.getElementById('draft-select-all');
    const hiddenWrap = document.getElementById('draft-selected-hidden-inputs');
    const officerSel = document.getElementById('draft-officer-select');
    const btnText = document.getElementById('draft-send-btn-text');
    const summaryEl = document.getElementById('draft-send-summary');
    const searchInput = document.getElementById('draftItemSearch');
    const routeFilters = document.getElementById('draftRouteFilters');
    const emptyRow = document.getElementById('draftFilterEmpty');
    const rows = Array.from(document.querySelectorAll('tr.draft-item-row'));
    let activeRoute = 'all';

    function applyDraftFilters() {
        const q = (searchInput ? searchInput.value : '').trim().toLowerCase();
        let visibleCount = 0;
        rows.forEach(row => {
            const matchRoute = activeRoute === 'all' || row.dataset.route === activeRoute;
            const hay = (row.dataset.search || '').toLowerCase();
            const matchText = !q || hay.includes(q);
            const show = matchRoute && matchText;
            row.style.display = show ? '' : 'none';
            if (show) visibleCount += 1;
        });
        if (emptyRow) {
            emptyRow.style.display = visibleCount === 0 ? 'block' : 'none';
        }
        syncDraftSelection();
    }

    function syncDraftSelection() {
        const visibleRows = rows.filter(r => r.style.display !== 'none');
        const visibleChks = visibleRows.map(r => r.querySelector('.draft-item-chk')).filter(Boolean);
        const allChecked = document.querySelectorAll('.draft-item-chk:checked');

        rows.forEach(row => {
            const chk = row.querySelector('.draft-item-chk');
            if (chk) {
                row.classList.toggle('is-unselected', !chk.checked);
            }
        });

        if (selectAll) {
            const visibleCheckedCount = visibleChks.filter(c => c.checked).length;
            selectAll.checked = visibleChks.length > 0 && visibleCheckedCount === visibleChks.length;
        }
        if (summaryEl) {
            summaryEl.textContent = allChecked.length + ' قلم انتخاب‌شده';
        }
        if (btnText) {
            btnText.textContent = allChecked.length > 0
                ? 'ارسال ' + allChecked.length + ' قلم انتخاب‌شده'
                : 'ارسال اقلام انتخاب‌شده';
        }
    }

    if (searchInput) {
        searchInput.addEventListener('input', applyDraftFilters);
    }
    if (routeFilters) {
        routeFilters.querySelectorAll('.filter-pill-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                routeFilters.querySelectorAll('.filter-pill-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                activeRoute = btn.dataset.route || 'all';
                applyDraftFilters();
            });
        });
    }

    if (selectAll) {
        selectAll.addEventListener('change', function() {
            rows.forEach(row => {
                if (row.style.display === 'none') return;
                const chk = row.querySelector('.draft-item-chk');
                if (chk) chk.checked = selectAll.checked;
            });
            syncDraftSelection();
        });
    }
    document.querySelectorAll('.draft-item-chk').forEach(chk => {
        chk.addEventListener('change', syncDraftSelection);
    });
    syncDraftSelection();

    form.addEventListener('submit', function(e) {
        const submitterVal = e.submitter ? e.submitter.value : 'send';
        if (submitterVal === 'discard_draft') {
            return;
        }
        const checked = document.querySelectorAll('.draft-item-chk:checked');
        if (checked.length === 0) {
            e.preventDefault();
            alert('لطفاً حداقل یک قلم را برای ارسال انتخاب کنید.');
            return;
        }
        if (!officerSel || !officerSel.value) {
            e.preventDefault();
            alert('لطفاً مسئول خرید یا گزینه «خرید توسط خودم» را انتخاب کنید.');
            return;
        }
        hiddenWrap.innerHTML = '';
        checked.forEach(chk => {
            const inp = document.createElement('input');
            inp.type = 'hidden';
            inp.name = 'selected_item_ids';
            inp.value = chk.value;
            hiddenWrap.appendChild(inp);
        });
    });
})();
