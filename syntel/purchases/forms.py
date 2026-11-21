from decimal import Decimal

from django import forms
from django.core.validators import MinValueValidator
from .models import PurchaseRequest, PurchaseItem

class PurchaseItemForm(forms.ModelForm):
    quantity = forms.DecimalField(
        max_digits=12, decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001'))],
        widget=forms.NumberInput(attrs={
            'class': 'form-control', 'placeholder': 'مقدار', 'min': '0.0001', 'step': 'any',
        }),
    )

    class Meta:
        model = PurchaseItem
        fields = ['name', 'quantity', 'unit', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'نام کالا'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'واحد (کیلو، عدد، لیتر، ...)'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'توضیحات اختیاری'}),
        }

class PurchaseRequestForm(forms.ModelForm):
    class Meta:
        model = PurchaseRequest
        fields = []