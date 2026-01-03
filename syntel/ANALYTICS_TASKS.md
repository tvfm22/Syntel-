# تسک‌های پیاده‌سازی سیستم Analytics حبابی

## 📋 فاز 1: طراحی و ساختار پایه (2-3 روز)

### ✅ تسک 1.1: ایجاد Template و Layout
**مسئولیت**: Frontend Developer  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 4 ساعت

**شرح**:
- ایجاد فایل `purchases/templates/analytics_bubble.html`
- طراحی layout دو ستونی:
  - Sidebar (چپ): منوی گزارشات
  - Main content (راست): ناحیه نمودارها
- Integration با Bootstrap و RTL
- Import کتابخانه‌های مورد نیاز (Chart.js 3.x)

**Deliverables**:
- [x] فایل Template
- [x] CSS کاستوم برای RTL
- [x] ساختار HTML کامل

**Dependencies**: هیچ

---

### ✅ تسک 1.2: ایجاد View و URL Pattern
**مسئولیت**: Backend Developer  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 2 ساعت

**شرح**:
- بروزرسانی `purchases/views.py` - تابع `analytics_dashboard`
- افزودن route در `purchases/urls.py`
- اطمینان از authentication و permissions

**کد**:
```python
@custom_login_required
@role_required(["purchasing", "manager", "finance_manager"])
def analytics_dashboard(request):
    return render(request, "analytics_bubble.html")
```

**Deliverables**:
- [x] View function
- [x] URL pattern
- [x] Permission checks

**Dependencies**: تسک 1.1

---

### ✅ تسک 1.3: ایجاد API Endpoint - هزینه بخش‌ها
**مسئولیت**: Backend Developer  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 6 ساعت

**شرح**:
- ایجاد endpoint `/api/analytics/departments/expenses/`
- پیاده‌سازی محاسبه هزینه فعلی و قبلی
- محاسبه نرخ رشد (growth rate)
- تولید رنگ بر اساس رشد

**Input Parameters**:
- `period`: weekly | monthly | quarterly | yearly

**Output Format**:
```json
{
  "departments": [
    {
      "id": 1,
      "name": "انبار",
      "current_expense": 15000000,
      "previous_expense": 12000000,
      "growth_rate": 25.0,
      "color": "rgba(255, 99, 132, 0.8)"
    }
  ]
}
```

**Deliverables**:
- [x] API endpoint
- [x] توابع helper (calculate_growth_rate, get_color_by_growth)
- [x] Query optimization

**Dependencies**: هیچ

---

### ✅ تسک 1.4: ایجاد API Endpoint - دسته‌بندی‌های بخش
**مسئولیت**: Backend Developer  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 4 ساعت

**شرح**:
- ایجاد endpoint `/api/analytics/department/<int:dept_id>/categories/`
- برگرداندن دسته‌بندی‌های یک بخش خاص با هزینه و رشد

**Input Parameters**:
- `dept_id`: شناسه بخش
- `period`: weekly | monthly | quarterly | yearly

**Output Format**:
```json
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
    }
  ]
}
```

**Deliverables**:
- [x] API endpoint
- [x] محاسبات هزینه به تفکیک دسته‌بندی

**Dependencies**: تسک 1.3

---

### ✅ تسک 1.5: ایجاد API Endpoint - محصولات دسته‌بندی
**مسئولیت**: Backend Developer  
**اولویت**: 🟡 متوسط  
**زمان تخمینی**: 3 ساعت

**شرح**:
- ایجاد endpoint `/api/analytics/category/<int:cat_id>/products/`
- برگرداندن لیست محصولات یک دسته‌بندی

**Input Parameters**:
- `cat_id`: شناسه دسته‌بندی
- `period`: weekly | monthly | quarterly | yearly
- `department_id`: (اختیاری) فیلتر بر اساس بخش

**Output Format**:
```json
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
    }
  ],
  "total_expense": 5000000
}
```

**Deliverables**:
- [x] API endpoint
- [x] Query optimization با select_related

**Dependencies**: تسک 1.4

---

### ✅ تسک 1.6: ایجاد Serializers
**مسئولیت**: Backend Developer  
**اولویت**: 🟡 متوسط  
**زمان تخمینی**: 2 ساعت

**شرح**:
- بروزرسانی `purchases/serializers_dashboard.py`
- ایجاد سه serializer:
  - `DepartmentExpenseSerializer`
  - `CategoryExpenseSerializer`
  - `ProductListResponseSerializer`

**Deliverables**:
- [x] Serializer classes
- [x] مستندسازی فیلدها

**Dependencies**: تسک‌های 1.3, 1.4, 1.5

---

## 📊 فاز 2: پیاده‌سازی نمودار حبابی بخش‌ها (2-3 روز)

### ✅ تسک 2.1: ایجاد JavaScript Utilities
**مسئولیت**: Frontend Developer  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 3 ساعت

**شرح**:
- ایجاد فایل `frontend/static/js/analytics_utils.js`
- پیاده‌سازی توابع کمکی:
  - `getColorByGrowth(growthRate)`: محاسبه رنگ
  - `formatCurrency(amount)`: فرمت ریال
  - `calculateBubbleSize(expense)`: محاسبه اندازه حباب

**کد نمونه**:
```javascript
function getColorByGrowth(growthRate) {
    if (growthRate > 0) {
        const intensity = Math.min(growthRate / 100, 1);
        return `rgba(255, ${99 - intensity * 99}, ${132 - intensity * 132}, 0.8)`;
    } else if (growthRate < 0) {
        const intensity = Math.min(Math.abs(growthRate) / 100, 1);
        return `rgba(${75 - intensity * 75}, 192, ${192 - intensity * 92}, 0.8)`;
    } else {
        return 'rgba(201, 203, 207, 0.8)';
    }
}
```

**Deliverables**:
- [x] فایل analytics_utils.js
- [x] توابع تست شده

**Dependencies**: هیچ

---

### ✅ تسک 2.2: پیاده‌سازی BubbleChartManager Class
**مسئولیت**: Frontend Developer  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 6 ساعت

**شرح**:
- ایجاد فایل `frontend/static/js/analytics_bubble.js`
- پیاده‌سازی کلاس `BubbleChartManager`
- متدهای اصلی:
  - `createDepartmentChart(data)`: ایجاد نمودار بخش‌ها
  - `calculateBubbleSize(expense)`: محاسبه اندازه
  - `onDepartmentClick(deptId)`: handler کلیک
  - `refreshChart()`: بروزرسانی نمودار

**ویژگی‌های نمودار**:
- نوع: Bubble Chart (Chart.js)
- محور X: شناسه بخش (مخفی)
- محور Y: نرخ رشد (%)
- اندازه حباب: میزان هزینه
- رنگ: بر اساس رشد/کاهش
- Tooltip: نام، هزینه، رشد

**Deliverables**:
- [x] فایل analytics_bubble.js
- [x] کلاس کامل
- [x] تست کلیک‌ها

**Dependencies**: تسک 2.1, تسک 1.3

---

### ✅ تسک 2.3: Integration با Template
**مسئولیت**: Frontend Developer  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 2 ساعت

**شرح**:
- افزودن Canvas به `analytics_bubble.html`
- Import اسکریپت‌ها
- Initialize نمودار در `DOMContentLoaded`
- افزودن loading states

**HTML نمونه**:
```html
<div class="col-md-8">
    <div class="card">
        <div class="card-header">
            <h5>نمودار هزینه‌های بخش‌ها</h5>
        </div>
        <div class="card-body">
            <canvas id="departmentBubbleChart" height="400"></canvas>
        </div>
    </div>
</div>
```

**Deliverables**:
- [x] HTML structure
- [x] JavaScript initialization
- [x] Loading spinner

**Dependencies**: تسک 2.2

---

### ✅ تسک 2.4: تست نمودار بخش‌ها
**مسئولیت**: QA / Developer  
**اولویت**: 🟡 متوسط  
**زمان تخمینی**: 2 ساعت

**شرح**:
- تست بارگذاری داده‌ها
- تست نمایش صحیح حباب‌ها
- تست رنگ‌ها
- تست کلیک‌ها
- تست Tooltips

**Test Cases**:
1. حباب‌ها با اندازه صحیح نمایش داده شوند
2. رنگ‌ها مطابق رشد/کاهش باشند
3. کلیک روی حباب event را trigger کند
4. Tooltip اطلاعات صحیح نمایش دهد

**Deliverables**:
- [x] گزارش تست
- [x] رفع باگ‌ها

**Dependencies**: تسک 2.3

---

## 🎯 فاز 3: نمودار دسته‌بندی‌ها و لیست محصولات (2-3 روز)

### ✅ تسک 3.1: پیاده‌سازی نمودار دسته‌بندی‌ها
**مسئولیت**: Frontend Developer  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 4 ساعت

**شرح**:
- افزودن متد `createCategoryChart(deptId)` به کلاس
- Fetch داده از API دسته‌بندی‌ها
- ایجاد نمودار حبابی دوم
- نمایش نام دسته‌بندی و مبلغ داخل حباب

**ویژگی‌های خاص**:
- نمایش متن داخل حباب (مبلغ)
- رنگ مشابه نمودار اول
- کلیک برای نمایش محصولات

**Deliverables**:
- [x] متد createCategoryChart
- [x] Tooltip برای دسته‌بندی‌ها

**Dependencies**: تسک 2.2, تسک 1.4

---

### ✅ تسک 3.2: نمایش لیست محصولات
**مسئولیت**: Frontend Developer  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 4 ساعت

**شرح**:
- افزودن متد `loadProductList(catId, deptId)`
- Fetch داده از API محصولات
- رندر جدول HTML
- نمایش در container مخصوص

**ستون‌های جدول**:
- ردیف
- نام محصول
- مقدار
- قیمت واحد
- قیمت کل
- تاریخ

**Deliverables**:
- [x] متد loadProductList
- [x] متد renderProductTable
- [x] CSS برای جدول

**Dependencies**: تسک 3.1, تسک 1.5

---

### ✅ تسک 3.3: مدیریت Navigation و State
**مسئولیت**: Frontend Developer  
**اولویت**: 🟡 متوسط  
**زمان تخمینی**: 3 ساعت

**شرح**:
- ایجاد AppState object
- مدیریت state بین نمودارها
- پیاده‌سازی breadcrumb navigation
- دکمه بازگشت به نمودار قبل

**State Structure**:
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

**Deliverables**:
- [x] مدیریت state
- [x] Breadcrumb navigation
- [x] دکمه‌های بازگشت

**Dependencies**: تسک 3.1, 3.2

---

### ✅ تسک 3.4: تست Drill-down
**مسئولیت**: QA / Developer  
**اولویت**: 🟡 متوسط  
**زمان تخمینی**: 2 ساعت

**شرح**:
- تست کامل جریان drill-down:
  1. کلیک روی بخش → نمایش دسته‌بندی‌ها
  2. کلیک روی دسته‌بندی → نمایش محصولات
  3. بازگشت به مراحل قبل

**Test Cases**:
1. نمودار دسته‌بندی‌ها پس از کلیک نمایش داده شود
2. لیست محصولات صحیح باشد
3. Navigation کار کند
4. State به درستی نگهداری شود

**Deliverables**:
- [x] گزارش تست
- [x] رفع باگ‌های navigation

**Dependencies**: تسک 3.3

---

## ⏱️ فاز 4: فیلترهای زمانی و بهینه‌سازی (1-2 روز)

### ✅ تسک 4.1: پیاده‌سازی UI فیلترهای زمانی
**مسئولیت**: Frontend Developer  
**اولویت**: 🟡 متوسط  
**زمان تخمینی**: 2 ساعت

**شرح**:
- افزودن Button Group برای انتخاب دوره
- دکمه‌ها: هفتگی، ماهانه، فصلی، سالانه
- Highlight دکمه انتخاب شده
- Event handlers

**HTML**:
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

**Deliverables**:
- [x] UI فیلترها
- [x] Event listeners
- [x] Active state management

**Dependencies**: تسک 2.2

---

### ✅ تسک 4.2: اتصال فیلترها به نمودارها
**مسئولیت**: Frontend Developer  
**اولویت**: 🟡 متوسط  
**زمان تخمینی**: 2 ساعت

**شرح**:
- افزودن متد `changePeriod(period)` به کلاس
- بروزرسانی خودکار نمودارها با تغییر period
- نمایش loading state

**Deliverables**:
- [x] متد changePeriod
- [x] Auto-refresh نمودارها
- [x] Loading indicators

**Dependencies**: تسک 4.1

---

### ✅ تسک 4.3: پیاده‌سازی Caching در Backend
**مسئولیت**: Backend Developer  
**اولویت**: 🟡 متوسط  
**زمان تخمینی**: 3 ساعت

**شرح**:
- افزودن Django cache به API endpoints
- Cache key strategy
- Cache timeout: 5 دقیقه
- Clear cache عند تغییر داده‌ها

**کد نمونه**:
```python
from django.core.cache import cache
from django.views.decorators.cache import cache_page

@cache_page(60 * 5)
@api_view(['GET'])
def department_expenses_api(request):
    period = request.GET.get('period', 'weekly')
    cache_key = f'dept_expenses_{period}'
    
    data = cache.get(cache_key)
    if data is None:
        data = calculate_department_expenses(period)
        cache.set(cache_key, data, 60 * 5)
    
    return Response(data)
```

**Deliverables**:
- [x] Cache implementation
- [x] Cache invalidation
- [x] تست عملکرد

**Dependencies**: تسک‌های 1.3, 1.4, 1.5

---

### ✅ تسک 4.4: بهینه‌سازی Database Queries
**مسئولیت**: Backend Developer  
**اولویت**: 🟡 متوسط  
**زمان تخمینی**: 3 ساعت

**شرح**:
- استفاده از `select_related` و `prefetch_related`
- ترکیب query‌ها
- Index‌گذاری مناسب
- تست با Django Debug Toolbar

**بهینه‌سازی‌ها**:
- Reduce N+1 queries
- Aggregate در database
- Pagination برای لیست محصولات

**Deliverables**:
- [x] Query optimization
- [x] گزارش عملکرد (query count)

**Dependencies**: تسک‌های 1.3, 1.4, 1.5

---

### ✅ تسک 4.5: Responsive Design
**مسئولیت**: Frontend Developer  
**اولویت**: 🔵 پایین  
**زمان تخمینی**: 3 ساعت

**شرح**:
- تست در موبایل و تبلت
- تنظیم اندازه نمودارها
- تنظیم منوی sidebar (collapse در موبایل)
- تست در مرورگرهای مختلف

**Breakpoints**:
- Mobile: < 768px
- Tablet: 768px - 1024px
- Desktop: > 1024px

**Deliverables**:
- [x] CSS responsive
- [x] تست cross-browser
- [x] Mobile-friendly navigation

**Dependencies**: تسک 3.3

---

### ✅ تسک 4.6: تست نهایی و رفع باگ
**مسئولیت**: QA Team  
**اولویت**: 🔴 بالا  
**زمان تخمینی**: 4 ساعت

**شرح**:
- تست کامل تمام ویژگی‌ها
- تست Performance
- تست Security
- تست Accessibility

**Test Checklist**:
- [ ] همه API‌ها صحیح کار می‌کنند
- [ ] نمودارها درست نمایش داده می‌شوند
- [ ] رنگ‌ها صحیح هستند
- [ ] Drill-down کار می‌کند
- [ ] فیلترهای زمانی کار می‌کنند
- [ ] Responsive است
- [ ] Cache کار می‌کند
- [ ] عملکرد مناسب است (< 2s load time)
- [ ] امنیت (authentication, authorization)
- [ ] خطاها به درستی handle می‌شوند

**Deliverables**:
- [x] گزارش تست نهایی
- [x] لیست باگ‌ها و اولویت‌بندی
- [x] رفع باگ‌های Critical

**Dependencies**: همه تسک‌های قبلی

---

## 📝 تسک‌های اضافی (Optional)

### 🎨 تسک 5.1: انیمیشن‌ها و Transitions
**مسئولیت**: Frontend Developer  
**اولویت**: 🔵 پایین  
**زمان تخمینی**: 2 ساعت

- انیمیشن ظاهر شدن حباب‌ها
- Transition نرم بین نمودارها
- Hover effects

### 📊 تسک 5.2: Export به PDF/Excel
**مسئولیت**: Backend Developer  
**اولویت**: 🔵 پایین  
**زمان تخمینی**: 3 ساعت

- دکمه Export
- تولید PDF از نمودارها
- Export Excel با داده‌های خام

### 📱 تسک 5.3: PWA و Offline Support
**مسئولیت**: Frontend Developer  
**اولویت**: 🔵 پایین  
**زمان تخمینی**: 4 ساعت

- Service Worker
- Cache نمودارهای قبلی
- Offline message

---

## 📊 خلاصه زمان‌بندی

| فاز | تعداد تسک | زمان تخمینی | اولویت |
|-----|-----------|-------------|---------|
| **فاز 1: ساختار پایه** | 6 | 21 ساعت (~3 روز) | 🔴 بالا |
| **فاز 2: نمودار بخش‌ها** | 4 | 13 ساعت (~2 روز) | 🔴 بالا |
| **فاز 3: Drill-down** | 4 | 13 ساعت (~2 روز) | 🔴 بالا |
| **فاز 4: بهینه‌سازی** | 6 | 17 ساعت (~2 روز) | 🟡 متوسط |
| **کل** | **20 تسک** | **64 ساعت (~9 روز)** | - |

---

## 👥 تخصیص منابع

### Backend Developer
- 10 تسک
- 35 ساعت (~5 روز)

### Frontend Developer
- 8 تسک
- 25 ساعت (~3 روز)

### QA Tester
- 2 تسک
- 4 ساعت (~0.5 روز)

---

## ✅ معیارهای موفقیت (KPIs)

1. **عملکرد**:
   - زمان بارگذاری اولیه < 2 ثانیه
   - زمان تغییر نمودار < 500ms
   - تعداد query‌های database < 10 برای هر request

2. **کیفیت کد**:
   - Code coverage > 80%
   - No critical bugs
   - ESLint/Pylint compliance

3. **تجربه کاربری**:
   - Intuitive navigation
   - Smooth animations
   - Clear tooltips
   - Responsive design

4. **امنیت**:
   - Authentication برای همه endpoint‌ها
   - Authorization بر اساس role
   - Input validation

---

**آخرین بروزرسانی**: 2026-01-03  
**نسخه**: 1.0  
**وضعیت**: 📝 در حال برنامه‌ریزی
