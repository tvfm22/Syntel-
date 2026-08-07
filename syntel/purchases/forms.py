from django import forms
from .models import PurchaseRequest, PurchaseItem, StockItem, StockTransaction, Product, Unit, Warehouse

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
    """فرم ثبت موجودی اولیه انبار - برندمحور
    محصول و برند به صورت فیلد مخفی (از طریق اتوکامپلیت) ارسال می‌شوند"""
    product = forms.IntegerField(widget=forms.HiddenInput(), required=True, label='شناسه محصول')
    product_brand = forms.IntegerField(widget=forms.HiddenInput(), required=False, label='شناسه برند')
    warehouse = forms.ModelChoiceField(
        queryset=Warehouse.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='انبار',
        empty_label='انتخاب انبار'
    )
    quantity = forms.DecimalField(
        max_digits=12,
        decimal_places=4,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'مقدار', 'step': 'any'}),
        label='مقدار'
    )

    class Meta:
        model = StockItem
        fields = ['warehouse']