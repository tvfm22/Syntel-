# Quick Start Guide - Analytics Redesign
## شروع سریع پیاده‌سازی

این راهنما برای شروع فوری کار بر روی پروژه Analytics است.

---

## 🚀 مرحله 1: نصب Dependencies (5 دقیقه)

### 1. بررسی موارد نصب شده
```bash
# بررسی Django و DRF
python manage.py --version
```

### 2. اگر نیاز به نصب بود
```bash
pip install djangorestframework
pip install django-cors-headers
```

---

## 📝 مرحله 2: ایجاد فایل‌های پایه (15 دقیقه)

### A. Template اصلی

**فایل**: `d:\Syntel\syntel\purchases\templates\analytics_bubble.html`

```html
{% extends 'base.html' %}
{% load static %}

{% block title %}داشبورد تحلیلی - نمودارهای حبابی{% endblock %}

{% block extra_head %}
<script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
<style>
    .analytics-container {
        display: flex;
        height: calc(100vh - 100px);
    }
    
    .sidebar-menu {
        width: 250px;
        background: #f8f9fa;
        padding: 20px;
        border-left: 2px solid #dee2e6;
    }
    
    .sidebar-menu h5 {
        color: #495057;
        margin-bottom: 15px;
        font-weight: bold;
    }
    
    .menu-item {
        padding: 10px 15px;
        margin-bottom: 8px;
        background: white;
        border: 1px solid #dee2e6;
        border-radius: 5px;
        cursor: pointer;
        transition: all 0.3s;
    }
    
    .menu-item:hover {
        background: #007bff;
        color: white;
        transform: translateX(-5px);
    }
    
    .menu-item.active {
        background: #007bff;
        color: white;
        border-color: #007bff;
    }
    
    .main-content {
        flex: 1;
        padding: 20px;
        overflow-y: auto;
    }
    
    .chart-container {
        background: white;
        border-radius: 8px;
        padding: 20px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        margin-bottom: 20px;
    }
    
    .chart-canvas {
        height: 500px !important;
    }
    
    .period-filters {
        margin-bottom: 20px;
    }
    
    .loading {
        text-align: center;
        padding: 40px;
        color: #6c757d;
    }
    
    .loading .spinner-border {
        width: 3rem;
        height: 3rem;
    }
</style>
{% endblock %}

{% block content %}
<div class="analytics-container">
    <!-- Sidebar Menu -->
    <div class="sidebar-menu">
        <h5><i class="fas fa-chart-bar"></i> گزارشات</h5>
        <div class="menu-item active" data-report="expenses-by-department">
            <i class="fas fa-building"></i>
            هزینه‌ها به تفکیک بخش‌ها
        </div>
        <div class="menu-item" data-report="category-analysis">
            <i class="fas fa-tags"></i>
            تحلیل دسته‌بندی‌ها
        </div>
        <div class="menu-item" data-report="product-trends">
            <i class="fas fa-box"></i>
            روند محصولات
        </div>
        <div class="menu-item" data-report="supplier-performance">
            <i class="fas fa-truck"></i>
            عملکرد تأمین‌کنندگان
        </div>
    </div>

    <!-- Main Content Area -->
    <div class="main-content">
        <!-- Period Filters -->
        <div class="period-filters">
            <div class="btn-group" role="group">
                <button type="button" class="btn btn-outline-primary period-btn active" data-period="weekly">
                    <i class="fas fa-calendar-week"></i> هفتگی
                </button>
                <button type="button" class="btn btn-outline-primary period-btn" data-period="monthly">
                    <i class="fas fa-calendar-alt"></i> ماهانه
                </button>
                <button type="button" class="btn btn-outline-primary period-btn" data-period="quarterly">
                    <i class="fas fa-calendar"></i> فصلی
                </button>
                <button type="button" class="btn btn-outline-primary period-btn" data-period="yearly">
                    <i class="fas fa-calendar-check"></i> سالانه
                </button>
            </div>
        </div>

        <!-- Breadcrumb Navigation -->
        <nav aria-label="breadcrumb">
            <ol class="breadcrumb" id="breadcrumb">
                <li class="breadcrumb-item active">نمودار بخش‌ها</li>
            </ol>
        </nav>

        <!-- Department Bubble Chart -->
        <div class="chart-container" id="department-chart-container">
            <h4>نمودار حبابی هزینه‌های بخش‌ها</h4>
            <p class="text-muted">اندازه حباب = میزان هزینه | رنگ = رشد (قرمز) یا کاهش (سبز)</p>
            <div id="department-loading" class="loading">
                <div class="spinner-border text-primary" role="status">
                    <span class="sr-only">در حال بارگذاری...</span>
                </div>
            </div>
            <canvas id="departmentBubbleChart" class="chart-canvas" style="display:none;"></canvas>
        </div>

        <!-- Category Bubble Chart (Hidden Initially) -->
        <div class="chart-container" id="category-chart-container" style="display:none;">
            <h4>نمودار حبابی دسته‌بندی‌ها</h4>
            <button class="btn btn-sm btn-secondary mb-3" id="back-to-departments">
                <i class="fas fa-arrow-right"></i> بازگشت به بخش‌ها
            </button>
            <canvas id="categoryBubbleChart" class="chart-canvas"></canvas>
        </div>

        <!-- Product List (Hidden Initially) -->
        <div class="chart-container" id="product-list-container" style="display:none;">
        </div>
    </div>
</div>

<script src="{% static 'js/analytics_utils.js' %}"></script>
<script src="{% static 'js/analytics_bubble.js' %}"></script>
<script>
    // Initialize on page load
    document.addEventListener('DOMContentLoaded', function() {
        const app = new AnalyticsApp();
        app.init();
    });
</script>
{% endblock %}
```

---

### B. JavaScript Utilities

**فایل**: `d:\Syntel\syntel\frontend\static\js\analytics_utils.js`

```javascript
/**
 * Analytics Utilities
 * توابع کمکی برای محاسبات و نمایش
 */

/**
 * محاسبه رنگ بر اساس نرخ رشد
 */
function getColorByGrowth(growthRate) {
    if (growthRate > 0) {
        // قرمز برای رشد
        const intensity = Math.min(growthRate / 100, 1);
        const green = Math.floor(99 - intensity * 99);
        const blue = Math.floor(132 - intensity * 132);
        return `rgba(255, ${green}, ${blue}, 0.8)`;
    } else if (growthRate < 0) {
        // سبز برای کاهش
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
 */
function calculateBubbleSize(amount, minAmount = 0, maxAmount = 100000000) {
    const minSize = 10;
    const maxSize = 60;
    
    if (maxAmount === minAmount) return (minSize + maxSize) / 2;
    
    const normalized = (amount - minAmount) / (maxAmount - minAmount);
    return minSize + (normalized * (maxSize - minSize));
}

/**
 * فرمت کردن مبلغ ریالی
 */
function formatCurrency(amount) {
    return new Intl.NumberFormat('fa-IR').format(amount) + ' ریال';
}

/**
 * فرمت کردن درصد
 */
function formatPercent(value) {
    return new Intl.NumberFormat('fa-IR', {
        style: 'percent',
        minimumFractionDigits: 1,
        maximumFractionDigits: 1
    }).format(value / 100);
}

/**
 * نمایش پیام خطا
 */
function showError(message) {
    alert(message); // می‌توان با toast یا modal جایگزین کرد
}

/**
 * نمایش loading
 */
function showLoading(elementId) {
    const el = document.getElementById(elementId);
    if (el) el.style.display = 'block';
}

/**
 * مخفی کردن loading
 */
function hideLoading(elementId) {
    const el = document.getElementById(elementId);
    if (el) el.style.display = 'none';
}
```

---

### C. Main Application Logic

**فایل**: `d:\Syntel\syntel\frontend\static\js\analytics_bubble.js`

```javascript
/**
 * Analytics Application
 * مدیریت اصلی برنامه Analytics
 */

class AnalyticsApp {
    constructor() {
        this.currentPeriod = 'weekly';
        this.selectedDepartment = null;
        this.departmentChart = null;
        this.categoryChart = null;
        
        // Bind methods
        this.init = this.init.bind(this);
        this.loadDepartmentChart = this.loadDepartmentChart.bind(this);
        this.loadCategoryChart = this.loadCategoryChart.bind(this);
        this.loadProductList = this.loadProductList.bind(this);
    }

    /**
     * Initialize application
     */
    init() {
        this.setupEventListeners();
        this.loadDepartmentChart();
    }

    /**
     * Setup all event listeners
     */
    setupEventListeners() {
        // Period filter buttons
        document.querySelectorAll('.period-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                this.currentPeriod = e.target.dataset.period;
                this.loadDepartmentChart();
            });
        });

        // Back button
        const backBtn = document.getElementById('back-to-departments');
        if (backBtn) {
            backBtn.addEventListener('click', () => {
                this.showDepartmentView();
            });
        }

        // Menu items
        document.querySelectorAll('.menu-item').forEach(item => {
            item.addEventListener('click', (e) => {
                document.querySelectorAll('.menu-item').forEach(i => i.classList.remove('active'));
                e.currentTarget.classList.add('active');
                
                const report = e.currentTarget.dataset.report;
                if (report === 'expenses-by-department') {
                    this.loadDepartmentChart();
                }
                // سایر گزارشات در آینده پیاده‌سازی می‌شوند
            });
        });
    }

    /**
     * Load department bubble chart
     */
    async loadDepartmentChart() {
        showLoading('department-loading');
        const canvas = document.getElementById('departmentBubbleChart');
        canvas.style.display = 'none';

        try {
            const response = await fetch(`/api/analytics/departments/expenses/?period=${this.currentPeriod}`);
            const data = await response.json();

            hideLoading('department-loading');
            canvas.style.display = 'block';

            // Destroy old chart
            if (this.departmentChart) {
                this.departmentChart.destroy();
            }

            // Create new chart
            this.departmentChart = this.createBubbleChart(
                'departmentBubbleChart',
                data.departments,
                'بخش',
                (deptId) => this.loadCategoryChart(deptId)
            );

        } catch (error) {
            hideLoading('department-loading');
            showError('خطا در بارگذاری داده‌ها: ' + error.message);
        }
    }

    /**
     * Load category bubble chart
     */
    async loadCategoryChart(deptId) {
        this.selectedDepartment = deptId;
        
        const container = document.getElementById('category-chart-container');
        container.style.display = 'block';
        container.innerHTML = '<div class="loading"><div class="spinner-border text-primary"></div></div>';

        try {
            const response = await fetch(`/api/analytics/department/${deptId}/categories/?period=${this.currentPeriod}`);
            const data = await response.json();

            container.innerHTML = `
                <h4>دسته‌بندی‌های بخش: ${data.department.name}</h4>
                <button class="btn btn-sm btn-secondary mb-3" onclick="app.showDepartmentView()">
                    <i class="fas fa-arrow-right"></i> بازگشت به بخش‌ها
                </button>
                <canvas id="categoryBubbleChart" class="chart-canvas"></canvas>
            `;

            // Create category chart
            this.categoryChart = this.createBubbleChart(
                'categoryBubbleChart',
                data.categories,
                'دسته‌بندی',
                (catId) => this.loadProductList(catId, deptId)
            );

            // Update breadcrumb
            document.getElementById('breadcrumb').innerHTML = `
                <li class="breadcrumb-item"><a href="#" onclick="app.showDepartmentView()">نمودار بخش‌ها</a></li>
                <li class="breadcrumb-item active">دسته‌بندی‌های ${data.department.name}</li>
            `;

        } catch (error) {
            showError('خطا در بارگذاری دسته‌بندی‌ها: ' + error.message);
        }
    }

    /**
     * Load product list
     */
    async loadProductList(catId, deptId) {
        const container = document.getElementById('product-list-container');
        container.style.display = 'block';
        container.innerHTML = '<div class="loading"><div class="spinner-border text-primary"></div></div>';

        try {
            const response = await fetch(`/api/analytics/category/${catId}/products/?period=${this.currentPeriod}&department_id=${deptId}`);
            const data = await response.json();

            let html = `
                <h4>محصولات دسته‌بندی: ${data.category.name}</h4>
                <p class="text-info"><strong>مجموع هزینه:</strong> ${formatCurrency(data.total_expense)}</p>
                <table class="table table-striped table-hover">
                    <thead class="thead-dark">
                        <tr>
                            <th>ردیف</th>
                            <th>نام محصول</th>
                            <th>مقدار</th>
                            <th>قیمت واحد</th>
                            <th>قیمت کل</th>
                            <th>تاریخ</th>
                        </tr>
                    </thead>
                    <tbody>
            `;

            data.products.forEach((product, index) => {
                html += `
                    <tr>
                        <td>${index + 1}</td>
                        <td>${product.name}</td>
                        <td>${product.quantity}</td>
                        <td>${formatCurrency(product.unit_price)}</td>
                        <td>${formatCurrency(product.total_price)}</td>
                        <td>${product.date}</td>
                    </tr>
                `;
            });

            html += `
                    </tbody>
                </table>
            `;

            container.innerHTML = html;

        } catch (error) {
            showError('خطا در بارگذاری محصولات: ' + error.message);
        }
    }

    /**
     * Create a bubble chart
     */
    createBubbleChart(canvasId, items, label, clickHandler) {
        const ctx = document.getElementById(canvasId).getContext('2d');

        // Calculate min/max for bubble sizing
        const amounts = items.map(item => item.current_expense);
        const minAmount = Math.min(...amounts);
        const maxAmount = Math.max(...amounts);

        const datasets = items.map(item => ({
            label: item.name,
            data: [{
                x: item.id,
                y: item.growth_rate,
                r: calculateBubbleSize(item.current_expense, minAmount, maxAmount)
            }],
            backgroundColor: item.color,
            borderColor: item.color.replace('0.8', '1'),
            borderWidth: 2,
            hoverBackgroundColor: item.color.replace('0.8', '0.9'),
        }));

        return new Chart(ctx, {
            type: 'bubble',
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onClick: (event, elements) => {
                    if (elements.length > 0) {
                        const index = elements[0].datasetIndex;
                        const itemId = items[index].id;
                        clickHandler(itemId);
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'right',
                        rtl: true,
                        labels: {
                            font: { family: 'Tahoma', size: 12 }
                        }
                    },
                    tooltip: {
                        rtl: true,
                        callbacks: {
                            label: (context) => {
                                const item = items[context.datasetIndex];
                                return [
                                    `${label}: ${item.name}`,
                                    `هزینه: ${formatCurrency(item.current_expense)}`,
                                    `رشد: ${formatPercent(item.growth_rate)}`
                                ];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        display: false
                    },
                    y: {
                        title: {
                            display: true,
                            text: 'نرخ رشد (%)',
                            font: { family: 'Tahoma', size: 14 }
                        },
                        ticks: {
                            callback: (value) => value + '%'
                        }
                    }
                }
            }
        });
    }

    /**
     * Show department view
     */
    showDepartmentView() {
        document.getElementById('department-chart-container').style.display = 'block';
        document.getElementById('category-chart-container').style.display = 'none';
        document.getElementById('product-list-container').style.display = 'none';
        
        document.getElementById('breadcrumb').innerHTML = `
            <li class="breadcrumb-item active">نمودار بخش‌ها</li>
        `;
        
        this.selectedDepartment = null;
    }
}

// Global instance
let app;
document.addEventListener('DOMContentLoaded', function() {
    app = new AnalyticsApp();
    app.init();
});
```

---

## 🔧 مرحله 3: Backend API (30 دقیقه)

### بروزرسانی `views_analytics.py`

**در انتهای فایل `d:\Syntel\syntel\purchases\views_analytics.py` اضافه کنید**:

```python
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def department_expenses_api(request):
    """
    API برگرداندن هزینه‌های بخش‌ها
    """
    period = request.GET.get('period', 'weekly')
    start, end, prev_start, prev_end = get_date_range(period)
    
    requesters = Requester.objects.all()
    departments = []
    
    for req in requesters:
        # هزینه دوره فعلی
        current = _invoice_items_for_period(
            start=start, end=end
        ).filter(
            invoice__purchase_request__requester=req
        ).aggregate(
            total=Coalesce(Sum('total_amount'), Value(0, output_field=DecimalField()))
        )['total']
        
        # هزینه دوره قبل
        previous = _invoice_items_for_period(
            start=prev_start, end=prev_end
        ).filter(
            invoice__purchase_request__requester=req
        ).aggregate(
            total=Coalesce(Sum('total_amount'), Value(0, output_field=DecimalField()))
        )['total']
        
        # محاسبه رشد
        growth_rate = calculate_growth_rate(float(current), float(previous))
        color = get_color_by_growth(growth_rate)
        
        departments.append({
            'id': req.id,
            'name': req.name,
            'current_expense': float(current),
            'previous_expense': float(previous),
            'growth_rate': growth_rate,
            'color': color
        })
    
    return Response({'departments': departments})


def calculate_growth_rate(current, previous):
    """محاسبه نرخ رشد"""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return ((current - previous) / previous) * 100


def get_color_by_growth(growth_rate):
    """تولید رنگ بر اساس رشد"""
    if growth_rate > 0:
        intensity = min(growth_rate / 100, 1.0)
        green = int(99 - intensity * 99)
        blue = int(132 - intensity * 132)
        return f'rgba(255, {green}, {blue}, 0.8)'
    elif growth_rate < 0:
        intensity = min(abs(growth_rate) / 100, 1.0)
        red = int(75 - intensity * 75)
        blue = int(192 - intensity * 92)
        return f'rgba({red}, 192, {blue}, 0.8)'
    else:
        return 'rgba(201, 203, 207, 0.8)'
```

### بروزرسانی `urls.py`

**به فایل `d:\Syntel\syntel\purchases\urls.py` اضافه کنید**:

```python
# در قسمت import ها
from . import views_analytics

# در urlpatterns
path('api/analytics/departments/expenses/', views_analytics.department_expenses_api, name='api_department_expenses'),
```

---

## ✅ مرحله 4: تست (10 دقیقه)

### 1. اجرای سرور
```bash
cd d:\Syntel\syntel
python manage.py runserver
```

### 2. باز کردن مرورگر
```
http://localhost:8000/analytics/
```

### 3. چک کردن API به صورت مستقیم
```
http://localhost:8000/api/analytics/departments/expenses/?period=weekly
```

**خروجی مورد انتظار**:
```json
{
  "departments": [
    {
      "id": 1,
      "name": "انبار",
      "current_expense": 15000000,
      "previous_expense": 12000000,
      "growth_rate": 25.0,
      "color": "rgba(255, 74, 107, 0.8)"
    }
  ]
}
```

---

## 🐛 عیب‌یابی سریع

### مشکل: صفحه سفید
- بررسی Console مرورگر (F12)
- بررسی لاگ Django

### مشکل: Chart نمایش داده نمی‌شود
- بررسی Chart.js لود شده باشد
- بررسی Canvas موجود باشد

### مشکل: API خطا می‌دهد
- بررسی URL در `urls.py`
- بررسی import ها
- بررسی permissions

---

## 📊 مراحل بعدی

پس از تست موفقیت‌آمیز:

1. ✅ پیاده‌سازی API دسته‌بندی‌ها (تسک 1.4)
2. ✅ پیاده‌سازی API محصولات (تسک 1.5)
3. ✅ تکمیل توابع frontend

برای ادامه، به فایل [ANALYTICS_TASKS.md](./ANALYTICS_TASKS.md) مراجعه کنید.

---

**موفق باشید! 🚀**
