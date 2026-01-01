from django import forms
from .models import PurchaseRequest, PurchaseItem, StockItem, StockTransaction, Product, Unit

class PurchaseItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseItem
        fields = ['name', 'quantity', 'unit', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'نام کالا'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'مقدار'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'واحد (کیلو، عدد، لیتر، ...)'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'توضیحات اختیاری'}),
        }

class PurchaseRequestForm(forms.ModelForm):
    class Meta:
        model = PurchaseRequest
        fields = []


class InitialStockForm(forms.ModelForm):
    """فرم ثبت موجودی اولیه انبار"""
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='محصول',
        empty_label='انتخاب محصول'
    )
    quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'مقدار', 'step': '0.01'}),
        label='مقدار'
    )
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='واحد',
        required=False
    )
    min_quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'حداقل موجودی', 'step': '0.01'}),
        label='حداقل موجودی',
        initial=0
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'توضیحات'}),
        label='توضیحات',
        required=False
    )

    class Meta:
        model = StockItem
        fields = ['product', 'quantity', 'unit', 'min_quantity']