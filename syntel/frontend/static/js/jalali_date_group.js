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
            var raw = String(hidden.value || '').trim();
            if (!raw) return;
            var datePart = raw.split('T')[0].split(' ')[0];
            var parts = datePart.split(/[\/\-]/);
            if (parts.length === 3) {
                year.value = parts[0];
                month.value = parts[1];
                day.value = parts[2];
            }
        }

        function combine() {
            var y = String(year.value || '').trim();
            var m = String(month.value || '').trim();
            var d = String(day.value || '').trim();
            if (y && m && d) {
                hidden.value = y + '/' + pad2(m) + '/' + pad2(d);
            } else {
                hidden.value = '';
            }
        }

        function limit(el, maxLen) {
            if (el.value.length > maxLen) el.value = el.value.slice(0, maxLen);
        }

        year.addEventListener('input', function () {
            limit(year, 4);
            if (year.value.length === 4) month.focus();
        });
        month.addEventListener('input', function () {
            limit(month, 2);
            if (month.value.length === 2) day.focus();
        });
        day.addEventListener('input', function () {
            limit(day, 2);
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
