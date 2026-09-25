import pandas as pd
import re
import json
from django.shortcuts import render, redirect
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from .models import Contact
from django.db.models.functions import TruncDate
from django.contrib.auth.models import User
from django.utils import timezone 

@never_cache
@login_required
def upload_excel(request):
    if request.method == 'POST':
        if 'excel_file' not in request.FILES:
            messages.error(request, "Please select a file to upload.")
            return redirect('upload_excel')
            
        excel_file = request.FILES['excel_file']
        
        if not excel_file.name.endswith(('.xlsx', '.xls')):
            messages.error(request, "Invalid file format. Please upload an Excel (.xlsx or .xls) file.")
            return redirect('upload_excel')
        
        try:
            df = pd.read_excel(excel_file).fillna('')
            
            df.columns = [str(col).strip().lower() for col in df.columns]
            
            if 'cx name' not in df.columns or 'contact' not in df.columns:
                messages.error(request, "Upload failed: The Excel file MUST contain 'Cx Name' and 'Contact' columns.")
                return redirect('upload_excel')

            
            Contact.objects.filter(user=request.user).update(is_active=False)


            count = 0
            for index, row in df.iterrows():
                clean_name = " ".join(str(row.get('cx name', '')).split())
                clean_phone = " ".join(str(row.get('contact', '')).split())
                
                Contact.objects.create(
                    user=request.user,
                    name=clean_name,
                    phone_number=clean_phone
                )
                count += 1
            
            messages.success(request, f"Successfully uploaded {count} contacts!")
            return redirect('contact_list')
            
        except Exception as e:
            messages.error(request, f"Error processing file: {str(e)}")
            return redirect('upload_excel')
        
    return render(request, 'upload.html')

@never_cache
@login_required
def contact_list(request):
    # 1. If user clicks "Clear", wipe the search query and restore the pre-search page
    if 'clear' in request.GET:
        request.session['last_query'] = ''
        restore_page = request.session.get('pre_search_page', 1)
        return redirect(f"/contacts/?page={restore_page}")

    # 2. If user clicks "View Contacts" from navbar, restore their exact last state
    if not request.GET and 'last_page' in request.session:
        last_page = request.session.get('last_page', 1)
        last_query = request.session.get('last_query', '')
        
        redirect_url = f"/contacts/?page={last_page}"
        if last_query:
            redirect_url += f"&q={last_query}"
        return redirect(redirect_url)

    # 3. Process the current request
    raw_query = request.GET.get('q', '')
    page_number = request.GET.get('page', 1)
    
    # If they are NOT searching right now, save this page as the safe return point
    if not raw_query:
        request.session['pre_search_page'] = page_number
        
    request.session['last_query'] = raw_query
    request.session['last_page'] = page_number
    
    cleaned_query = " ".join(raw_query.split())
    
    # Admins see all contacts, normal users only see their own
    if request.user.is_staff or request.user.is_superuser:
        base_contacts = Contact.objects.all()
    else:
        base_contacts = Contact.objects.filter(user=request.user, is_active=True)
    
    if cleaned_query:
        escaped_query = re.escape(cleaned_query)
        
        # REGEX: Matches the exact name, and forgives trailing dots, dashes, or spaces
        regex_pattern = rf'^{escaped_query}[^a-zA-Z0-9]*$'
        
        contacts = base_contacts.filter(
            Q(name__iregex=regex_pattern) | Q(phone_number__icontains=cleaned_query)
        ).order_by('id')
    else:
        contacts = base_contacts.order_by('id')

    paginator = Paginator(contacts, 10)
    page_obj = paginator.get_page(page_number)

    return render(request, 'contact_list.html', {'page_obj': page_obj, 'query': raw_query})

@never_cache
@login_required
def update_contacts(request):
    if request.method == 'POST':
        page = request.POST.get('current_page', 1)
        query = request.POST.get('current_query', '')
        
        # Determine base queryset for security
        if request.user.is_staff or request.user.is_superuser:
            base_contacts = Contact.objects.all()
        else:
            base_contacts = Contact.objects.filter(user=request.user, is_active=True)
            
        for key, value in request.POST.items():
            if key.startswith('status_'):
                contact_id = key.split('_')[1]
                try:
                    contact = base_contacts.get(id=contact_id)
                    contact.call_status = value
                    contact.description = request.POST.get(f'desc_{contact_id}', '')
                    contact.save()
                except Contact.DoesNotExist:
                    pass
        
        messages.success(request, "Contact statuses updated successfully!")            
        
        redirect_url = f"/contacts/?page={page}"
        if query:
            redirect_url += f"&q={query}"
        return redirect(redirect_url)
        
    return redirect('contact_list')

@never_cache
@login_required
def auto_update_contact(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            contact_id = data.get('id')
            
            # Security: Ensure they can only update their own leads via AJAX
            if request.user.is_staff or request.user.is_superuser:
                contact = Contact.objects.get(id=contact_id)
            else:
                contact = Contact.objects.get(id=contact_id, user=request.user, is_active=True)
            
            if 'status' in data:
                contact.call_status = data['status']
            
            if 'description' in data:
                contact.description = data['description']
                
            # Save the reminder date
            if 'reminder_date' in data:
                date_val = data['reminder_date']
                contact.reminder_date = date_val if date_val else None
                
            contact.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@never_cache
@login_required
def export_excel(request):
    if request.user.is_staff or request.user.is_superuser:
        contacts = Contact.objects.all()
    else:
        contacts = Contact.objects.filter(user=request.user, is_active=True)
        
    contacts = contacts.values('name', 'phone_number', 'call_status', 'description')
    df = pd.DataFrame(list(contacts))
    
    if not df.empty:
        df.rename(columns={
            'name': 'Cx Name', 
            'phone_number': 'Contact', 
            'call_status': 'Call Status', 
            'description': 'Description'
        }, inplace=True)
    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="updated_leads.xlsx"'
    
    df.to_excel(response, index=False)
    return response

@never_cache
@login_required
def dashboard(request):
    today = timezone.localdate()

    if request.user.is_staff or request.user.is_superuser:
        # --- ADMIN DASHBOARD ---
        
        search_query = request.GET.get('q', '').strip()
        search_results = None
        
        if search_query:
            search_results = Contact.objects.filter(
                Q(name__icontains=search_query) | Q(phone_number__icontains=search_query)
            ).select_related('user').order_by('-last_updated')[:50]

        # Calculate company-wide aggregate totals
        company_stats = Contact.objects.aggregate(
            total_leads=Count('id'),
            pending=Count('id', filter=Q(call_status='Pending')),
            total_called=Count('id', filter=~Q(call_status='Pending')),
            called_today=Count('id', filter=~Q(call_status='Pending') & Q(last_updated__date=today)),
            connected=Count('id', filter=Q(call_status='Connected')),
            not_connected=Count('id', filter=Q(call_status='Not Connected'))
        )

        # Employee metrics
        employees = User.objects.filter(is_superuser=False, is_staff=False).annotate(
            total_leads=Count('contact'),
            pending=Count('contact', filter=Q(contact__call_status='Pending')),
            total_called=Count('contact', filter=~Q(contact__call_status='Pending')),
            called_today=Count('contact', filter=~Q(contact__call_status='Pending') & Q(contact__last_updated__date=today)),
            connected=Count('contact', filter=Q(contact__call_status='Connected')),
            not_connected=Count('contact', filter=Q(contact__call_status='Not Connected'))
        ).order_by('username')
        
        # Daily history
        daily_history_raw = Contact.objects.filter(
            ~Q(call_status='Pending'), 
            last_updated__isnull=False
        ).annotate(
            date=TruncDate('last_updated')
        ).values('user__id', 'date').annotate(
            daily_calls=Count('id')
        ).order_by('-date')
        
        history_by_user = {}
        for entry in daily_history_raw:
            uid = entry['user__id']
            if uid not in history_by_user:
                history_by_user[uid] = []
            history_by_user[uid].append({
                'date': entry['date'],
                'calls': entry['daily_calls']
            })
            
        for emp in employees:
            emp.daily_history = history_by_user.get(emp.id, [])
            
        context = {
            'is_admin': True,
            'employees': employees,
            'search_query': search_query,
            'search_results': search_results,
            'company_stats': company_stats,
        }
        return render(request, 'dashboard.html', context)
        
    else:
        # --- TELECALLER DASHBOARD ---
        base_contacts = Contact.objects.filter(user=request.user, is_active=True)
        total_leads = base_contacts.count()
        status_metrics = base_contacts.values('call_status').annotate(total=Count('call_status'))
        
        labels = []
        counts = []
        for metric in status_metrics:
            labels.append(metric['call_status'])
            counts.append(metric['total'])
            
        todays_reminders = base_contacts.filter(reminder_date__lte=today).order_by('reminder_date', 'name')
            
        context = {
            'is_admin': False,
            'total_leads': total_leads,
            'labels': labels,
            'counts': counts,
            'todays_reminders': todays_reminders,
            'today': today, 
        }
        return render(request, 'dashboard.html', context)

@never_cache
def landing_page(request):
    return render(request, 'index.html')

@never_cache
@login_required
def get_daily_call_details(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    user_id = request.GET.get('user_id')
    date_str = request.GET.get('date') 
    
    try:
        # FIX: Removed .values() so Django formats the SQLite datetime correctly
        calls = Contact.objects.filter(
            user_id=user_id,
            last_updated__date=date_str
        ).exclude(call_status='Pending').order_by('-last_updated')
        
        call_list = []
        for call in calls:
            # Convert the proper model datetime to local time (IST)
            local_time = timezone.localtime(call.last_updated)
            
            call_list.append({
                'name': call.name,
                'phone': call.phone_number,
                'status': call.call_status,
                'description': call.description or '-',
                'time': local_time.strftime('%I:%M %p')
            })
            
        return JsonResponse({'success': True, 'calls': call_list})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})