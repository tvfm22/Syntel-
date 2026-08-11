// Analytics Bubble Chart Implementation
let currentChart = null;
let currentLevel = 'department'; // department, category, product
let currentDepartment = null;
let currentCategory = null;
let currentPeriod = 'monthly';

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    // Load initial data
    loadDepartmentData();
    
    // Period change handler
    document.getElementById('period').addEventListener('change', function(e) {
        currentPeriod = e.target.value;
        // Reload current level
        if (currentLevel === 'department') {
            loadDepartmentData();
        } else if (currentLevel === 'category') {
            loadCategoryData(currentDepartment);
        } else if (currentLevel === 'product') {
            loadProductData(currentDepartment, currentCategory);
        }
    });
});

// Load department data
function loadDepartmentData() {
    showLoading();
    currentLevel = 'department';
    currentDepartment = null;
    currentCategory = null;
    
    fetch(`/api/analytics/expenses/departments/?period=${currentPeriod}`)
        .then(response => response.json())
        .then(data => {
            updateBreadcrumb([{text: 'دپارتمان‌ها', active: true}]);
            updateStats(data.summary);
            renderBubbleChart(data.data, 'department');
            hideProductTable();
            hideLoading();
        })
        .catch(error => {
            console.error('Error loading department data:', error);
            hideLoading();
        });
}

// Load category data for a department
function loadCategoryData(departmentId) {
    showLoading();
    currentLevel = 'category';
    currentDepartment = departmentId;
    currentCategory = null;
    
    fetch(`/api/analytics/expenses/categories/${departmentId}/?period=${currentPeriod}`)
        .then(response => response.json())
        .then(data => {
            updateBreadcrumb([
                {text: 'دپارتمان‌ها', link: true},
                {text: data.department_name, active: true}
            ]);
            updateStats(data.summary);
            renderBubbleChart(data.data, 'category');
            hideProductTable();
            hideLoading();
        })
        .catch(error => {
            console.error('Error loading category data:', error);
            hideLoading();
        });
}

// Load product data for a category
function loadProductData(departmentId, categoryId) {
    showLoading();
    currentLevel = 'product';
    currentDepartment = departmentId;
    currentCategory = categoryId;
    
    fetch(`/api/analytics/expenses/products/${departmentId}/${categoryId}/?period=${currentPeriod}`)
        .then(response => response.json())
        .then(data => {
            updateBreadcrumb([
                {text: 'دپارتمان‌ها', link: true},
                {text: data.department_name, link: true, departmentId: departmentId},
                {text: data.category_name, active: true}
            ]);
            updateStats(data.summary);
            renderProductTable(data.products);
            if (currentChart) {
                currentChart.destroy();
                currentChart = null;
            }
            document.getElementById('chartTitle').textContent = 'محصولات دسته‌بندی';
            hideLoading();
        })
        .catch(error => {
            console.error('Error loading product data:', error);
            hideLoading();
        });
}

// Update breadcrumb
function updateBreadcrumb(items) {
    const breadcrumb = document.getElementById('breadcrumb');
    breadcrumb.innerHTML = items.map((item, index) => {
        if (item.link) {
            return `
                <li class="breadcrumb-item">
                    <a href="#" onclick="handleBreadcrumbClick(${index}, ${item.departmentId || null}); return false;">
                        ${item.text}
                    </a>
                </li>
            `;
        } else {
            return `<li class="breadcrumb-item active">${item.text}</li>`;
        }
    }).join('');
}

// Handle breadcrumb click
function handleBreadcrumbClick(index, departmentId) {
    if (index === 0) {
        // Back to departments
        loadDepartmentData();
    } else if (index === 1 && departmentId) {
        // Back to categories
        loadCategoryData(departmentId);
    }
}

// Update stats
function updateStats(summary) {
    document.getElementById('totalExpense').textContent = 
        new Intl.NumberFormat('fa-IR').format(summary.total_expense) + ' ریال';
    document.getElementById('totalItems').textContent = 
        new Intl.NumberFormat('fa-IR').format(summary.total_items);
    
    const change = summary.change_percent || 0;
    const changeEl = document.getElementById('changePercent');
    changeEl.textContent = (change > 0 ? '+' : '') + change.toFixed(1) + '%';
    changeEl.style.color = change > 0 ? '#ef4444' : '#10b981';
}

// Render bubble chart
function renderBubbleChart(data, level) {
    const ctx = document.getElementById('bubbleChart').getContext('2d');
    
    // Destroy previous chart
    if (currentChart) {
        currentChart.destroy();
    }
    
    // Prepare datasets
    const bubbles = data.map((item, index) => ({
        x: index + 1,
        y: item.y,
        r: item.r,
        id: item.id,
        label: item.label,
        change: item.change_percent,
        count: item.count,
        expense: item.expense
    }));
    
    currentChart = new Chart(ctx, {
        type: 'bubble',
        data: {
            datasets: [{
                label: level === 'department' ? 'دپارتمان‌ها' : 'دسته‌بندی‌ها',
                data: bubbles,
                backgroundColor: bubbles.map(b => getColorForChange(b.change, 0.6)),
                borderColor: bubbles.map(b => getColorForChange(b.change, 1)),
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    rtl: true,
                    textDirection: 'rtl',
                    backgroundColor: 'rgba(0, 0, 0, 0.8)',
                    padding: 12,
                    bodyFont: {
                        size: 13,
                        family: 'Vazir'
                    },
                    callbacks: {
                        title: function(context) {
                            return context[0].raw.label;
                        },
                        label: function(context) {
                            const data = context.raw;
                            return [
                                `هزینه: ${new Intl.NumberFormat('fa-IR').format(data.expense)} ریال`,
                                `تعداد: ${new Intl.NumberFormat('fa-IR').format(data.count)}`,
                                `تغییرات: ${data.change > 0 ? '+' : ''}${data.change.toFixed(1)}%`
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
                    beginAtZero: true,
                    ticks: {
                        callback: function(value) {
                            return new Intl.NumberFormat('fa-IR', {
                                notation: 'compact',
                                compactDisplay: 'short'
                            }).format(value);
                        },
                        font: {
                            family: 'Vazir',
                            size: 11
                        }
                    },
                    grid: {
                        color: 'rgba(0, 0, 0, 0.05)'
                    }
                }
            },
            onClick: (event, elements) => {
                if (elements.length > 0) {
                    const index = elements[0].index;
                    const bubble = bubbles[index];
                    handleBubbleClick(bubble, level);
                }
            }
        }
    });
    
    // Update chart title
    if (level === 'department') {
        document.getElementById('chartTitle').textContent = 'هزینه‌های دپارتمان‌ها';
    } else if (level === 'category') {
        document.getElementById('chartTitle').textContent = 'هزینه‌های دسته‌بندی‌ها';
    }
}

// Handle bubble click
function handleBubbleClick(bubble, level) {
    if (level === 'department') {
        // Drill down to categories
        loadCategoryData(bubble.id);
    } else if (level === 'category') {
        // Drill down to products
        loadProductData(currentDepartment, bubble.id);
    }
}

// Get color based on change percentage
function getColorForChange(change, alpha) {
    if (change > 0) {
        // Red for increase (bad)
        return `rgba(239, 68, 68, ${alpha})`;
    } else if (change < 0) {
        // Green for decrease (good)
        return `rgba(16, 185, 129, ${alpha})`;
    } else {
        // Gray for no change
        return `rgba(107, 114, 128, ${alpha})`;
    }
}

// Render product table
function renderProductTable(products) {
    const tbody = document.getElementById('productTableBody');
    tbody.innerHTML = products.map(product => `
        <tr>
            <td>${product.code}</td>
            <td>${product.name}</td>
            <td>${new Intl.NumberFormat('fa-IR').format(product.purchase_count)}</td>
            <td>${new Intl.NumberFormat('fa-IR').format(product.total_expense)} ریال</td>
            <td>${new Intl.NumberFormat('fa-IR').format(product.avg_price)} ریال</td>
            <td>
                <span class="badge ${product.change_percent > 0 ? 'badge-danger' : 'badge-success'}">
                    ${product.change_percent > 0 ? '+' : ''}${product.change_percent.toFixed(1)}%
                </span>
            </td>
        </tr>
    `).join('');
    
    showProductTable();
}

// Show/hide product table
function showProductTable() {
    document.getElementById('productTableContainer').classList.add('show');
}

function hideProductTable() {
    document.getElementById('productTableContainer').classList.remove('show');
}

// Show/hide loading
function showLoading() {
    document.getElementById('loadingOverlay').style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loadingOverlay').style.display = 'none';
}
