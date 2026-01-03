/**
 * Analytics Utilities
 * توابع کمکی برای محاسبات و نمایش
 * Version: 1.0
 * Date: 2026-01-03
 */

/**
 * محاسبه رنگ بر اساس نرخ رشد
 * @param {number} growthRate - نرخ رشد به درصد
 * @returns {string} - رنگ به فرمت rgba
 */
function getColorByGrowth(growthRate) {
    if (growthRate > 0) {
        // قرمز برای رشد - هرچه رشد بیشتر، قرمزتر
        const intensity = Math.min(growthRate / 100, 1);
        const green = Math.floor(99 - intensity * 99);
        const blue = Math.floor(132 - intensity * 132);
        return `rgba(255, ${green}, ${blue}, 0.8)`;
    } else if (growthRate < 0) {
        // سبز برای کاهش - هرچه کاهش بیشتر، سبزتر
        const intensity = Math.min(Math.abs(growthRate) / 100, 1);
        const red = Math.floor(75 - intensity * 75);
        const blue = Math.floor(192 - intensity * 92);
        return `rgba(${red}, 192, ${blue}, 0.8)`;
    } else {
        // خاکستری برای بدون تغییر
        return 'rgba(201, 203, 207, 0.8)';
    }
}

/**
 * محاسبه اندازه حباب بر اساس مبلغ
 * @param {number} amount - مبلغ
 * @param {number} minAmount - کمترین مبلغ در مجموعه داده
 * @param {number} maxAmount - بیشترین مبلغ در مجموعه داده
 * @returns {number} - اندازه حباب (10-60)
 */
function calculateBubbleSize(amount, minAmount = 0, maxAmount = 100000000) {
    const minSize = 10;
    const maxSize = 60;
    
    // اگر همه مقادیر برابر باشند
    if (maxAmount === minAmount) {
        return (minSize + maxSize) / 2;
    }
    
    // نرمال‌سازی بین 0 و 1
    const normalized = (amount - minAmount) / (maxAmount - minAmount);
    
    // تبدیل به اندازه حباب
    return minSize + (normalized * (maxSize - minSize));
}

/**
 * فرمت کردن مبلغ ریالی
 * @param {number} amount - مبلغ
 * @returns {string} - مبلغ فرمت شده
 */
function formatCurrency(amount) {
    if (amount === null || amount === undefined) {
        return '0 ریال';
    }
    
    // تبدیل به عدد اگر string است
    const numAmount = typeof amount === 'string' ? parseFloat(amount) : amount;
    
    // فرمت فارسی
    return new Intl.NumberFormat('fa-IR').format(Math.round(numAmount)) + ' ریال';
}

/**
 * فرمت کردن درصد
 * @param {number} value - مقدار درصد
 * @returns {string} - درصد فرمت شده
 */
function formatPercent(value) {
    if (value === null || value === undefined) {
        return '0%';
    }
    
    const numValue = typeof value === 'string' ? parseFloat(value) : value;
    
    return new Intl.NumberFormat('fa-IR', {
        style: 'percent',
        minimumFractionDigits: 1,
        maximumFractionDigits: 1
    }).format(numValue / 100);
}

/**
 * فرمت کردن عدد ساده
 * @param {number} value - عدد
 * @returns {string} - عدد فرمت شده
 */
function formatNumber(value) {
    if (value === null || value === undefined) {
        return '0';
    }
    
    const numValue = typeof value === 'string' ? parseFloat(value) : value;
    return new Intl.NumberFormat('fa-IR').format(numValue);
}

/**
 * نمایش پیام خطا
 * @param {string} message - پیام خطا
 */
function showError(message) {
    // استفاده از SweetAlert اگر موجود باشد
    if (typeof Swal !== 'undefined') {
        Swal.fire({
            icon: 'error',
            title: 'خطا',
            text: message,
            confirmButtonText: 'متوجه شدم'
        });
    } else {
        // fallback به alert
        alert('خطا: ' + message);
    }
    console.error('Analytics Error:', message);
}

/**
 * نمایش پیام موفقیت
 * @param {string} message - پیام موفقیت
 */
function showSuccess(message) {
    if (typeof Swal !== 'undefined') {
        Swal.fire({
            icon: 'success',
            title: 'موفق',
            text: message,
            timer: 2000,
            showConfirmButton: false
        });
    } else {
        console.log('Success:', message);
    }
}

/**
 * نمایش loading
 * @param {string} elementId - ID المنت
 */
function showLoading(elementId) {
    const el = document.getElementById(elementId);
    if (el) {
        el.style.display = 'block';
    }
}

/**
 * مخفی کردن loading
 * @param {string} elementId - ID المنت
 */
function hideLoading(elementId) {
    const el = document.getElementById(elementId);
    if (el) {
        el.style.display = 'none';
    }
}

/**
 * دریافت CSRF Token برای Django
 * @returns {string} - CSRF token
 */
function getCsrfToken() {
    const cookieValue = document.cookie
        .split('; ')
        .find(row => row.startsWith('csrftoken='));
    
    return cookieValue ? cookieValue.split('=')[1] : '';
}

/**
 * تبدیل تاریخ میلادی به شمسی
 * @param {string} dateString - تاریخ میلادی
 * @returns {string} - تاریخ شمسی
 */
function toPersianDate(dateString) {
    try {
        const date = new Date(dateString);
        return date.toLocaleDateString('fa-IR', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit'
        });
    } catch (e) {
        return dateString;
    }
}

/**
 * محاسبه میانگین آرایه
 * @param {Array<number>} arr - آرایه اعداد
 * @returns {number} - میانگین
 */
function calculateAverage(arr) {
    if (!arr || arr.length === 0) return 0;
    const sum = arr.reduce((a, b) => a + b, 0);
    return sum / arr.length;
}

/**
 * محاسبه مجموع آرایه
 * @param {Array<number>} arr - آرایه اعداد
 * @returns {number} - مجموع
 */
function calculateSum(arr) {
    if (!arr || arr.length === 0) return 0;
    return arr.reduce((a, b) => a + b, 0);
}

/**
 * مرتب‌سازی آرایه بر اساس یک property
 * @param {Array} arr - آرایه
 * @param {string} property - نام property
 * @param {boolean} descending - نزولی یا صعودی
 * @returns {Array} - آرایه مرتب شده
 */
function sortBy(arr, property, descending = true) {
    return [...arr].sort((a, b) => {
        const valA = a[property];
        const valB = b[property];
        
        if (descending) {
            return valB - valA;
        } else {
            return valA - valB;
        }
    });
}

/**
 * debounce function برای بهینه‌سازی event handlers
 * @param {Function} func - تابع اصلی
 * @param {number} wait - مدت زمان انتظار (میلی‌ثانیه)
 * @returns {Function} - تابع debounced
 */
function debounce(func, wait = 300) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * بررسی خالی بودن آبجکت
 * @param {Object} obj - آبجکت
 * @returns {boolean} - true اگر خالی باشد
 */
function isEmpty(obj) {
    return obj === null || obj === undefined || 
           (typeof obj === 'object' && Object.keys(obj).length === 0) ||
           (typeof obj === 'string' && obj.trim() === '');
}

/**
 * کپی عمیق یک آبجکت
 * @param {Object} obj - آبجکت اصلی
 * @returns {Object} - کپی عمیق
 */
function deepCopy(obj) {
    return JSON.parse(JSON.stringify(obj));
}

// Export functions اگر از module استفاده می‌شود
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        getColorByGrowth,
        calculateBubbleSize,
        formatCurrency,
        formatPercent,
        formatNumber,
        showError,
        showSuccess,
        showLoading,
        hideLoading,
        getCsrfToken,
        toPersianDate,
        calculateAverage,
        calculateSum,
        sortBy,
        debounce,
        isEmpty,
        deepCopy
    };
}
