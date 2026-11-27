    function dismissAlert(el) {
        if (!el) return;
        el.classList.add('alert-fading');
        setTimeout(function() { el.remove(); }, 420);
    }
    // اعلان‌ها بعد از ۴ ثانیه خودکار محو می‌شوند
    (function() {
        var alerts = document.querySelectorAll('[data-alert]');
        if (alerts.length) {
            setTimeout(function() { alerts.forEach(function(a) { dismissAlert(a); }); }, 4000);
        }
    })();
    

/**
 * Toggle mobile sidebar open/close with animation.
 * Handles body scroll lock, ARIA states, and icon swap.
 */
function toggleSidebar() {
        var sidebar = document.getElementById('sidebar');
        var overlay = document.getElementById('sidebarOverlay');
        var toggle = document.getElementById('menuToggle');
        var isOpen = sidebar.classList.contains('open');

        if (isOpen) {
            // ── Close ──
            sidebar.classList.remove('open');
            overlay.classList.remove('show');
            document.body.classList.remove('menu-open');
            toggle.setAttribute('aria-expanded', 'false');
            toggle.setAttribute('aria-label', 'باز کردن منو');
            var icon = toggle.querySelector('i');
            if (icon) icon.className = 'fas fa-bars';
            // Return focus to toggle after animation
            setTimeout(function() { toggle.focus(); }, 100);
        } else {
            // ── Open ──
            sidebar.classList.add('open');
            overlay.classList.add('show');
            document.body.classList.add('menu-open');
            toggle.setAttribute('aria-expanded', 'true');
            toggle.setAttribute('aria-label', 'بستن منو');
            var icon = toggle.querySelector('i');
            if (icon) icon.className = 'fas fa-times';
            // Focus first nav item for keyboard users
            setTimeout(function() {
                var firstNav = sidebar.querySelector('.nav-item');
                if (firstNav) firstNav.focus();
            }, 150);
        }
    }

    // Close sidebar on Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            var sidebar = document.getElementById('sidebar');
            if (sidebar && sidebar.classList.contains('open')) {
                toggleSidebar();
                e.preventDefault();
            }
        }
    });

    // DOM ready setup
    document.addEventListener('DOMContentLoaded', function() {
        // نمایش مینیمال تاریخ امروز بدون سال در نوار بالای موبایل
        var mobileDateEl = document.getElementById('mobileTopBarDateText');
        if (mobileDateEl) {
            try {
                mobileDateEl.textContent = new Intl.DateTimeFormat('fa-IR-u-nu-latn', {
                    month: 'long',
                    day: 'numeric'
                }).format(new Date());
            } catch (e) {}
        }

        // اسکرول خودکار منوی کناری به آیتم فعال تا کاربر همیشه جایگاه فعلی خود را ببیند
        var activeNavItem = document.querySelector('.sidebar-nav .nav-sub-item.active')
            || document.querySelector('.sidebar-nav .nav-item.active');
        if (activeNavItem && typeof activeNavItem.scrollIntoView === 'function') {
            try {
                activeNavItem.scrollIntoView({ block: 'nearest', behavior: 'auto' });
            } catch (e) {}
        }

        // Close sidebar on nav link click (mobile)
        document.querySelectorAll('.nav-item').forEach(function(link) {
            link.addEventListener('click', function() {
                var sidebar = document.getElementById('sidebar');
                if (sidebar && window.innerWidth < 1024 && sidebar.classList.contains('open')) {
                    setTimeout(function() {
                        sidebar.classList.remove('open');
                        var overlay = document.getElementById('sidebarOverlay');
                        if (overlay) overlay.classList.remove('show');
                        document.body.classList.remove('menu-open');
                        var toggle = document.getElementById('menuToggle');
                        if (toggle) {
                            toggle.setAttribute('aria-expanded', 'false');
                            toggle.setAttribute('aria-label', 'باز کردن منو');
                            var icon = toggle.querySelector('i');
                            if (icon) icon.className = 'fas fa-bars';
                        }
                    }, 200);
                }
            });
        });

        // Window resize handler
        var resizeTimer;
        window.addEventListener('resize', function() {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(function() {
                if (window.innerWidth >= 1024) {
                    var sidebar = document.getElementById('sidebar');
                    var overlay = document.getElementById('sidebarOverlay');
                    if (sidebar && sidebar.classList.contains('open')) {
                        sidebar.classList.remove('open');
                        if (overlay) overlay.classList.remove('show');
                        document.body.classList.remove('menu-open');
                        var toggle = document.getElementById('menuToggle');
                        if (toggle) {
                            toggle.setAttribute('aria-expanded', 'false');
                            toggle.setAttribute('aria-label', 'باز کردن منو');
                            var icon = toggle.querySelector('i');
                            if (icon) icon.className = 'fas fa-bars';
                        }
                    }
                }
            }, 250);
        });
    });

    // Focus trap inside mobile sidebar when open
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Tab') {
            var sidebar = document.getElementById('sidebar');
            if (!sidebar || !sidebar.classList.contains('open')) return;
            if (window.innerWidth >= 1024) return;

            var focusable = Array.prototype.filter.call(
                sidebar.querySelectorAll('a, button, [tabindex]:not([tabindex="-1"])'),
                function(el) { return el.offsetParent !== null; }
            );
            if (focusable.length === 0) return;

            var first = focusable[0];
            var last = focusable[focusable.length - 1];

            if (e.shiftKey) {
                if (document.activeElement === first) {
                    e.preventDefault();
                    last.focus();
                }
            } else {
                if (document.activeElement === last) {
                    e.preventDefault();
                    first.focus();
                }
            }
        }
    });


/* قفل سمت کلاینت فقط به‌عنوان لایهٔ دوم؛ گارد اصلی در سرور است. */
(function () {
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form.matches || !form.matches('form[data-lock-submit]')) return;
        if (form.dataset.submitting === '1') {
            event.preventDefault();
            return;
        }
        form.dataset.submitting = '1';
        setTimeout(function () {
            form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (button) {
                button.disabled = true;
            });
        }, 0);
    });
})();


/* جداکنندهٔ سه‌رقمی با کاما برای همهٔ فیلدهای مبلغ (با پشتیبانی اعداد فارسی/عربی و اعشار) */
(function () {
    function toAsciiDigits(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/[۰-۹]/g, function (d) { return String(d.charCodeAt(0) - 0x06F0); })
            .replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 0x0660); })
            .replace(/٫/g, '.');
    }

    function stripCommas(value) {
        return toAsciiDigits(value).replace(/[,،٬\s]/g, '');
    }

    function formatCommaString(rawValue, allowDecimal) {
        var text = toAsciiDigits(rawValue);
        if (!text) return '';

        if (!allowDecimal) {
            var digits = text.replace(/[^0-9]/g, '');
            if (!digits) return '';
            digits = digits.replace(/^0+(?=\d)/, '');
            return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        }

        var cleaned = '';
        var seenDot = false;
        for (var i = 0; i < text.length; i++) {
            var ch = text.charAt(i);
            if (ch >= '0' && ch <= '9') {
                cleaned += ch;
            } else if (ch === '.' && !seenDot) {
                cleaned += '.';
                seenDot = true;
            }
        }
        if (!cleaned) return '';

        var dotIdx = cleaned.indexOf('.');
        if (dotIdx === -1) {
            var intOnly = cleaned.replace(/^0+(?=\d)/, '');
            return intOnly.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        }

        var intPart = cleaned.slice(0, dotIdx).replace(/^0+(?=\d)/, '');
        var decPart = cleaned.slice(dotIdx + 1);
        if (!intPart) intPart = '0';
        return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + decPart;
    }

    function isDecimalInput(input) {
        if (!input) return false;
        if (input.dataset && input.dataset.commaInt === '1') return false;
        if (input.dataset && input.dataset.commaDecimal === '1') return true;
        return (input.getAttribute('inputmode') || '').toLowerCase() === 'decimal';
    }

    function applyCommaToInput(input) {
        if (!input) return;
        var allowDecimal = isDecimalInput(input);
        var raw = input.value;
        var isFocused = (document.activeElement === input) && (typeof input.selectionStart === 'number');
        var sigBefore = 0;
        var dotPrependAdjust = 0;

        if (isFocused) {
            var caret = input.selectionStart;
            var prefixAscii = toAsciiDigits(raw.slice(0, caret));
            var seenDot = false;
            var hasDigitBeforeDot = false;
            for (var i = 0; i < prefixAscii.length; i++) {
                var ch = prefixAscii.charAt(i);
                if (ch >= '0' && ch <= '9') {
                    sigBefore++;
                    if (!seenDot) hasDigitBeforeDot = true;
                } else if (allowDecimal && ch === '.' && !seenDot) {
                    sigBefore++;
                    seenDot = true;
                }
            }
            if (allowDecimal && seenDot && !hasDigitBeforeDot) {
                dotPrependAdjust = 1;
            }
        }

        var formatted = formatCommaString(raw, allowDecimal);
        if (formatted !== raw) {
            input.value = formatted;
            if (isFocused) {
                var targetSig = sigBefore + dotPrependAdjust;
                var newPos = 0;
                if (targetSig > 0) {
                    var seen = 0;
                    while (newPos < formatted.length && seen < targetSig) {
                        var fc = formatted.charAt(newPos);
                        if ((fc >= '0' && fc <= '9') || (allowDecimal && fc === '.')) {
                            seen++;
                        }
                        newPos++;
                    }
                }
                try {
                    input.setSelectionRange(newPos, newPos);
                } catch (err) {}
            }
        }
    }

    function formatAll(root) {
        var scope = root || document;
        scope.querySelectorAll('input.js-comma').forEach(function (input) {
            applyCommaToInput(input);
        });
    }

    function formatDisplayNumber(value) {
        if (value === null || value === undefined || value === '') return '0';
        var str = toAsciiDigits(String(value)).replace(/[,،٬\s]/g, '');
        var n = Number(str);
        if (!isFinite(n)) return String(value);
        var neg = str.charAt(0) === '-';
        var absStr = neg ? str.slice(1) : str;
        var parts = absStr.split('.');
        var intPart = (parts[0] || '0').replace(/^0+(?=\d)/, '');
        var fmtInt = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        var decPart = parts[1] !== undefined ? parts[1].replace(/0+$/, '') : '';
        var out = decPart ? fmtInt + '.' + decPart : fmtInt;
        return neg && out !== '0' ? '-' + out : out;
    }

    window.SyntelComma = {
        toAsciiDigits: toAsciiDigits,
        stripCommas: stripCommas,
        formatCommaString: formatCommaString,
        formatDisplayNumber: formatDisplayNumber,
        applyCommaToInput: applyCommaToInput,
        formatAll: formatAll
    };

    document.addEventListener('input', function (event) {
        var target = event.target;
        if (target && target.matches && target.matches('input.js-comma')) {
            applyCommaToInput(target);
        }
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { formatAll(document); });
    } else {
        formatAll(document);
    }
})();
