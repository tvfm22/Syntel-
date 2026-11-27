const _invCfg = (document.getElementById('invoiceRegisterConfig') || {}).dataset || {};
const SEARCH_SUPPLIER_PRODUCTS_URL = _invCfg.searchSupplierProductsUrl || '';
const PRODUCTS_AUTOCOMPLETE_URL = _invCfg.productsAutocompleteUrl || '';
function _escapeOptHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

document.addEventListener('DOMContentLoaded', function() {
    let itemCount = 1;
    
    // ============================================
    // Issues Management (باید در اولویت باشد)
    // ============================================
    let selectedIssues = [];
    
    const addIssueBtn = document.getElementById('add-issue');
    const issueSelect = document.getElementById('issue-select');
    const issuesTagsContainer = document.getElementById('issues-tags');
    const issuesInput = document.getElementById('issues-input');
    
    console.log('Issue elements:', {
        addIssueBtn: addIssueBtn,
        issueSelect: issueSelect,
        issuesTagsContainer: issuesTagsContainer,
        issuesInput: issuesInput
    });
    
    if (addIssueBtn && issueSelect) {
        addIssueBtn.addEventListener('click', function(e) {
            e.preventDefault();
            console.log('Add issue button clicked');
            
            const issueId = issueSelect.value;
            const issueName = issueSelect.options[issueSelect.selectedIndex].text;
            
            console.log('Selected issue:', issueId, issueName);
            
            if (issueId && issueId !== '' && !selectedIssues.includes(issueId)) {
                selectedIssues.push(issueId);
                addIssueTag(issueId, issueName);
                updateIssuesInput();
                issueSelect.value = '';
                console.log('Issue added successfully');
            } else {
                console.log('Issue not added - already exists or empty');
            }
        });
    } else {
        console.error('Issue elements not found!');
    }
    
    function addIssueTag(id, name) {
        if (!issuesTagsContainer) {
            console.error('Issues tags container not found');
            return;
        }
        
        const tag = document.createElement('span');
        tag.className = 'issue-tag';
        tag.appendChild(document.createTextNode(String(name || '') + ' '));
        const removeSpan = document.createElement('span');
        removeSpan.className = 'remove-issue';
        removeSpan.dataset.id = String(id);
        removeSpan.innerHTML = '&times;';
        tag.appendChild(removeSpan);
        issuesTagsContainer.appendChild(tag);
        
        const removeBtn = tag.querySelector('.remove-issue');
        if (removeBtn) {
            removeBtn.addEventListener('click', function(e) {
                e.preventDefault();
                const issueId = this.getAttribute('data-id');
                selectedIssues = selectedIssues.filter(id => id != issueId);
                tag.remove();
                updateIssuesInput();
                console.log('Issue removed:', issueId);
            });
        }
    }
    
    function updateIssuesInput() {
        if (issuesInput) {
            issuesInput.value = selectedIssues.join(',');
            console.log('Updated issues input:', issuesInput.value);
        }
    }
    
    // ============================================
    // Payment Section Toggle
    // ============================================
    const addPaymentCheckbox = document.getElementById('add-payment');
    const paymentSection = document.getElementById('payment-section');
    const paymentAmountInput = document.getElementById('payment-amount');
    const grandTotalElement = document.getElementById('grand-total');
    
    if (addPaymentCheckbox && paymentSection) {
        addPaymentCheckbox.addEventListener('change', function() {
            if (this.checked) {
                paymentSection.style.display = 'block';
                // پر کردن خودکار مبلغ پرداخت با مبلغ کل فاکتور
                if (paymentAmountInput && grandTotalElement) {
                    const totalText = grandTotalElement.textContent.replace(/[^\d]/g, '');
                    paymentAmountInput.value = totalText ? formatNumber(totalText) : '';
                    if (window.SyntelComma) window.SyntelComma.applyCommaToInput(paymentAmountInput);
                }
            } else {
                paymentSection.style.display = 'none';
            }
        });
    }
    
    // ============================================
    // Enter Key Navigation for Base Info Section
    // ============================================
    const baseInfoFields = [
        'supplier_name',
        'invoice_number',
        'invoice_date',
        'request_date',
        'total_cash_discount',
        'shipping_cost'
    ];
    
    baseInfoFields.forEach((fieldId, index) => {
        const field = document.getElementById(fieldId);
        if (field) {
            field.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    
                    // Move to next field
                    if (index < baseInfoFields.length - 1) {
                        const nextFieldId = baseInfoFields[index + 1];
                        const nextField = document.getElementById(nextFieldId);
                        if (nextField) {
                            nextField.focus();
                            // Select text if it's an input field
                            if (nextField.select) {
                                nextField.select();
                            }
                        }
                    } else {
                        // If last field, focus on first item description
                        const firstDescription = document.querySelector('.description-input');
                        if (firstDescription) {
                            firstDescription.focus();
                        }
                    }
                }
            });
        }
    });
    
    // ============================================
    // Initialize Select2 for Category Selects
    // ============================================
    function initSelect2(element) {
        $(element).select2({
            placeholder: "جستجو و انتخاب دسته‌بندی...",
            allowClear: true,
            dir: "rtl",
            language: {
                noResults: function() {
                    return "نتیجه‌ای یافت نشد";
                },
                searching: function() {
                    return "در حال جستجو...";
                }
            },
            width: '100%'
        });
    }
    
    // Initialize Select2 for existing category selects
    $('.category-select').each(function() {
        initSelect2(this);
    });
    
    // ============================================
    // Invoice Items Functionality
    // ============================================
    
    // Function to format number with commas (سه رقم سه رقم)
    function formatNumber(num) {
        if (!num || num === 0) return '0';
        return Math.round(num).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    }
    
    // Function to parse number from formatted string
    function parseNumber(str) {
        if (!str) return 0;
        const clean = window.SyntelComma
            ? window.SyntelComma.stripCommas(str)
            : str.toString().replace(/[,،٬\s]/g, '');
        return parseFloat(clean) || 0;
    }
    
    // Format money input field
    function formatMoneyInput(input) {
        if (window.SyntelComma) {
            window.SyntelComma.applyCommaToInput(input);
            return;
        }
        let cursorPos = input.selectionStart;
        let oldValue = input.value;
        let oldLength = oldValue.length;
        let rawValue = oldValue.replace(/,/g, '');
        if (/^\d*$/.test(rawValue) && rawValue !== '') {
            let numValue = parseInt(rawValue, 10);
            if (!isNaN(numValue)) {
                let formatted = formatNumber(numValue);
                if (formatted && formatted !== oldValue) {
                    input.value = formatted;
                    let newLength = formatted.length;
                    let newPos = cursorPos + (newLength - oldLength);
                    if (newPos < 0) newPos = 0;
                    if (newPos > newLength) newPos = newLength;
                    input.setSelectionRange(newPos, newPos);
                }
            }
        }
    }
    
    // Add event listeners to all money inputs
    function setupMoneyInputs() {
        document.querySelectorAll('.money-input').forEach(function(input) {
            if (!input.dataset.formatted) {
                input.dataset.formatted = 'true';
                
                input.addEventListener('input', function() {
                    formatMoneyInput(this);
                    calculateTotals();
                });
                
                input.addEventListener('paste', function(e) {
                    setTimeout(function() {
                        formatMoneyInput(e.target);
                        calculateTotals();
                    }, 10);
                });
            }
        });
    }
    
    function calculateTotals() {
        let itemsTotal = 0;
        let totalTax = 0;
        let totalDiscount = 0;
        
        document.querySelectorAll('.item-row').forEach(function(row) {
            const quantityInput = row.querySelector('.quantity-input');
            const unitPriceInput = row.querySelector('.unit-price-input');
            const taxInput = row.querySelector('.tax-input');
            const discountInput = row.querySelector('.discount-input');
            const lineTotalSpan = row.querySelector('.line-total');
            
            const quantity = parseFloat(quantityInput ? quantityInput.value : 0) || 0;
            const unitPrice = parseNumber(unitPriceInput ? unitPriceInput.value : 0);
            const taxAmount = parseNumber(taxInput ? taxInput.value : 0);
            const discountAmount = parseNumber(discountInput ? discountInput.value : 0);
            
            const baseTotal = quantity * unitPrice;
            const lineTotal = baseTotal + taxAmount - discountAmount;
            
            if (lineTotalSpan) {
                lineTotalSpan.textContent = formatNumber(lineTotal);
            }
            itemsTotal += lineTotal;
            totalTax += taxAmount;
            totalDiscount += discountAmount;
        });
        
        // Add shipping cost and subtract total cash discount
        const shippingCostEl = document.getElementById('shipping_cost');
        const totalCashDiscountEl = document.getElementById('total_cash_discount');
        
        const shippingCost = parseNumber(shippingCostEl ? shippingCostEl.value : 0);
        const totalCashDiscount = parseNumber(totalCashDiscountEl ? totalCashDiscountEl.value : 0);
        
        // Display items total
        const itemsTotalEl = document.getElementById('items-total');
        if (itemsTotalEl) {
            itemsTotalEl.textContent = formatNumber(itemsTotal) + ' ریال';
        }
        
        // Display shipping cost
        const shippingDisplay = document.getElementById('shipping-display');
        if (shippingDisplay) {
            shippingDisplay.textContent = formatNumber(shippingCost) + ' ریال';
        }
        
        // Display total discount
        const totalDiscountDisplay = document.getElementById('total-discount-display');
        if (totalDiscountDisplay) {
            const finalDiscount = totalDiscount + totalCashDiscount;
            totalDiscountDisplay.textContent = formatNumber(finalDiscount) + ' ریال';
        }
        
        const finalTotal = itemsTotal + shippingCost - totalCashDiscount; // Note: item line totals already include tax and discount
        
        const grandTotalEl = document.getElementById('grand-total');
        if (grandTotalEl) {
            grandTotalEl.textContent = formatNumber(finalTotal) + ' ریال';
        }
    }
    
    // Calculate on quantity input change
    document.addEventListener('input', function(e) {
        if (e.target.classList.contains('quantity-input') || 
            e.target.classList.contains('unit-price-input') ||
            e.target.classList.contains('tax-input') ||
            e.target.classList.contains('discount-input') ||
            e.target.id === 'shipping_cost' ||
            e.target.id === 'total_cash_discount') {
            calculateTotals();
        }
    });
    
    function escapeRegExp(str) {
        return String(str || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function appendHighlightedText(parentEl, text, query) {
        const str = String(text || '');
        if (!query) {
            parentEl.textContent = str;
            return;
        }
        const regex = new RegExp(`(${escapeRegExp(query)})`, 'gi');
        const parts = str.split(regex);
        const qLower = String(query).toLowerCase();
        parts.forEach(part => {
            if (!part) return;
            if (part.toLowerCase() === qLower) {
                const strong = document.createElement('strong');
                strong.textContent = part;
                parentEl.appendChild(strong);
            } else {
                parentEl.appendChild(document.createTextNode(part));
            }
        });
    }

    // Build category options HTML from server-side template
    function getCategoryOptions() {
        let options = '<option value="">انتخاب...</option>';
        let cats = [];
        try {
            const el = document.getElementById('categories-data');
            cats = el ? JSON.parse(el.textContent || '[]') : [];
        } catch (e) { cats = []; }
        cats.forEach(c => {
            const label = (c.parent ? _escapeOptHtml(c.parent) + ' › ' : '') + _escapeOptHtml(c.name);
            options += `<option value="${_escapeOptHtml(c.id)}">${label}</option>`;
        });
        return options;
    }

    function getRequesterOptions() {
        let options = '<option value="">انتخاب...</option>';
        let reqs = [];
        try {
            const el = document.getElementById('requesters-data');
            reqs = el ? JSON.parse(el.textContent || '[]') : [];
        } catch (e) { reqs = []; }
        reqs.forEach(r => {
            options += `<option value="${_escapeOptHtml(r.id)}">${_escapeOptHtml(r.label)}</option>`;
        });
        return options;
    }

    // Autocomplete functionality for products
    let _acCounter = 0;
    function setupAutocomplete(input) {
        if (!input.id) {
            input.id = 'description_auto_' + (++_acCounter);
        }
        let currentFocus;
        let debounceTimer = null;

        input.addEventListener("input", function(e) {
            const val = this.value;
            const supplierId = document.getElementById('supplier_id').value;
            const row = this.closest('.item-row');
            const pidInput = row ? row.querySelector('.product-id-input') : null;
            if (pidInput) {
                pidInput.value = '';
            }

            if (debounceTimer) {
                clearTimeout(debounceTimer);
            }
            closeAllLists();
            if (!val || val.length < 1) { return false; }

            currentFocus = -1;
            const self = this;

            debounceTimer = setTimeout(function() {
                if (self.value !== val) return;

                // Create a DIV element that will contain the items (values)
                const listDiv = document.createElement("DIV");
                listDiv.setAttribute("id", self.id + "autocomplete-list");
                listDiv.setAttribute("class", "autocomplete-items");
                self.parentNode.appendChild(listDiv);

                // Create two promises for parallel fetching
                const promises = [];

                // Fetch from supplier's previous products (higher priority)
                if (supplierId) {
                    promises.push(
                        fetch(`${SEARCH_SUPPLIER_PRODUCTS_URL}?supplier_id=${supplierId}&q=${encodeURIComponent(val)}`)
                            .then(response => response.json())
                            .then(data => ({ type: 'supplier', data: data.results }))
                            .catch(() => ({ type: 'supplier', data: [] }))
                    );
                }

                // Fetch from general products database
                if (val.length >= 2) {
                    promises.push(
                        fetch(`${PRODUCTS_AUTOCOMPLETE_URL}?q=${encodeURIComponent(val)}`)
                            .then(response => response.json())
                            .then(data => ({ type: 'products', data: data.results }))
                            .catch(() => ({ type: 'products', data: [] }))
                    );
                }

                // Wait for all promises to resolve
                Promise.all(promises).then(results => {
                    if (self.value !== val) return;
                    // Clear list
                    listDiv.innerHTML = '';

                    const supplierResults = results.find(r => r.type === 'supplier');
                    const productResults = results.find(r => r.type === 'products');

                    // First show supplier's previous products
                    if (supplierResults && supplierResults.data.length > 0) {
                        const headerDiv = document.createElement("DIV");
                        headerDiv.classList.add('autocomplete-header');
                        headerDiv.innerHTML = '<i class="fas fa-history"></i> محصولات قبلی این تامین‌کننده';
                        listDiv.appendChild(headerDiv);

                        supplierResults.data.forEach(item => {
                            const itemDiv = document.createElement("DIV");
                            itemDiv.classList.add('autocomplete-item');
                            const mainEl = document.createElement('div');
                            mainEl.className = 'autocomplete-main';
                            const itemName = (typeof item === 'object' && item !== null) ? item.name : item;
                            appendHighlightedText(mainEl, itemName, val);
                            itemDiv.appendChild(mainEl);
                            itemDiv.dataset.productName = itemName;
                            if (typeof item === 'object' && item !== null && item.id) {
                                itemDiv.dataset.productId = item.id;
                            }

                            itemDiv.addEventListener("click", function(e) {
                                input.value = this.dataset.productName;
                                if (pidInput) {
                                    pidInput.value = this.dataset.productId || '';
                                }
                                closeAllLists();
                            });
                            listDiv.appendChild(itemDiv);
                        });
                    }

                    // Then show general products with categories
                    if (productResults && productResults.data.length > 0) {
                        if (supplierResults && supplierResults.data.length > 0) {
                            const headerDiv = document.createElement("DIV");
                            headerDiv.classList.add('autocomplete-header');
                            headerDiv.innerHTML = '<i class="fas fa-box"></i> سایر محصولات';
                            listDiv.appendChild(headerDiv);
                        }

                        productResults.data.forEach(item => {
                            const itemDiv = document.createElement("DIV");
                            itemDiv.classList.add('autocomplete-item');

                            const mainText = document.createElement('div');
                            mainText.classList.add('autocomplete-main');
                            appendHighlightedText(mainText, item.name, val);

                            if (item.category) {
                                const categoryText = document.createElement('div');
                                categoryText.classList.add('autocomplete-sub');
                                categoryText.textContent = item.category;
                                itemDiv.appendChild(mainText);
                                itemDiv.appendChild(categoryText);
                            } else {
                                itemDiv.appendChild(mainText);
                            }

                            itemDiv.dataset.productId = item.id;
                            itemDiv.dataset.productName = item.name;
                            itemDiv.dataset.categoryName = item.category || '';

                            itemDiv.addEventListener("click", function(e) {
                                input.value = this.dataset.productName;
                                if (pidInput) {
                                    pidInput.value = this.dataset.productId || '';
                                }

                                // Auto-fill category if available
                                if (row && this.dataset.categoryName) {
                                    const categorySelect = row.querySelector('.category-select');
                                    if (categorySelect) {
                                        for (let option of categorySelect.options) {
                                            if (option.text.includes(this.dataset.categoryName)) {
                                                categorySelect.value = option.value;
                                                break;
                                            }
                                        }
                                    }
                                }

                                closeAllLists();
                            });
                            listDiv.appendChild(itemDiv);
                        });
                    }

                    // Show "no results" message if nothing found
                    if (listDiv.children.length === 0) {
                        const noResultDiv = document.createElement("DIV");
                        noResultDiv.classList.add('autocomplete-item', 'text-muted');
                        noResultDiv.textContent = 'نتیجه‌ای یافت نشد';
                        noResultDiv.style.cursor = 'default';
                        listDiv.appendChild(noResultDiv);
                    }
                });
            }, 250);
        });
        
        input.addEventListener("keydown", function(e) {
            let x = document.getElementById(this.id + "autocomplete-list");
            if (x) x = Array.from(x.getElementsByClassName("autocomplete-item"));
            if (e.keyCode == 40) { // DOWN
                currentFocus++;
                addActive(x);
            } else if (e.keyCode == 38) { // UP
                currentFocus--;
                addActive(x);
            } else if (e.keyCode == 13) { // ENTER
                e.preventDefault();
                if (currentFocus > -1) {
                    if (x && x[currentFocus]) x[currentFocus].click();
                }
            }
        });
        
        function addActive(x) {
            if (!x) return false;
            removeActive(x);
            if (currentFocus >= x.length) currentFocus = 0;
            if (currentFocus < 0) currentFocus = (x.length - 1);
            if (x[currentFocus]) {
                x[currentFocus].classList.add("autocomplete-active");
            }
        }
        
        function removeActive(x) {
            for (let i = 0; i < x.length; i++) {
                x[i].classList.remove("autocomplete-active");
            }
        }
        
        function closeAllLists(elmnt) {
            const x = document.getElementsByClassName("autocomplete-items");
            for (let i = 0; i < x.length; i++) {
                if (elmnt != x[i] && elmnt != input) {
                    x[i].parentNode.removeChild(x[i]);
                }
            }
        }
        
        document.addEventListener("click", function (e) {
            closeAllLists(e.target);
        });
    }

    // Initialize autocomplete for existing inputs
    document.querySelectorAll('.description-input').forEach(input => {
        setupAutocomplete(input);
    });
    
    // Add new item row
    const addItemBtn = document.getElementById('add-item');
    if (addItemBtn) {
        addItemBtn.addEventListener('click', function() {
            const tbody = document.getElementById('items-tbody');
            const newRow = document.createElement('tr');
            newRow.className = 'item-row';
            newRow.innerHTML = `
                <td>
                    <div class="input-wrapper">
                        <input type="text" class="form-control form-control-sm description-input" id="description_${itemCount}" name="description_${itemCount}" placeholder="شرح کالا" required autocomplete="off">
                        <input type="hidden" class="product-id-input" name="product_id_${itemCount}" value="">
                    </div>
                </td>
                <td>
                    <select class="form-select form-select-sm category-select" name="category_${itemCount}">
                        ${getCategoryOptions()}
                    </select>
                </td>
                <td>
                    <select class="form-select form-select-sm" name="requester_${itemCount}">
                        ${getRequesterOptions()}
                    </select>
                </td>
                <td>
                    <input type="number" step="0.01" class="form-control form-control-sm quantity-input" name="quantity_${itemCount}" placeholder="0" required>
                </td>
                <td>
                    <input type="text" class="form-control form-control-sm money-input unit-price-input" name="unit_price_${itemCount}" placeholder="0" required>
                </td>
                <td>
                    <input type="text" class="form-control form-control-sm money-input tax-input" name="tax_amount_${itemCount}" placeholder="0">
                </td>
                <td>
                    <input type="text" class="form-control form-control-sm money-input discount-input" name="discount_amount_${itemCount}" placeholder="0">
                </td>
                <td>
                    <span class="line-total font-weight-bold text-primary">0</span>
                </td>
                <td class="text-center">
                    <button type="button" class="btn-action remove-item">
                        <i class="fas fa-trash-alt"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(newRow);
            itemCount++;
            
            // Setup money inputs for new row
            setupMoneyInputs();
            
            // Setup autocomplete for new row
            setupAutocomplete(newRow.querySelector('.description-input'));
            
            // Initialize Select2 for new category select
            initSelect2(newRow.querySelector('.category-select'));
        });
    }
    
    // Remove item row
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('remove-item') || e.target.closest('.remove-item')) {
            const rows = document.querySelectorAll('.item-row');
            if (rows.length > 1) {
                const row = e.target.closest('.item-row');
                if (row) {
                    row.remove();
                    calculateTotals();
                }
            } else {
                alert('حداقل یک آیتم باید وجود داشته باشد');
            }
        }
    });
    
    // Initial setup
    setupMoneyInputs();
    calculateTotals();

    // تجزیه تاریخ به فیلدهای جداگانه
    function parseDateToFields() {
        const invoiceDate = document.getElementById('invoice_date').value;
        const requestDate = document.getElementById('request_date').value;
        const paymentDate = document.getElementById('payment-date').value;
        
        if (invoiceDate) {
            const parts = invoiceDate.split(/[-\/]/);
            if (parts.length === 3) {
                document.getElementById('invoice_year').value = parts[0];
                document.getElementById('invoice_month').value = parts[1];
                document.getElementById('invoice_day').value = parts[2];
            }
        }
        
        if (requestDate) {
            const parts = requestDate.split(/[-\/]/);
            if (parts.length === 3) {
                document.getElementById('request_year').value = parts[0];
                document.getElementById('request_month').value = parts[1];
                document.getElementById('request_day').value = parts[2];
            }
        }
        
        if (paymentDate) {
            const parts = paymentDate.split(/[-\/]/);
            if (parts.length === 3) {
                document.getElementById('payment_year').value = parts[0];
                document.getElementById('payment_month').value = parts[1];
                document.getElementById('payment_day').value = parts[2];
            }
        }
    }

    // ترکیب فیلدهای تاریخ
    function combineDateFields() {
        const invoiceYear = document.getElementById('invoice_year').value;
        const invoiceMonth = document.getElementById('invoice_month').value;
        const invoiceDay = document.getElementById('invoice_day').value;
        
        const requestYear = document.getElementById('request_year').value;
        const requestMonth = document.getElementById('request_month').value;
        const requestDay = document.getElementById('request_day').value;
        
        const paymentYear = document.getElementById('payment_year').value;
        const paymentMonth = document.getElementById('payment_month').value;
        const paymentDay = document.getElementById('payment_day').value;
        
        if (invoiceYear && invoiceMonth && invoiceDay) {
            document.getElementById('invoice_date').value = `${invoiceYear}/${invoiceMonth.padStart(2, '0')}/${invoiceDay.padStart(2, '0')}`;
        }
        
        if (requestYear && requestMonth && requestDay) {
            document.getElementById('request_date').value = `${requestYear}/${requestMonth.padStart(2, '0')}/${requestDay.padStart(2, '0')}`;
        }
        
        if (paymentYear && paymentMonth && paymentDay) {
            document.getElementById('payment-date').value = `${paymentYear}-${paymentMonth.padStart(2, '0')}-${paymentDay.padStart(2, '0')}`;
        }
    }

    // Auto-focus برای فیلدهای تاریخ
    function setupDateAutoFocus() {
        const sets = [
            {year: 'invoice_year', month: 'invoice_month', day: 'invoice_day'},
            {year: 'request_year', month: 'request_month', day: 'request_day'},
            {year: 'payment_year', month: 'payment_month', day: 'payment_day'}
        ];
        
        sets.forEach(set => {
            const year = document.getElementById(set.year);
            const month = document.getElementById(set.month);
            const day = document.getElementById(set.day);
            
            if (year) {
                year.addEventListener('input', function() {
                    if (this.value.length > 4) this.value = this.value.slice(0, 4);
                    if (this.value.length === 4) month.focus();
                });
            }
            
            if (month) {
                month.addEventListener('input', function() {
                    if (this.value.length > 2) this.value = this.value.slice(0, 2);
                    if (this.value.length === 2) day.focus();
                });
            }
            
            if (day) {
                day.addEventListener('input', function() {
                    if (this.value.length > 2) this.value = this.value.slice(0, 2);
                });
            }
        });
    }
    
    parseDateToFields();
    setupDateAutoFocus();
    
    // ترکیب تاریخ‌ها قبل از ارسال فرم
    document.querySelector('form').addEventListener('submit', function() {
        combineDateFields();
    });
});
