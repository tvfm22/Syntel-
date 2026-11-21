/* ═══════════════════════════════════════════════════════════════════════════
   باکس تاریخ شمسی — سه فیلد سال / ماه / روز + ورودی مخفی YYYY/MM/DD
   دقیقاً مثل باکس تاریخ «ثبت فاکتور نقدی»:
     - مقدار اولیهٔ مخفی به سه فیلد شکسته می‌شود
     - سال که ۴ رقم شد → ماه، ماه که ۲ رقم شد → روز
     - قبل از ارسال فرم، سه فیلد در ورودی مخفی ترکیب می‌شوند
   راه‌اندازی خودکار روی هر [data-jalali-group] انجام می‌شود.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    function normalizeDigits(value) {
        return String(value || '')
            .replace(/[۰-۹]/g, function (c) { return String(c.charCodeAt(0) - 0x06F0); })
            .replace(/[٠-٩]/g, function (c) { return String(c.charCodeAt(0) - 0x0660); });
    }

    function pad2(value) {
        return String(value).padStart(2, '0');
    }

    function initGroup(group) {
        if (!group || group.getAttribute('data-jd-ready') === '1') return;
        group.setAttribute('data-jd-ready', '1');

        var hiddenId = group.getAttribute('data-jalali-group');
        var hidden = hiddenId ? document.getElementById(hiddenId) : null;
        if (!hidden) return;

        var year = group.querySelector('input[data-jd="year"]');
        var month = group.querySelector('input[data-jd="month"]');
        var day = group.querySelector('input[data-jd="day"]');
        if (!year || !month || !day) return;

        function fillFromHidden() {
            var raw = normalizeDigits(hidden.value).trim();
            if (!raw) return;
            var datePart = raw.split('T')[0].split(' ')[0];
            var parts = datePart.split(/[\/\-]/);
            if (parts.length === 3) {
                year.value = parts[0].replace(/\D/g, '').slice(0, 4);
                month.value = parts[1].replace(/\D/g, '').slice(0, 2);
                day.value = parts[2].replace(/\D/g, '').slice(0, 2);
            }
        }

        function combine() {
            var y = normalizeDigits(year.value).replace(/\D/g, '');
            var m = normalizeDigits(month.value).replace(/\D/g, '');
            var d = normalizeDigits(day.value).replace(/\D/g, '');
            if (y.length === 4 && m && d) {
                var mNum = parseInt(m, 10);
                var dNum = parseInt(d, 10);
                if (mNum < 1) mNum = 1;
                if (mNum > 12) mNum = 12;
                var maxDay = mNum <= 6 ? 31 : (mNum <= 11 ? 30 : 30);
                if (dNum < 1) dNum = 1;
                if (dNum > maxDay) dNum = maxDay;
                month.value = pad2(mNum);
                day.value = pad2(dNum);
                hidden.value = y + '/' + pad2(mNum) + '/' + pad2(dNum);
            } else {
                hidden.value = '';
            }
        }

        function cleanInput(el, maxLen) {
            var cleaned = normalizeDigits(el.value).replace(/\D/g, '');
            if (cleaned.length > maxLen) cleaned = cleaned.slice(0, maxLen);
            if (el.value !== cleaned) el.value = cleaned;
        }

        year.addEventListener('input', function () {
            cleanInput(year, 4);
            if (year.value.length === 4) month.focus();
        });
        month.addEventListener('input', function () {
            cleanInput(month, 2);
            if (month.value.length === 2) day.focus();
        });
        day.addEventListener('input', function () {
            cleanInput(day, 2);
        });

        group.addEventListener('input', combine);
        group.addEventListener('change', combine);

        var form = group.closest('form');
        if (form) form.addEventListener('submit', combine);

        fillFromHidden();
        combine();
    }

    function initAll(root) {
        var scope = root || document;
        var groups = scope.querySelectorAll('[data-jalali-group]');
        for (var i = 0; i < groups.length; i++) initGroup(groups[i]);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { initAll(); });
    } else {
        initAll();
    }

    // برای استفادهٔ دستی پس از افزودن داینامیک باکس
    window.initJalaliDateGroups = initAll;
})();
