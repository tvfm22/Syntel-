# Additional views for approval workflow and activity log
from .views import custom_login_required

@custom_login_required
@role_required(['manager'])
def approvals_list(request):
    # Get all requests pending approval
    requests = PurchaseRequest.objects.filter(status='pending_approval').select_related('user').prefetch_related('items')
    
    context = {
        'requests': requests,
    }
    return render(request, 'approvals_list.html', context)

@custom_login_required
@role_required(['manager'])
def approve_request(request, request_id):
    purchase_request = get_object_or_404(PurchaseRequest, id=request_id)
    
    if purchase_request.status == 'pending_approval':
        purchase_request.status = 'approved'
        purchase_request.approved_by = request.user
        purchase_request.approved_at = timezone.now()
        purchase_request.save()
        
        # Log activity
        ActivityLog.objects.create(
            user=request.user,
            request=purchase_request,
            action='approved',
            description='Manager approved the request'
        )
        
        messages.success(request, 'درخواست تأیید شد')
    else:
        messages.error(request, 'این درخواست قبلاً بررسی شده است')
    
    return redirect('approvals_list')

@custom_login_required
@role_required(['manager'])
def reject_request(request, request_id):
    purchase_request = get_object_or_404(PurchaseRequest, id=request_id)
    
    if request.method == 'POST':
        reason = request.POST.get('reason', '')
        
        if purchase_request.status == 'pending_approval':
            purchase_request.status = 'rejected'
            purchase_request.rejection_reason = reason
            purchase_request.approved_by = request.user
            purchase_request.approved_at = timezone.now()
            purchase_request.save()
            
            # Log activity
            ActivityLog.objects.create(
                user=request.user,
                request=purchase_request,
                action='rejected',
                description=f'Manager rejected: {reason}'
            )
            
            messages.success(request, 'درخواست رد شد')
            return redirect('approvals_list')
    
    context = {
        'purchase_request': purchase_request,
    }
    return render(request, 'reject_request.html', context)

@custom_login_required
def activity_log(request, request_id):
    purchase_request = get_object_or_404(PurchaseRequest, id=request_id)
    activities = purchase_request.activities.all()
    
    context = {
        'purchase_request': purchase_request,
        'activities': activities,
    }
    return render(request, 'activity_log.html', context)