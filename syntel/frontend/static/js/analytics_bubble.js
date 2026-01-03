/**
 * Analytics Bubble Chart Application
 * مدیریت نمودارهای حبابی تحلیلی
 * Version: 2.0 - Fixed for integration
 */

class AnalyticsApp {
    constructor() {
        this.state = {
            currentPeriod: 'monthly',
            selectedDepartment: null,
            selectedCategory: null,
            selectedSubcategory: null,
            departmentData: null,
            categoryData: null,
            subcategoryData: null
        };
        
        this.charts = {
            department: null,
            category: null,
            subcategory: null
        };
    }

    /**
     * Initialize application
     */
    init() {
        console.log('Analytics App initializing...');
        this.setupEventListeners();
        this.loadDepartmentChart();
    }

    /**
     * Setup all event listeners
     */
    setupEventListeners() {
        // Period filter buttons
        const periodButtons = document.querySelectorAll('.period-btn');
        periodButtons.forEach(btn => {
            btn.addEventListener('click', (e) => {
                periodButtons.forEach(b => b.classList.remove('active'));
                e.currentTarget.classList.add('active');
                this.state.currentPeriod = e.currentTarget.dataset.period;
                
                if (this.state.selectedSubcategory) {
                    this.loadProductList(this.state.selectedSubcategory, this.state.selectedDepartment);
                } else if (this.state.selectedCategory) {
                    this.loadSubcategoryChart(this.state.selectedCategory, this.state.selectedDepartment);
                } else if (this.state.selectedDepartment) {
                    this.loadCategoryChart(this.state.selectedDepartment);
                } else {
                    this.loadDepartmentChart();
                }
            });
        });

        // Back to departments button
        const backBtn = document.getElementById('back-to-departments');
        if (backBtn) {
            backBtn.addEventListener('click', () => {
                this.showDepartmentView();
            });
        }

        // Back to categories button
        const backToCatBtn = document.getElementById('back-to-categories');
        if (backToCatBtn) {
            backToCatBtn.addEventListener('click', () => {
                this.showCategoryView();
            });
        }

        // Back from products button
        const backFromProductsBtn = document.getElementById('back-from-products');
        if (backFromProductsBtn) {
            backFromProductsBtn.addEventListener('click', () => {
                this.goBackFromProducts();
            });
        }
    }

    /**
     * Load department bubble chart
     */
    async loadDepartmentChart() {
        console.log('Loading department chart for period:', this.state.currentPeriod);
        
        const loadingEl = document.getElementById('department-loading');
        const canvasEl = document.getElementById('departmentBubbleChart');
        
        if (!canvasEl) {
            console.error('Canvas element not found: departmentBubbleChart');
            return;
        }

        // Show loading
        if (loadingEl) loadingEl.style.display = 'flex';
        canvasEl.style.display = 'none';

        try {
            const response = await fetch(
                `/api/analytics/departments/expenses/?period=${this.state.currentPeriod}`,
                {
                    method: 'GET',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
                    },
                    credentials: 'same-origin'
                }
            );

            console.log('API Response status:', response.status);

            if (!response.ok) {
                const errorText = await response.text();
                console.error('API Error:', errorText);
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            console.log('API Data:', data);
            this.state.departmentData = data;

            // Hide loading, show canvas
            if (loadingEl) loadingEl.style.display = 'none';
            canvasEl.style.display = 'block';

            // Update total expense
            if (data.departments) {
                const total = data.departments.reduce((sum, d) => sum + (d.current_expense || 0), 0);
                const totalExpenseEl = document.getElementById('totalExpense');
                if (totalExpenseEl) {
                    totalExpenseEl.textContent = formatCurrency(total).replace(' تومان', '');
                }
            }

            // Destroy old chart if exists
            if (this.charts.department) {
                this.charts.department.destroy();
            }

            // Create new chart
            const departments = data.departments || [];
            if (departments.length === 0) {
                canvasEl.parentElement.innerHTML = `
                    <div style="text-align: center; padding: 2rem; color: #6b7280;">
                        <i class="fas fa-info-circle" style="font-size: 3rem; margin-bottom: 1rem;"></i>
                        <p>داده‌ای برای نمایش وجود ندارد</p>
                    </div>
                `;
                return;
            }

            this.charts.department = this.createBubbleChart(
                canvasEl,
                departments,
                'بخش',
                (deptId) => this.loadCategoryChart(deptId)
            );

            console.log('Department chart loaded successfully');

        } catch (error) {
            console.error('Error loading department chart:', error);
            if (loadingEl) loadingEl.style.display = 'none';
            
            const chartWrapper = canvasEl.parentElement;
            chartWrapper.innerHTML = `
                <div style="text-align: center; padding: 2rem; color: var(--danger-color);">
                    <i class="fas fa-exclamation-circle" style="font-size: 3rem; margin-bottom: 1rem;"></i>
                    <p>خطا در بارگذاری داده‌ها</p>
                    <small>${error.message}</small>
                    <br><br>
                    <button onclick="location.reload()" style="padding: 0.5rem 1rem; cursor: pointer;">
                        تلاش مجدد
                    </button>
                </div>
            `;
        }
    }

    /**
     * Load category bubble chart for a specific department
     * Shows only main categories (parent=null)
     */
    async loadCategoryChart(deptId) {
        console.log('Loading main categories for department:', deptId);
        
        this.state.selectedDepartment = deptId;
        this.state.selectedCategory = null;
        this.state.selectedSubcategory = null;
        
        const container = document.getElementById('category-chart-container');
        const subcatContainer = document.getElementById('subcategory-chart-container');
        const productContainer = document.getElementById('product-list-container');
        const deptContainer = document.getElementById('department-chart-container');
        
        if (container) container.style.display = 'block';
        if (subcatContainer) subcatContainer.style.display = 'none';
        if (productContainer) productContainer.style.display = 'none';
        if (deptContainer) deptContainer.style.display = 'none';
        
        const canvas = document.getElementById('categoryBubbleChart');
        const loadingEl = document.getElementById('category-loading');
        
        if (canvas) canvas.style.display = 'none';
        if (loadingEl) loadingEl.style.display = 'flex';

        try {
            const response = await fetch(
                `/api/analytics/department/${deptId}/categories/?period=${this.state.currentPeriod}`,
                {
                    method: 'GET',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
                    },
                    credentials: 'same-origin'
                }
            );

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            console.log('Category data:', data);
            this.state.categoryData = data;

            if (loadingEl) loadingEl.style.display = 'none';
            if (canvas) canvas.style.display = 'block';

            if (this.charts.category) {
                this.charts.category.destroy();
            }

            const categories = data.categories || [];
            if (categories.length === 0) {
                canvas.parentElement.innerHTML = `
                    <div style="text-align: center; padding: 2rem; color: #6b7280;">
                        <i class="fas fa-info-circle" style="font-size: 3rem; margin-bottom: 1rem;"></i>
                        <p>هیچ دسته‌بندی با هزینه یافت نشد</p>
                    </div>
                `;
                return;
            }

            // Update chart title
            const chartTitle = document.getElementById('categoryChartTitle');
            if (chartTitle && data.department) {
                chartTitle.textContent = `دسته‌بندی‌های ${data.department.name}`;
            }

            this.charts.category = this.createBubbleChart(
                canvas,
                categories,
                'دسته‌بندی',
                (catId) => {
                    // Check if category has subcategories
                    const category = categories.find(c => c.id === catId);
                    if (category && category.has_subcategories) {
                        this.loadSubcategoryChart(catId, deptId);
                    } else {
                        // Go directly to products
                        this.state.selectedCategory = catId;
                        this.loadProductList(catId, deptId);
                    }
                }
            );

            // Update breadcrumb
            const deptName = data.department ? data.department.name : 'بخش';
            const breadcrumb = document.getElementById('breadcrumb');
            if (breadcrumb) {
                breadcrumb.innerHTML = `
                    <li><a href="#" onclick="app.showDepartmentView(); return false;">داشبورد</a></li>
                    <li class="active">دسته‌بندی‌های ${deptName}</li>
                `;
            }

        } catch (error) {
            console.error('Error loading category chart:', error);
            if (loadingEl) loadingEl.style.display = 'none';
            showError('خطا در بارگذاری دسته‌بندی‌ها: ' + error.message);
        }
    }

    /**
     * Load subcategory bubble chart for a specific main category
     */
    async loadSubcategoryChart(catId, deptId) {
        console.log('Loading subcategories for category:', catId);
        
        this.state.selectedCategory = catId;
        this.state.selectedSubcategory = null;
        
        const container = document.getElementById('subcategory-chart-container');
        const catContainer = document.getElementById('category-chart-container');
        const productContainer = document.getElementById('product-list-container');
        const deptContainer = document.getElementById('department-chart-container');
        
        if (container) container.style.display = 'block';
        if (catContainer) catContainer.style.display = 'none';
        if (productContainer) productContainer.style.display = 'none';
        if (deptContainer) deptContainer.style.display = 'none';
        
        const canvas = document.getElementById('subcategoryBubbleChart');
        const loadingEl = document.getElementById('subcategory-loading');
        
        if (canvas) canvas.style.display = 'none';
        if (loadingEl) loadingEl.style.display = 'flex';

        try {
            const response = await fetch(
                `/api/analytics/category/${catId}/subcategories/?period=${this.state.currentPeriod}&department_id=${deptId}`,
                {
                    method: 'GET',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
                    },
                    credentials: 'same-origin'
                }
            );

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            console.log('Subcategory data:', data);
            this.state.subcategoryData = data;

            if (loadingEl) loadingEl.style.display = 'none';
            if (canvas) canvas.style.display = 'block';

            if (this.charts.subcategory) {
                this.charts.subcategory.destroy();
            }

            const subcategories = data.subcategories || [];
            
            // Update chart title
            const chartTitle = document.getElementById('subcategoryChartTitle');
            if (chartTitle && data.parent_category) {
                chartTitle.textContent = `زیر دسته‌بندی‌های ${data.parent_category.name}`;
            }

            // If there are direct products, add as an option
            let displayItems = [...subcategories];
            if (data.has_direct_products) {
                displayItems.unshift({
                    id: catId,
                    name: `محصولات مستقیم ${data.parent_category.name}`,
                    current_expense: data.direct_expense,
                    previous_expense: 0,
                    growth_rate: 0,
                    color: 'rgba(99, 102, 241, 0.7)',
                    is_direct: true
                });
            }

            if (displayItems.length === 0) {
                canvas.parentElement.innerHTML = `
                    <div style="text-align: center; padding: 2rem; color: #6b7280;">
                        <i class="fas fa-info-circle" style="font-size: 3rem; margin-bottom: 1rem;"></i>
                        <p>هیچ زیر دسته‌بندی با هزینه یافت نشد</p>
                    </div>
                `;
                return;
            }

            this.charts.subcategory = this.createBubbleChart(
                canvas,
                displayItems,
                'زیر دسته‌بندی',
                (subcatId) => {
                    this.state.selectedSubcategory = subcatId;
                    this.loadProductList(subcatId, deptId);
                }
            );

            // Update breadcrumb
            const deptName = this.state.categoryData?.department?.name || 'بخش';
            const catName = data.parent_category?.name || 'دسته‌بندی';
            const breadcrumb = document.getElementById('breadcrumb');
            if (breadcrumb) {
                breadcrumb.innerHTML = `
                    <li><a href="#" onclick="app.showDepartmentView(); return false;">داشبورد</a></li>
                    <li><a href="#" onclick="app.showCategoryView(); return false;">${deptName}</a></li>
                    <li class="active">زیر دسته‌های ${catName}</li>
                `;
            }

        } catch (error) {
            console.error('Error loading subcategory chart:', error);
            if (loadingEl) loadingEl.style.display = 'none';
            showError('خطا در بارگذاری زیر دسته‌بندی‌ها: ' + error.message);
        }
    }

    /**
     * Load product list for a specific category
     */
    async loadProductList(catId, deptId) {
        console.log('Loading products for category:', catId);
        
        this.state.selectedSubcategory = catId;
        
        const container = document.getElementById('product-list-container');
        const catContainer = document.getElementById('category-chart-container');
        const subcatContainer = document.getElementById('subcategory-chart-container');
        const deptContainer = document.getElementById('department-chart-container');
        
        if (container) container.style.display = 'block';
        if (catContainer) catContainer.style.display = 'none';
        if (subcatContainer) subcatContainer.style.display = 'none';
        if (deptContainer) deptContainer.style.display = 'none';
        
        if (!container) return;
        
        container.querySelector('.table-body').innerHTML = `
            <div style="text-align: center; padding: 2rem;">
                <div class="spinner"></div>
                <p>در حال بارگذاری محصولات...</p>
            </div>
        `;

        try {
            const response = await fetch(
                `/api/analytics/category/${catId}/products/?period=${this.state.currentPeriod}&department_id=${deptId}`,
                {
                    method: 'GET',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
                    },
                    credentials: 'same-origin'
                }
            );

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            console.log('Products data:', data);
            this.renderProductTable(data);

            // Update breadcrumb
            const deptName = this.state.categoryData?.department?.name || 'بخش';
            const catName = data.category?.name || 'دسته‌بندی';
            const breadcrumb = document.getElementById('breadcrumb');
            if (breadcrumb) {
                if (this.state.subcategoryData) {
                    // Coming from subcategory view
                    const parentCatName = this.state.subcategoryData.parent_category?.name || 'دسته‌بندی';
                    breadcrumb.innerHTML = `
                        <li><a href="#" onclick="app.showDepartmentView(); return false;">داشبورد</a></li>
                        <li><a href="#" onclick="app.showCategoryView(); return false;">${deptName}</a></li>
                        <li><a href="#" onclick="app.loadSubcategoryChart(${this.state.selectedCategory}, ${deptId}); return false;">${parentCatName}</a></li>
                        <li class="active">محصولات ${catName}</li>
                    `;
                } else {
                    // Coming from category view (no subcategories)
                    breadcrumb.innerHTML = `
                        <li><a href="#" onclick="app.showDepartmentView(); return false;">داشبورد</a></li>
                        <li><a href="#" onclick="app.showCategoryView(); return false;">${deptName}</a></li>
                        <li class="active">محصولات ${catName}</li>
                    `;
                }
            }

        } catch (error) {
            console.error('Error loading products:', error);
            container.querySelector('.table-body').innerHTML = `
                <div style="text-align: center; padding: 2rem; color: var(--danger-color);">
                    <i class="fas fa-exclamation-circle"></i>
                    خطا در بارگذاری محصولات: ${error.message}
                </div>
            `;
        }
    }

    /**
     * Render product table
     */
    renderProductTable(data) {
        const container = document.getElementById('product-list-container');
        if (!container) return;
        
        const tableBody = container.querySelector('.table-body');
        const tbody = container.querySelector('#productTableBody');
        
        // Update title
        const titleEl = document.getElementById('productListTitle');
        if (titleEl && data.category) {
            titleEl.textContent = `محصولات ${data.category.name}`;
        }
        
        if (!data.products || data.products.length === 0) {
            // Restore table structure and show empty message
            tableBody.innerHTML = `
                <div class="table-responsive">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>ردیف</th>
                                <th>نام محصول</th>
                                <th>مقدار</th>
                                <th>قیمت واحد</th>
                                <th>قیمت کل</th>
                                <th>درصد رشد</th>
                                <th>فروشنده</th>
                                <th>تاریخ</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td colspan="8" style="text-align: center; padding: 2rem; color: #6b7280;">
                                    <i class="fas fa-info-circle" style="font-size: 2rem; margin-bottom: 1rem; display: block;"></i>
                                    هیچ محصولی یافت نشد
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            `;
            return;
        }

        // Build table HTML
        let rowsHtml = '';
        data.products.forEach((product, index) => {
            const growthRate = product.growth_rate || 0;
            const growthClass = growthRate > 0 ? 'growth-positive' : (growthRate < 0 ? 'growth-negative' : 'growth-neutral');
            const growthIcon = growthRate > 0 ? '▲' : (growthRate < 0 ? '▼' : '−');
            const growthColor = growthRate > 0 ? '#ef4444' : (growthRate < 0 ? '#10b981' : '#6b7280');
            
            rowsHtml += `
                <tr>
                    <td>${index + 1}</td>
                    <td>${product.name || 'نامشخص'}</td>
                    <td>${formatNumber(product.quantity || 0)}</td>
                    <td>${formatCurrency(product.unit_price || 0)}</td>
                    <td>${formatCurrency(product.total_price || 0)}</td>
                    <td style="color: ${growthColor}; font-weight: 600;">
                        ${growthIcon} ${formatPercent(Math.abs(growthRate))}
                    </td>
                    <td>${product.supplier || '-'}</td>
                    <td>${toPersianDate(product.date || '')}</td>
                </tr>
            `;
        });

        // Restore full table structure
        tableBody.innerHTML = `
            <div class="table-responsive">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>ردیف</th>
                            <th>نام محصول</th>
                            <th>مقدار</th>
                            <th>قیمت واحد</th>
                            <th>قیمت کل</th>
                            <th>درصد رشد</th>
                            <th>فروشنده</th>
                            <th>تاریخ</th>
                        </tr>
                    </thead>
                    <tbody id="productTableBody">
                        ${rowsHtml}
                    </tbody>
                </table>
            </div>
        `;
    }

    /**
     * Create a bubble chart
     */
    createBubbleChart(canvasEl, items, labelType, clickHandler) {
        if (!canvasEl) {
            console.error('Canvas element is null');
            return null;
        }

        const ctx = canvasEl.getContext('2d');
        if (!ctx) {
            console.error('Could not get 2d context');
            return null;
        }

        // Calculate bubble sizes
        const amounts = items.map(item => item.current_expense || 0);
        const minAmount = Math.min(...amounts);
        const maxAmount = Math.max(...amounts);

        // Prepare datasets
        const datasets = items.map((item, index) => {
            const bubbleSize = calculateBubbleSize(
                item.current_expense || 0,
                minAmount,
                maxAmount
            );

            return {
                label: item.name || `${labelType} ${index + 1}`,
                data: [{
                    x: index,
                    y: item.growth_rate || 0,
                    r: bubbleSize
                }],
                backgroundColor: item.color || 'rgba(99, 102, 241, 0.7)',
                borderColor: (item.color || 'rgba(99, 102, 241, 1)').replace('0.8', '1').replace('0.7', '1'),
                borderWidth: 2,
                hoverBorderWidth: 3,
                itemData: item  // Store reference to original data
            };
        });

        // Create chart
        const chart = new Chart(ctx, {
            type: 'bubble',
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onClick: (event, elements) => {
                    if (elements && elements.length > 0) {
                        const index = elements[0].datasetIndex;
                        const item = items[index];
                        if (item && item.id) {
                            clickHandler(item.id);
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'right',
                        rtl: true,
                        labels: {
                            font: {
                                family: 'Vazir, Tahoma, Arial, sans-serif',
                                size: 12
                            },
                            padding: 15,
                            usePointStyle: true
                        }
                    },
                    tooltip: {
                        rtl: true,
                        backgroundColor: 'rgba(0, 0, 0, 0.85)',
                        padding: 12,
                        titleFont: {
                            family: 'Vazir, Tahoma, Arial, sans-serif',
                            size: 14
                        },
                        bodyFont: {
                            family: 'Vazir, Tahoma, Arial, sans-serif',
                            size: 12
                        },
                        callbacks: {
                            label: (context) => {
                                const item = items[context.datasetIndex];
                                return [
                                    `${labelType}: ${item.name}`,
                                    `هزینه فعلی: ${formatCurrency(item.current_expense)}`,
                                    `هزینه قبلی: ${formatCurrency(item.previous_expense)}`,
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
                            font: {
                                family: 'Vazir, Tahoma, Arial, sans-serif',
                                size: 14,
                                weight: 'bold'
                            }
                        },
                        ticks: {
                            font: {
                                family: 'Vazir, Tahoma, Arial, sans-serif',
                                size: 12
                            },
                            callback: (value) => value + '%'
                        },
                        grid: {
                            color: 'rgba(0, 0, 0, 0.05)'
                        }
                    }
                },
                interaction: {
                    mode: 'nearest',
                    intersect: true
                }
            }
        });

        return chart;
    }

    /**
     * Show department view (reset to initial state)
     */
    showDepartmentView() {
        this.state.selectedDepartment = null;
        this.state.selectedCategory = null;
        this.state.selectedSubcategory = null;
        
        const deptContainer = document.getElementById('department-chart-container');
        const catContainer = document.getElementById('category-chart-container');
        const subcatContainer = document.getElementById('subcategory-chart-container');
        const prodContainer = document.getElementById('product-list-container');
        
        if (deptContainer) deptContainer.style.display = 'block';
        if (catContainer) catContainer.style.display = 'none';
        if (subcatContainer) subcatContainer.style.display = 'none';
        if (prodContainer) prodContainer.style.display = 'none';
        
        // Reset breadcrumb
        const breadcrumb = document.getElementById('breadcrumb');
        if (breadcrumb) {
            breadcrumb.innerHTML = `
                <li><a href="/dashboard/">داشبورد</a></li>
                <li class="active">دپارتمان‌ها</li>
            `;
        }
        
        this.loadDepartmentChart();
    }

    /**
     * Show category view (back from subcategories)
     */
    showCategoryView() {
        if (!this.state.selectedDepartment) {
            this.showDepartmentView();
            return;
        }
        
        this.state.selectedCategory = null;
        this.state.selectedSubcategory = null;
        
        const deptContainer = document.getElementById('department-chart-container');
        const catContainer = document.getElementById('category-chart-container');
        const subcatContainer = document.getElementById('subcategory-chart-container');
        const prodContainer = document.getElementById('product-list-container');
        
        if (deptContainer) deptContainer.style.display = 'none';
        if (catContainer) catContainer.style.display = 'block';
        if (subcatContainer) subcatContainer.style.display = 'none';
        if (prodContainer) prodContainer.style.display = 'none';
        
        // Update breadcrumb
        const deptName = this.state.categoryData?.department?.name || 'بخش';
        const breadcrumb = document.getElementById('breadcrumb');
        if (breadcrumb) {
            breadcrumb.innerHTML = `
                <li><a href="#" onclick="app.showDepartmentView(); return false;">داشبورد</a></li>
                <li class="active">دسته‌بندی‌های ${deptName}</li>
            `;
        }
        
        this.loadCategoryChart(this.state.selectedDepartment);
    }

    /**
     * Go back from products to previous view
     */
    goBackFromProducts() {
        if (this.state.subcategoryData) {
            // Go back to subcategories
            this.loadSubcategoryChart(this.state.selectedCategory, this.state.selectedDepartment);
        } else {
            // Go back to categories
            this.showCategoryView();
        }
    }
}

// Utility functions
function getCsrfToken() {
    const name = 'csrftoken';
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

function formatCurrency(amount) {
    if (amount === null || amount === undefined) return '۰ تومان';
    const formatted = Number(amount).toLocaleString('fa-IR');
    return formatted + ' تومان';
}

function formatNumber(num) {
    if (num === null || num === undefined) return '۰';
    return Number(num).toLocaleString('fa-IR');
}

function formatPercent(value) {
    if (value === null || value === undefined) return '۰٪';
    const formatted = Number(value).toLocaleString('fa-IR', { maximumFractionDigits: 2 });
    return formatted + '٪';
}

function calculateBubbleSize(value, minValue, maxValue) {
    const minSize = 20;
    const maxSize = 60;
    
    if (maxValue === minValue) return (minSize + maxSize) / 2;
    
    const normalizedValue = (value - minValue) / (maxValue - minValue);
    return minSize + normalizedValue * (maxSize - minSize);
}

function showError(message) {
    console.error('Error:', message);
    alert('خطا: ' + message);
}

function showSuccess(message) {
    console.log('Success:', message);
}

function toPersianDate(dateStr) {
    if (!dateStr) return '-';
    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString('fa-IR');
    } catch (e) {
        return dateStr;
    }
}

// Global app instance
let app;

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, initializing Analytics App...');
    app = new AnalyticsApp();
    app.init();
});
