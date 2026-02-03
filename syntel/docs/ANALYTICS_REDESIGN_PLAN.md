# طرح بازطراحی صفحه Analytics با نمودارهای حبابی

## نمای کلی پروژه
بازطراحی کامل صفحه `/analytics/` با استفاده از نمودارهای حبابی تعاملی برای نمایش هزینه‌های بخش‌ها و دسته‌بندی‌ها

## ویژگی‌های کلیدی

### 1. ساختار Layout
- **منوی کناری (سمت چپ)**: فهرست گزارشات
- **ناحیه اصلی (سمت راست)**: نمایش گزارشات و نمودارها

### 2. گزارش اول: هزینه‌ها به تفکیک بخش‌ها

#### نمودار حبابی اول - بخش‌ها (Departments)
- **داده‌ها**: بخش‌های مختلف (همان درخواست‌کنندگان - Requesters)
- **اندازه حباب**: میزان هزینه هر بخش
- **رنگ حباب**: 
  - قرمز‌تر → رشد هزینه نسبت به دوره قبل
  - سبز‌تر → کاهش هزینه نسبت به دوره قبل
- **تعاملی**: با کلیک روی هر حباب، نمودار دوم نمایش داده می‌شود

#### نمودار حبابی دوم - دسته‌بندی‌ها (Categories)
- **داده‌ها**: دسته‌بندی‌های محصولات داخل بخش انتخابی
- **اندازه حباب**: میزان هزینه هر دسته‌بندی
- **متن داخل حباب**: مبلغ هزینه
- **رنگ حباب**: مشابه نمودار اول (رشد/کاهش)
- **تعاملی**: با کلیک روی هر حباب، لیست کالاها نمایش داده می‌شود

#### نمایش لیست کالاها
- جدول محصولات دسته‌بندی انتخابی
- شامل: نام محصول، مقدار، قیمت واحد، قیمت کل، تاریخ خرید

### 3. فیلترهای زمانی
هر دو نمودار باید این فیلترها را داشته باشند:
- **هفتگی**: مقایسه با هفته قبل
- **ماهانه**: مقایسه با ماه قبل
- **فصلی**: مقایسه با فصل قبل (3 ماه)
- **سالانه**: مقایسه با سال قبل

---

## فاز 1: طراحی و ساختار پایه

### تسک 1.1: ایجاد Template جدید
**فایل**: `purchases/templates/analytics_bubble.html`

**محتوا**:
```html
- Layout دو ستونی (Sidebar + Main Content)
- Sidebar: منوی گزارشات
- Main Content: ناحیه نمایش نمودارها
- Integration با Bootstrap و RTL
- Import Chart.js 3.x
```

**وابستگی‌ها**:
- Chart.js 3.x
- Bootstrap 4/5
- Font Awesome (برای آیکون‌ها)

### تسک 1.2: ایجاد API Endpoints
**فایل**: `purchases/views_analytics.py`

**Endpoints مورد نیاز**:

#### 1. `/api/analytics/departments/expenses/`
```python
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def department_expenses_api(request):
    """
    برگرداندن هزینه‌های بخش‌ها با مقایسه دوره قبل
    
    Parameters:
        - period: 'weekly', 'monthly', 'quarterly', 'yearly'
    
    Response:
        {
            "departments": [
                {
                    "id": 1,
                    "name": "انبار",
                    "current_expense": 15000000,
                    "previous_expense": 12000000,
                    "growth_rate": 25.0,  # درصد رشد
                    "color": "rgba(255, 99, 132, 0.8)"  # رنگ بر اساس رشد
                },
                ...
            ]
        }
    """
```

#### 2. `/api/analytics/department/<int:dept_id>/categories/`
```python
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def department_categories_api(request, dept_id):
    """
    برگرداندن هزینه‌های دسته‌بندی‌ها برای یک بخش خاص
    
    Parameters:
        - period: 'weekly', 'monthly', 'quarterly', 'yearly'
        - department_id: شناسه بخش
    
    Response:
        {
            "department": {
                "id": 1,
                "name": "انبار"
            },
            "categories": [
                {
                    "id": 5,
                    "name": "لوازم برقی",
                    "current_expense": 5000000,
                    "previous_expense": 4000000,
                    "growth_rate": 25.0,
                    "color": "rgba(255, 99, 132, 0.7)"
                },
                ...
            ]
        }
    """
```

#### 3. `/api/analytics/category/<int:cat_id>/products/`
```python
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def category_products_api(request, cat_id):
    """
    برگرداندن لیست محصولات یک دسته‌بندی
    
    Parameters:
        - period: 'weekly', 'monthly', 'quarterly', 'yearly'
        - category_id: شناسه دسته‌بندی
        - department_id: شناسه بخش (اختیاری)
    
    Response:
        {
            "category": {
                "id": 5,
                "name": "لوازم برقی"
            },
            "products": [
                {
                    "id": 100,
                    "name": "لامپ LED",
                    "quantity": 50,
                    "unit_price": 25000,
                    "total_price": 1250000,
                    "date": "2026-01-01"
                },
                ...
            ],
            "total_expense": 5000000
        }
    """
```

### تسک 1.3: ایجاد Serializers
**فایل**: `purchases/serializers_dashboard.py`

```python
class DepartmentExpenseSerializer:
    - id, name
    - current_expense, previous_expense
    - growth_rate
    - color (محاسبه شده)

class CategoryExpenseSerializer:
    - id, name
    - current_expense, previous_expense
    - growth_rate
    - color (محاسبه شده)

class ProductListResponseSerializer:
    - id, name, quantity
    - unit_price, total_price
    - date
```

---

## فاز 2: پیاده‌سازی نمودار حبابی بخش‌ها

### تسک 2.1: تابع محاسبه رنگ بر اساس رشد
**فایل**: `static/js/analytics_utils.js`

```javascript
/**
 * محاسبه رنگ بر اساس نرخ رشد
 * @param {number} growthRate - نرخ رشد به درصد
 * @returns {string} - رنگ به فرمت rgba
 */
function getColorByGrowth(growthRate) {
    // رشد مثبت (قرمز): 0% تا 100%+
    // رشد منفی (سبز): 0% تا -100%
    
    if (growthRate > 0) {
        // قرمز با شدت بیشتر
        const intensity = Math.min(growthRate / 100, 1);
        return `rgba(255, ${99 - intensity * 99}, ${132 - intensity * 132}, 0.8)`;
    } else if (growthRate < 0) {
        // سبز با شدت بیشتر
        const intensity = Math.min(Math.abs(growthRate) / 100, 1);
        return `rgba(${75 - intensity * 75}, 192, ${192 - intensity * 92}, 0.8)`;
    } else {
        // بدون تغییر (خاکستری)
        return 'rgba(201, 203, 207, 0.8)';
    }
}
```

### تسک 2.2: پیاده‌سازی نمودار حبابی Chart.js
**فایل**: `static/js/analytics_bubble.js`

```javascript
class BubbleChartManager {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.chart = null;
        this.currentPeriod = 'weekly';
    }

    /**
     * ایجاد نمودار حبابی بخش‌ها
     */
    createDepartmentChart(data) {
        const datasets = data.departments.map(dept => ({
            label: dept.name,
            data: [{
                x: dept.id,
                y: dept.growth_rate,
                r: this.calculateBubbleSize(dept.current_expense)
            }],
            backgroundColor: dept.color,
            borderColor: dept.color.replace('0.8', '1'),
            borderWidth: 2
        }));

        this.chart = new Chart(this.canvas, {
            type: 'bubble',
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onClick: (event, elements) => {
                    if (elements.length > 0) {
                        const deptIndex = elements[0].datasetIndex;
                        const deptId = data.departments[deptIndex].id;
                        this.onDepartmentClick(deptId);
                    }
                },
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: (context) => {
                                const dept = data.departments[context.datasetIndex];
                                return [
                                    `بخش: ${dept.name}`,
                                    `هزینه: ${dept.current_expense.toLocaleString('fa-IR')} ریال`,
                                    `رشد: ${dept.growth_rate.toFixed(1)}%`
                                ];
                            }
                        }
                    },
                    legend: {
                        display: true,
                        rtl: true,
                        labels: {
                            font: { family: 'IRANSans' }
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
                            font: { family: 'IRANSans' }
                        }
                    }
                }
            }
        });
    }

    /**
     * محاسبه اندازه حباب بر اساس هزینه
     */
    calculateBubbleSize(expense) {
        // تبدیل هزینه به اندازه مناسب (5 تا 50)
        const minSize = 5;
        const maxSize = 50;
        // فرض: حداقل هزینه 0، حداکثر 100 میلیون
        const normalizedExpense = expense / 100000000;
        return minSize + (normalizedExpense * (maxSize - minSize));
    }

    /**
     * رویداد کلیک روی بخش
     */
    onDepartmentClick(deptId) {
        // Load category chart
        console.log(`Department ${deptId} clicked`);
        // این تابع در فاز 3 پیاده‌سازی می‌شود
    }

    /**
     * تغییر دوره زمانی
     */
    changePeriod(period) {
        this.currentPeriod = period;
        this.refreshChart();
    }

    /**
     * بروزرسانی نمودار
     */
    refreshChart() {
        fetch(`/api/analytics/departments/expenses/?period=${this.currentPeriod}`)
            .then(response => response.json())
            .then(data => {
                if (this.chart) {
                    this.chart.destroy();
                }
                this.createDepartmentChart(data);
            });
    }
}
```

### تسک 2.3: پیاده‌سازی Backend محاسبات
**فایل**: `purchases/views_analytics.py`

```python
def calculate_growth_rate(current, previous):
    """محاسبه نرخ رشد به درصد"""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return ((current - previous) / previous) * 100

def get_color_by_growth(growth_rate):
    """تولید رنگ بر اساس نرخ رشد"""
    if growth_rate > 0:
        # قرمز برای رشد
        intensity = min(growth_rate / 100, 1.0)
        green = int(99 - intensity * 99)
        blue = int(132 - intensity * 132)
        return f'rgba(255, {green}, {blue}, 0.8)'
    elif growth_rate < 0:
        # سبز برای کاهش
        intensity = min(abs(growth_rate) / 100, 1.0)
        red = int(75 - intensity * 75)
        blue = int(192 - intensity * 92)
        return f'rgba({red}, 192, {blue}, 0.8)'
    else:
        # خاکستری برای بدون تغییر
        return 'rgba(201, 203, 207, 0.8)'
```

---

## فاز 3: نمودار حبابی دسته‌بندی‌ها و لیست محصولات

### تسک 3.1: پیاده‌سازی نمودار دسته‌بندی‌ها
**فایل**: `static/js/analytics_bubble.js` (ادامه)

```javascript
class BubbleChartManager {
    // ... کدهای قبلی ...

    /**
     * ایجاد نمودار حبابی دسته‌بندی‌ها
     */
    createCategoryChart(deptId) {
        fetch(`/api/analytics/department/${deptId}/categories/?period=${this.currentPeriod}`)
            .then(response => response.json())
            .then(data => {
                // ایجاد canvas جدید برای نمودار دوم
                const container = document.getElementById('category-chart-container');
                container.innerHTML = '<canvas id="categoryBubbleChart"></canvas>';
                
                const ctx = document.getElementById('categoryBubbleChart');
                const datasets = data.categories.map(cat => ({
                    label: cat.name,
                    data: [{
                        x: cat.id,
                        y: cat.growth_rate,
                        r: this.calculateBubbleSize(cat.current_expense)
                    }],
                    backgroundColor: cat.color,
                    borderColor: cat.color.replace('0.8', '1'),
                    borderWidth: 2
                }));

                new Chart(ctx, {
                    type: 'bubble',
                    data: { datasets },
                    options: {
                        // مشابه نمودار اول با تغییرات
                        onClick: (event, elements) => {
                            if (elements.length > 0) {
                                const catIndex = elements[0].datasetIndex;
                                const catId = data.categories[catIndex].id;
                                this.onCategoryClick(catId, deptId);
                            }
                        },
                        plugins: {
                            tooltip: {
                                callbacks: {
                                    label: (context) => {
                                        const cat = data.categories[context.datasetIndex];
                                        return [
                                            `دسته: ${cat.name}`,
                                            `هزینه: ${cat.current_expense.toLocaleString('fa-IR')} ریال`,
                                            `رشد: ${cat.growth_rate.toFixed(1)}%`
                                        ];
                                    }
                                }
                            }
                        }
                    }
                });
            });
    }

    /**
     * رویداد کلیک روی دسته‌بندی
     */
    onCategoryClick(catId, deptId) {
        this.loadProductList(catId, deptId);
    }
}
```

### تسک 3.2: نمایش لیست محصولات
**فایل**: `static/js/analytics_bubble.js` (ادامه)

```javascript
class BubbleChartManager {
    // ... کدهای قبلی ...

    /**
     * بارگذاری و نمایش لیست محصولات
     */
    loadProductList(catId, deptId) {
        fetch(`/api/analytics/category/${catId}/products/?period=${this.currentPeriod}&department_id=${deptId}`)
            .then(response => response.json())
            .then(data => {
                this.renderProductTable(data);
            });
    }

    /**
     * رندر جدول محصولات
     */
    renderProductTable(data) {
        const container = document.getElementById('product-list-container');
        
        let html = `
            <div class="card mt-3">
                <div class="card-header">
                    <h5>محصولات دسته‌بندی: ${data.category.name}</h5>
                    <p class="mb-0">مجموع هزینه: ${data.total_expense.toLocaleString('fa-IR')} ریال</p>
                </div>
                <div class="card-body">
                    <table class="table table-hover table-striped">
                        <thead>
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
                    <td>${product.unit_price.toLocaleString('fa-IR')}</td>
                    <td>${product.total_price.toLocaleString('fa-IR')}</td>
                    <td>${product.date}</td>
                </tr>
            `;
        });

        html += `
                        </tbody>
                    </table>
                </div>
            </div>
        `;

        container.innerHTML = html;
    }
}
```

---

## فاز 4: فیلترهای زمانی و بهینه‌سازی

### تسک 4.1: پیاده‌سازی فیلترهای زمانی
**فایل**: `static/js/analytics_bubble.js` (ادامه)

```javascript
// اضافه کردن event listeners برای فیلترها
document.addEventListener('DOMContentLoaded', function() {
    const periodButtons = document.querySelectorAll('.period-filter-btn');
    const chartManager = new BubbleChartManager('departmentBubbleChart');
    
    // بارگذاری اولیه
    chartManager.refreshChart();
    
    // رویدادهای فیلتر
    periodButtons.forEach(btn => {
        btn.addEventListener('click', function() {
            // Remove active class from all
            periodButtons.forEach(b => b.classList.remove('active'));
            // Add active to clicked
            this.classList.add('active');
            
            const period = this.dataset.period;
            chartManager.changePeriod(period);
        });
    });
});
```

**HTML فیلترها** (در Template):
```html
<div class="btn-group mb-3" role="group">
    <button type="button" class="btn btn-outline-primary period-filter-btn active" data-period="weekly">
        هفتگی
    </button>
    <button type="button" class="btn btn-outline-primary period-filter-btn" data-period="monthly">
        ماهانه
    </button>
    <button type="button" class="btn btn-outline-primary period-filter-btn" data-period="quarterly">
        فصلی
    </button>
    <button type="button" class="btn btn-outline-primary period-filter-btn" data-period="yearly">
        سالانه
    </button>
</div>
```

### تسک 4.2: بهینه‌سازی عملکرد

#### Caching
```python
# در views_analytics.py
from django.core.cache import cache
from django.views.decorators.cache import cache_page

@cache_page(60 * 5)  # 5 دقیقه cache
@api_view(['GET'])
def department_expenses_api(request):
    period = request.GET.get('period', 'weekly')
    cache_key = f'dept_expenses_{period}'
    
    data = cache.get(cache_key)
    if data is None:
        # محاسبه داده‌ها
        data = calculate_department_expenses(period)
        cache.set(cache_key, data, 60 * 5)
    
    return Response(data)
```

#### Database Optimization
```python
# استفاده از select_related و prefetch_related
def calculate_department_expenses(period):
    start, end, prev_start, prev_end = get_date_range(period)
    
    # بهینه‌سازی query
    requesters = Requester.objects.all()
    
    expenses = []
    for req in requesters:
        current = _invoice_items_for_period(
            start=start, end=end
        ).filter(
            invoice__purchase_request__requester=req
        ).aggregate(
            total=Coalesce(Sum('total_amount'), Value(0))
        )['total']
        
        previous = _invoice_items_for_period(
            start=prev_start, end=prev_end
        ).filter(
            invoice__purchase_request__requester=req
        ).aggregate(
            total=Coalesce(Sum('total_amount'), Value(0))
        )['total']
        
        # ... بقیه کد
```

### تسک 4.3: تست و رفع اشکال

#### تست API Endpoints
```python
# در purchases/tests/test_analytics_api.py
from django.test import TestCase, Client
from django.urls import reverse

class AnalyticsAPITests(TestCase):
    def setUp(self):
        # ایجاد داده‌های تست
        pass
    
    def test_department_expenses_weekly(self):
        response = self.client.get(
            '/api/analytics/departments/expenses/',
            {'period': 'weekly'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('departments', response.json())
    
    def test_category_expenses(self):
        # تست API دسته‌بندی‌ها
        pass
    
    def test_product_list(self):
        # تست API لیست محصولات
        pass
```

#### تست Frontend
```javascript
// در static/js/tests/analytics_tests.js
describe('BubbleChartManager', function() {
    it('should calculate bubble size correctly', function() {
        const manager = new BubbleChartManager('testCanvas');
        const size = manager.calculateBubbleSize(50000000);
        expect(size).toBeGreaterThan(5);
        expect(size).toBeLessThan(50);
    });
    
    it('should generate correct color for growth', function() {
        const color = getColorByGrowth(50);
        expect(color).toContain('rgba(255');
    });
});
```

---

## ساختار فایل‌های نهایی

```
syntel/
├── purchases/
│   ├── views_analytics.py
│   │   ├── department_expenses_api()
│   │   ├── department_categories_api()
│   │   ├── category_products_api()
│   │   ├── calculate_growth_rate()
│   │   └── get_color_by_growth()
│   │
│   ├── serializers_dashboard.py
│   │   ├── DepartmentExpenseSerializer
│   │   ├── CategoryExpenseSerializer
│   │   └── ProductListResponseSerializer
│   │
│   ├── urls.py
│   │   ├── path('analytics/', ...)
│   │   ├── path('api/analytics/departments/expenses/', ...)
│   │   ├── path('api/analytics/department/<int:dept_id>/categories/', ...)
│   │   └── path('api/analytics/category/<int:cat_id>/products/', ...)
│   │
│   └── templates/
│       └── analytics_bubble.html
│
└── frontend/
    └── static/
        └── js/
            ├── analytics_bubble.js
            │   └── class BubbleChartManager
            │
            ├── analytics_utils.js
            │   ├── getColorByGrowth()
            │   └── formatCurrency()
            │
            └── tests/
                └── analytics_tests.js
```

---

## نکات مهم پیاده‌سازی

### 1. مدیریت State
```javascript
const AppState = {
    currentPeriod: 'weekly',
    selectedDepartment: null,
    selectedCategory: null,
    charts: {
        department: null,
        category: null
    }
};
```

### 2. خطاهای احتمالی و راه‌حل‌ها

| خطا | راه‌حل |
|-----|--------|
| حباب‌ها روی هم می‌افتند | استفاده از الگوریتم force-directed layout |
| رنگ‌ها به درستی نمایش داده نمی‌شوند | بررسی مقادیر growth_rate و تست تابع getColorByGrowth |
| کندی در بارگذاری | پیاده‌سازی caching و pagination |
| مشکل RTL در Chart.js | استفاده از plugin RTL یا تنظیمات دستی |

### 3. بهینه‌سازی تجربه کاربری
- Loading spinners هنگام fetch داده‌ها
- پیام‌های خطای مناسب
- Tooltip های غنی با اطلاعات کامل
- Animations نرم برای transition بین نمودارها
- Responsive design برای موبایل

---

## زمان‌بندی پیشنهادی

| فاز | تخمین زمان | اولویت |
|-----|------------|---------|
| فاز 1 | 2-3 روز | بالا |
| فاز 2 | 2-3 روز | بالا |
| فاز 3 | 2-3 روز | متوسط |
| فاز 4 | 1-2 روز | متوسط |
| **کل** | **7-11 روز** | - |

---

## چک‌لیست نهایی

### Backend
- [ ] API endpoint بخش‌ها
- [ ] API endpoint دسته‌بندی‌ها
- [ ] API endpoint محصولات
- [ ] محاسبه growth rate
- [ ] تولید رنگ بر اساس رشد
- [ ] فیلترهای زمانی
- [ ] Caching
- [ ] تست‌های واحد

### Frontend
- [ ] Template اصلی با layout دو ستونی
- [ ] نمودار حبابی بخش‌ها
- [ ] نمودار حبابی دسته‌بندی‌ها
- [ ] جدول محصولات
- [ ] فیلترهای زمانی UI
- [ ] تعامل کلیک روی حباب‌ها
- [ ] Tooltips
- [ ] Responsive design
- [ ] RTL support
- [ ] تست‌های frontend

### دیگر
- [ ] مستندسازی API
- [ ] راهنمای کاربر
- [ ] تست یکپارچگی
- [ ] بهینه‌سازی عملکرد
- [ ] Deploy و اعتبارسنجی

---

## منابع و مراجع

- Chart.js Documentation: https://www.chartjs.org/docs/latest/
- Chart.js Bubble Charts: https://www.chartjs.org/docs/latest/charts/bubble.html
- Django REST Framework: https://www.django-rest-framework.org/
- Bootstrap RTL: https://github.com/morteza/bootstrap-rtl

---

**نویسنده**: GitHub Copilot  
**تاریخ ایجاد**: 2026-01-03  
**نسخه**: 1.0
