/* سرچ و انتخاب تأمین‌کننده در فرم‌های بدهی — بدون وابستگی
   استفاده: data تأمین‌کنندگان با json_script (id="suppliers-data") رندر شود و هر فرم
   ساختار زیر را داشته باشد:
   <div class="sup-picker">
     <input type="text" class="sup-input" name="company" ...>
     <input type="hidden" class="sup-hidden" name="supplier_id" value="...">
     <div class="sup-dropdown"></div>
   </div>
*/
(function() {
    'use strict';
    var dataEl = document.getElementById('suppliers-data');
    var SUPPLIERS = dataEl ? JSON.parse(dataEl.textContent || '[]') : [];

    function initPicker(root) {
        var input = root.querySelector('.sup-input');
        var hidden = root.querySelector('.sup-hidden');
        var dropdown = root.querySelector('.sup-dropdown');
        if (!input || !hidden || !dropdown) return;

        // نام انتخاب‌شده فعلی (مثلاً در ویرایش بدهی) — برای پاک‌کردن انتخاب با ویرایش دستی نام
        var selectedName = function() {
            var cur = SUPPLIERS.filter(function(s) { return String(s.id) === hidden.value; })[0];
            return cur ? cur.name : '';
        };

        function render() {
            var term = input.value.trim();
            var matches;
            if (term) {
                var t = term.toLowerCase();
                matches = SUPPLIERS.filter(function(s) {
                    return s.name.toLowerCase().indexOf(t) !== -1
                        || (s.bank_name && s.bank_name.toLowerCase().indexOf(t) !== -1)
                        || (s.card_number && s.card_number.indexOf(t) !== -1)
                        || (s.sheba_number && s.sheba_number.indexOf(t) !== -1);
                });
            } else {
                matches = SUPPLIERS.slice(0, 20);  // بدون جستجو: لیست تا ۲۰ مورد
            }
            if (!matches.length) { dropdown.classList.remove('show'); dropdown.innerHTML = ''; return; }
            dropdown.innerHTML = matches.map(function(s) {
                var extra = [];
                if (s.bank_name) extra.push(s.bank_name);
                if (s.card_number) extra.push('کارت: ' + s.card_number);
                if (s.sheba_number) extra.push('شبا: ' + s.sheba_number);
                return '<div class="sup-item" data-id="' + s.id + '">'
                     + '<div class="sup-item-name">' + s.name + '</div>'
                     + (extra.length ? '<div class="sup-item-bank">' + extra.join(' • ') + '</div>' : '')
                     + '</div>';
            }).join('');
            dropdown.classList.add('show');
        }

        function select(s) {
            input.value = s.name;
            hidden.value = s.id;
            dropdown.classList.remove('show');
            dropdown.innerHTML = '';
        }

        input.addEventListener('input', function() {
            // اگر کاربر نام را دستی تغییر داد، انتخاب قبلی باطل می‌شود (نام آزاد مجاز است)
            if (this.value !== selectedName()) hidden.value = '';
            render();
        });
        input.addEventListener('focus', render);
        input.addEventListener('blur', function() {
            // حذف با تأخیر تا کلیک روی آیتم‌های لیست فرصت داشته باشد
            setTimeout(function() { dropdown.classList.remove('show'); }, 150);
        });
        dropdown.addEventListener('click', function(e) {
            var item = e.target.closest ? e.target.closest('.sup-item') : null;
            if (!item) return;
            var s = SUPPLIERS.filter(function(x) { return String(x.id) === item.dataset.id; })[0];
            if (s) select(s);
        });
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') dropdown.classList.remove('show');
        });
    }

    document.querySelectorAll('.sup-picker').forEach(initPicker);
})();
