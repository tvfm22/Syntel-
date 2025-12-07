from django import forms
from .models import PurchaseRequest, PurchaseItem

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