(function () {
    'use strict';

    const _cogsCfg = (document.getElementById('cogsManageConfig') || {}).dataset || {};
    const PREVIEW_URL = _cogsCfg.previewUrl || '';
    const PRICE_URL = _cogsCfg.priceUrl || '';
    const RAW_URL = _cogsCfg.rawUrl || '';
    const SECTION_URL = _cogsCfg.sectionUrl || '';
    const CSRF_TOKEN = _cogsCfg.csrfToken || (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';

    const fa = n => String(n);
    const num = v => {
        if (v === null || v === undefined || v === '') return NaN;
        const cleaned = String(v)
            .replace(/[٠-٩]/g, d => String(d.charCodeAt(0) - 0x0660))
            .replace(/[۰-۹]/g, d => String(d.charCodeAt(0) - 0x06F0))
            .replace(/٫/g, '.')
            .replace(/[,،٬\s]/g, '');
        const n = Number(cleaned);
        return isFinite(n) ? n : NaN;
    };
    const fmtNum = v => {
        const n = Number(v);
        if (!isFinite(n)) return '—';
        return fa(n.toLocaleString('en-US', { maximumFractionDigits: 4 }));
    };

    /* ---------- مودال ---------- */
    function openModal(id) {
        document.getElementById(id).classList.add('is-open');
        document.body.style.overflow = 'hidden';
    }
    function closeModal(id) {
        document.getElementById(id).classList.remove('is-open');
        if (!document.querySelector('.cogs-modal.is-open')) document.body.style.overflow = '';
    }
    document.querySelectorAll('[data-close]').forEach(btn => {
        btn.addEventListener('click', () => closeModal(btn.dataset.close));
    });
    document.querySelectorAll('.cogs-modal').forEach(modal => {
        modal.addEventListener('click', e => {
            if (e.target === modal) closeModal(modal.id);
        });
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            document.querySelectorAll('.cogs-modal.is-open').forEach(m => closeModal(m.id));
        }
    });

    function showAlert(id, text) {
        const box = document.getElementById(id);
        box.textContent = text;
        box.classList.add('is-open');
    }
    function hideAlert(id) {
        document.getElementById(id).classList.remove('is-open');
    }

    /* ---------- الف) نوع آیتم: غذای منو / نیمه‌آماده ---------- */
    const kindInput = document.getElementById('itemKind');
    const dishWrap = document.getElementById('dishSectionWrap');
    const prepWrap = document.getElementById('prepSectionWrap');
    const dishSelect = document.getElementById('dishSectionSelect');
    const prepSelect = document.getElementById('prepSectionSelect');
    const outUnit = document.getElementById('itemOutUnit');
    const newSectionBox = document.getElementById('newSectionBox');

    function applyKind(kind) {
        kindInput.value = kind;
        document.querySelectorAll('.kind-tab').forEach(t => {
            t.classList.toggle('is-active', t.dataset.kind === kind);
        });
        const isPrep = kind === 'prep';
        dishWrap.style.display = isPrep ? 'none' : '';
        prepWrap.style.display = isPrep ? '' : 'none';
        dishSelect.disabled = isPrep;
        prepSelect.disabled = !isPrep;
        // جعبهٔ «بخش جدید» فقط وقتی باز می‌ماند که گزینهٔ ساخت بخش انتخاب شده باشد
        newSectionBox.classList.toggle('is-open', !isPrep && dishSelect.value === '__new__');
        // واحد پیش‌فرض هر نوع
        if (isPrep && outUnit.value === 'پرس') outUnit.value = 'گرم';
        if (!isPrep && outUnit.value === 'گرم') outUnit.value = 'پرس';
        hideAlert('itemFormAlert');
    }
    document.querySelectorAll('.kind-tab').forEach(tab => {
        tab.addEventListener('click', () => applyKind(tab.dataset.kind));
    });
    applyKind('dish');

    /* انتخاب «بخش جدید…» جعبهٔ نام را باز می‌کند */
    dishSelect.addEventListener('change', () => {
        newSectionBox.classList.toggle('is-open', dishSelect.value === '__new__');
        if (dishSelect.value === '__new__') {
            document.getElementById('newSectionName').focus();
        }
    });

    /* ساخت بخش تازه — همان اندپوینت مدیریت بخش‌ها، سپس انتخاب خودکار */
    document.getElementById('btnNewSection').addEventListener('click', function () {
        const input = document.getElementById('newSectionName');
        const name = input.value.trim();
        hideAlert('itemFormAlert');
        if (!name) {
            showAlert('itemFormAlert', 'نام بخش را وارد کنید.');
            return;
        }
        const btn = this;
        btn.disabled = true;

        const body = new URLSearchParams();
        body.append('name', name);
        body.append('kind', 'dish');
        body.append('csrfmiddlewaretoken', CSRF_TOKEN);

        fetch(SECTION_URL, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: body,
        })
            .then(r => r.json())
            .then(data => {
                if (!data.ok) throw new Error(data.error || 'بخش ساخته نشد');
                const section = data.section;
                const opt = document.createElement('option');
                opt.value = section.id;
                opt.textContent = section.name;
                // قبل از گزینهٔ «بخش جدید…»
                const newOpt = Array.from(dishSelect.options)
                    .find(o => o.value === '__new__');
                dishSelect.insertBefore(opt, newOpt || null);
                dishSelect.value = String(section.id);
                newSectionBox.classList.remove('is-open');
                input.value = '';
            })
            .catch(err => showAlert('itemFormAlert', err.message))
            .finally(() => { btn.disabled = false; });
    });

    /* ارسال فرم آیتم: اگر خطا داشت، همان‌جا نشان بده و صفحه را نبر */
    document.getElementById('itemForm').addEventListener('submit', function (e) {
        hideAlert('itemFormAlert');
        const name = this.querySelector('[name="name"]').value.trim();
        if (!name) {
            e.preventDefault();
            showAlert('itemFormAlert', 'نام آیتم را وارد کنید.');
            return;
        }
        if (dishSelect.value === '__new__') {
            e.preventDefault();
            showAlert('itemFormAlert',
                'اول «ساخت بخش» را بزنید یا یک بخش موجود انتخاب کنید.');
        }
    });

    /* ---------- ب) ماده اولیه جدید ---------- */
    document.getElementById('btnNewRaw').addEventListener('click', () => {
        hideAlert('rawFormAlert');
        openModal('rawModal');
    });

    const rawCategory = document.getElementById('rawCategory');
    rawCategory.addEventListener('change', () => {
        document.getElementById('newCategoryBox').style.display =
            rawCategory.value === '__new__' ? '' : 'none';
    });

    /* «بستهٔ دلخواه» در فرم ساخت، دو فیلد اندازه/واحد بسته را باز می‌کند */
    const rawPriceUnit = document.getElementById('rawPriceUnit');
    function toggleRawPackage() {
        const isPackage = rawPriceUnit.value === 'package';
        document.getElementById('rawPackageBox').style.display = isPackage ? '' : 'none';
        document.getElementById('rawPackageUnitBox').style.display = isPackage ? '' : 'none';
    }
    rawPriceUnit.addEventListener('change', toggleRawPackage);
    toggleRawPackage();

    const rawUsageGroup = document.getElementById('rawUsageGroup');
    if (rawUsageGroup) {
        const usageBoxes = Array.from(rawUsageGroup.querySelectorAll('input[name="usage_locations"]'));
        const generalBox = usageBoxes.find(cb => cb.value === 'general');
        usageBoxes.forEach(cb => {
            cb.addEventListener('change', () => {
                if (cb === generalBox && cb.checked) {
                    usageBoxes.forEach(other => { if (other !== generalBox) other.checked = false; });
                } else if (cb !== generalBox && cb.checked && generalBox) {
                    generalBox.checked = false;
                }
                if (!usageBoxes.some(other => other.checked) && generalBox) {
                    generalBox.checked = true;
                }
            });
        });
    }

    document.getElementById('rawForm').addEventListener('submit', function (e) {
        e.preventDefault();
        const btn = this.querySelector('[type="submit"]');
        const form = this;
        hideAlert('rawFormAlert');

        // «دستهٔ جدید» به‌صورت نام ارسال می‌شود، نه شناسه
        if (rawCategory.value === '__new__') {
            const name = form.querySelector('[name="category_name"]').value.trim();
            if (!name) {
                showAlert('rawFormAlert', 'نام دستهٔ جدید را وارد کنید.');
                return;
            }
        }
        const data = new FormData(form);
        if (data.get('category_id') === '__new__') data.delete('category_id');

        btn.disabled = true;
        const original = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ساخت...';

        fetch(RAW_URL, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': CSRF_TOKEN },
            body: data,
        })
            .then(r => r.json())
            .then(res => {
                if (!res.ok) throw new Error(res.error || 'ساخته نشد');
                window.location.reload();
            })
            .catch(err => showAlert('rawFormAlert', err.message))
            .finally(() => {
                btn.disabled = false;
                btn.innerHTML = original;
            });
    });

    /* ---------- ج) ورود قیمت + پیش‌نمایش زنده ---------- */
    const pmPrice = document.getElementById('pmPrice');
    const pmUnit = document.getElementById('pmUnit');
    const pmPackageFields = document.getElementById('pmPackageFields');
    const pmPackageSize = document.getElementById('pmPackageSize');
    const pmPackageUnit = document.getElementById('pmPackageUnit');
    const pmPreview = document.getElementById('pmPreview');
    const pmPreviewEntered = document.getElementById('pmPreviewEntered');
    const pmPreviewStored = document.getElementById('pmPreviewStored');
    let previewTimer = null;
    let previewSeq = 0;

    function togglePackageFields() {
        pmPackageFields.classList.toggle('is-open', pmUnit.value === 'package');
    }

    /** پیش‌نمایش سمت کلاینت — آینهٔ همان قاعدهٔ سرور (کilo→÷۱۰۰۰) */
    function localPreview() {
        const amount = num(pmPrice.value);
        if (!isFinite(amount) || amount <= 0) return null;
        let enteredUnit = '', stored = null, smallest = '';

        if (pmUnit.value === 'package') {
            const size = num(pmPackageSize.value);
            const base = pmPackageUnit.value;
            if (!isFinite(size) || size <= 0) return null;
            const factor = (base === 'کیلوگرم' || base === 'لیتر') ? 1000 : 1;
            smallest = (base === 'کیلوگرم') ? 'گرم'
                     : (base === 'لیتر') ? 'میل' : base;
            stored = amount / (size * factor);
            enteredUnit = 'بستهٔ ' + fmtNum(size) + ' ' + base;
        } else {
            const map = {
                kg: { label: 'کیلوگرم', factor: 1000, smallest: 'گرم' },
                gram: { label: 'گرم', factor: 1, smallest: 'گرم' },
                liter: { label: 'لیتر', factor: 1000, smallest: 'میل' },
                mil: { label: 'میل', factor: 1, smallest: 'میل' },
                piece: { label: 'عدد', factor: 1, smallest: 'عدد' },
            };
            const cfg = map[pmUnit.value];
            if (cfg) {
                enteredUnit = cfg.label;
                smallest = cfg.smallest;
                stored = amount / cfg.factor;
            } else if (pmUnit.value) {
                enteredUnit = pmUnit.value;
                smallest = pmUnit.value;
                stored = amount;
            } else {
                return null;
            }
        }
        return { amount, enteredUnit, stored, smallest };
    }

    function renderPreview() {
        const p = localPreview();
        if (!p) {
            pmPreview.classList.add('is-empty');
            return;
        }
        pmPreview.classList.remove('is-empty');
        pmPreviewEntered.textContent = fmtNum(p.amount) + ' ریال برای ' +
            (pmUnit.value === 'package' ? p.enteredUnit : '۱ ' + p.enteredUnit);
        pmPreviewStored.textContent = fmtNum(p.stored) + ' ریال به ازای هر ۱ ' + p.smallest;
    }

    /** پیش‌نمایش معتبر سرور — برای اطمینان از اینکه UI و سرور یکی می‌گویند */
    function serverPreview() {
        const params = new URLSearchParams({
            price: pmPrice.value,
            unit: pmUnit.value,
            package_size: pmPackageSize.value,
            package_unit: pmPackageUnit.value,
        });
        const seq = ++previewSeq;
        fetch(PREVIEW_URL + '?' + params.toString(), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then(r => r.json())
            .then(data => {
                if (seq !== previewSeq || !data.ok) return;
                pmPreviewEntered.textContent = data.entered_text;
                pmPreviewStored.textContent = data.stored_text;
                pmPreview.classList.remove('is-empty');
            })
            .catch(() => { /* پیش‌نمایش محلی کافی است */ });
    }

    function schedulePreview() {
        renderPreview();
        if (previewTimer) clearTimeout(previewTimer);
        previewTimer = setTimeout(serverPreview, 320);
    }

    [pmPrice, pmPackageSize].forEach(el => el.addEventListener('input', schedulePreview));
    [pmUnit, pmPackageUnit].forEach(el => el.addEventListener('change', () => {
        togglePackageFields();
        schedulePreview();
    }));

    document.querySelectorAll('[data-price-item]').forEach(btn => {
        btn.addEventListener('click', () => {
            hideAlert('priceFormAlert');
            document.getElementById('pmItemId').value = btn.dataset.priceItem;
            document.getElementById('pmTitle').textContent = 'قیمت «' + btn.dataset.name + '»';

            // قیمت فعلی بر حسب واحد پایه است؛ واحد پیش‌فرض = واحد پایه همان کالا
            const baseUnit = (btn.dataset.unit || 'گرم').trim() || 'گرم';
            const unitMap = {
                'گرم': 'gram', 'میل': 'mil', 'عدد': 'piece',
                'کیلوگرم': 'kg', 'لیتر': 'liter',
            };
            pmUnit.querySelectorAll('option[data-custom-unit]').forEach(opt => opt.remove());
            if (unitMap[baseUnit]) {
                pmUnit.value = unitMap[baseUnit];
            } else {
                const customOpt = document.createElement('option');
                customOpt.value = baseUnit;
                customOpt.textContent = '1 ' + baseUnit;
                customOpt.dataset.customUnit = '1';
                const pkgOpt = pmUnit.querySelector('option[value="package"]');
                if (pkgOpt) pmUnit.insertBefore(customOpt, pkgOpt);
                else pmUnit.appendChild(customOpt);
                pmUnit.value = baseUnit;
            }
            pmPrice.value = btn.dataset.price || '';
            if (window.SyntelComma) window.SyntelComma.applyCommaToInput(pmPrice);
            pmPackageSize.value = '';
            togglePackageFields();
            pmPreview.classList.add('is-empty');
            openModal('priceModal');
            setTimeout(() => {
                pmPrice.focus();
                pmPrice.select();
                schedulePreview();
            }, 60);
        });
    });

    document.getElementById('priceForm').addEventListener('submit', function (e) {
        e.preventDefault();
        const btn = this.querySelector('[type="submit"]');
        hideAlert('priceFormAlert');

        if (!isFinite(num(pmPrice.value))) {
            showAlert('priceFormAlert', 'مبلغ را وارد کنید.');
            return;
        }
        if (pmUnit.value === 'package' &&
            (!isFinite(num(pmPackageSize.value)) || num(pmPackageSize.value) <= 0)) {
            showAlert('priceFormAlert', 'وزن یا حجم هر بسته را وارد کنید.');
            return;
        }

        const data = new FormData(this);
        btn.disabled = true;
        const original = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ثبت...';

        fetch(PRICE_URL, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': CSRF_TOKEN },
            body: data,
        })
            .then(r => r.json())
            .then(res => {
                if (!res.ok) throw new Error(res.error || 'ثبت نشد');
                window.location.reload();
            })
            .catch(err => showAlert('priceFormAlert', err.message))
            .finally(() => {
                btn.disabled = false;
                btn.innerHTML = original;
            });
    });
})();
