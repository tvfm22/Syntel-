# Migration to add supplier foreign key to PurchaseOrder and migrate data

from django.db import migrations, models
import django.db.models.deletion


def migrate_supplier_data(apps, schema_editor):
    """Migrate existing supplier data from text fields to Supplier model"""
    PurchaseOrder = apps.get_model('purchases', 'PurchaseOrder')
    Supplier = apps.get_model('purchases', 'Supplier')

    # Get all unique supplier names from existing orders
    supplier_data = PurchaseOrder.objects.values_list(
        'supplier_name', 'supplier_contact', 'supplier_address'
    ).distinct()

    supplier_map = {}

    for supplier_name, supplier_contact, supplier_address in supplier_data:
        if supplier_name:
            # Create or get supplier
            supplier, created = Supplier.objects.get_or_create(
                name=supplier_name,
                defaults={
                    'contact_person': supplier_contact.split(' - ')[0] if supplier_contact else '',
                    'phone': supplier_contact.split(' - ')[1] if supplier_contact and ' - ' in supplier_contact else supplier_contact,
                    'address': supplier_address,
                    'status': 'active'
                }
            )
            supplier_map[supplier_name] = supplier

    # Update all purchase orders to use the supplier foreign key
    for order in PurchaseOrder.objects.all():
        if order.supplier_name in supplier_map:
            order.supplier = supplier_map[order.supplier_name]
            order.save()


def reverse_migrate_supplier_data(apps, schema_editor):
    """Reverse migration - restore text fields from supplier model"""
    PurchaseOrder = apps.get_model('purchases', 'PurchaseOrder')

    for order in PurchaseOrder.objects.all():
        if order.supplier:
            order.supplier_name = order.supplier.name
            order.supplier_contact = order.supplier.get_contact_info()
            order.supplier_address = order.supplier.get_full_address()
            order.save()


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0011_add_supplier_models'),
    ]

    operations = [
        # Add the supplier foreign key field
        migrations.AddField(
            model_name='purchaseorder',
            name='supplier',
            field=models.ForeignKey(
                'Supplier',
                on_delete=django.db.models.deletion.CASCADE,
                related_name='purchase_orders',
                verbose_name='تأمین‌کننده',
                null=True  # Temporarily allow null
            ),
        ),

        # Run data migration to populate supplier field
        migrations.RunPython(
            migrate_supplier_data,
            reverse_migrate_supplier_data
        ),

        # Make supplier field non-nullable
        migrations.AlterField(
            model_name='purchaseorder',
            name='supplier',
            field=models.ForeignKey(
                'Supplier',
                on_delete=django.db.models.deletion.CASCADE,
                related_name='purchase_orders',
                verbose_name='تأمین‌کننده'
            ),
        ),

        # Remove old text fields
        migrations.RemoveField(
            model_name='purchaseorder',
            name='supplier_name',
        ),
        migrations.RemoveField(
            model_name='purchaseorder',
            name='supplier_contact',
        ),
        migrations.RemoveField(
            model_name='purchaseorder',
            name='supplier_address',
        ),
    ]