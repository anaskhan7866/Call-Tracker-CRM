import logging
import pandas as pd
import re
import json
from datetime import date, timedelta, datetime
from urllib.parse import urlencode, urlsplit
from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render, redirect
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db.models import Q, Count
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from .models import Contact
from django.db.models.functions import TruncDate
from django.contrib.auth.models import User
from django.utils import timezone 
from django.db import transaction

logger = logging.getLogger(__name__)


def _india_phone_tel_uri(phone_number):
    phone_text = str(phone_number).strip()
    if phone_text.endswith('.0') and phone_text[:-2].isdigit():
        phone_text = phone_text[:-2]

    digits = re.sub(r'\D', '', phone_text)
    if digits.startswith('0091'):
        digits = digits[2:]
    if len(digits) == 10:
        digits = f'91{digits}'
    elif len(digits) == 11 and digits.startswith('0'):
        digits = f'91{digits[1:]}'
    elif len(digits) != 12 or not digits.startswith('91'):
        return None

    return f'tel:+{digits}'


def _whatsapp_script_groups(contact):
    call_uri = _india_phone_tel_uri(contact.phone_number)
    if not call_uri:
        return []

    whatsapp_number = call_uri.removeprefix('tel:+')
    scripts = [
        {
            'heading': 'Not Connected',
            'heading_class': 'text-danger',
            'items': [
                {
                    'label': 'Send Missed Call Script',
                    'icon': 'bi-telephone-x',
                    'message': (
                        f'Hi {contact.name}, we tried reaching you regarding our trading platform '
                        'but couldn\'t connect. Please let me know a good time to call back!'
                    ),
                },
            ],
        },
        {
            'heading': 'Connected (Select Market)',
            'heading_class': 'text-success',
            'items': [
                {
                    'label': 'Indian Stock Trading',
                    'icon': 'bi-graph-up-arrow',
                    'message': (
                        f'Hi {contact.name}, as discussed today, here is the information and next '
                        'steps for trading in the Indian stock market.'
                    ),
                },
                {
                    'label': 'Forex Trading',
                    'icon': 'bi-globe-americas',
                    'message': (
                        f'Hi {contact.name}, as discussed today, here is the information regarding '
                        'forex trading.'
                    ),
                },
            ],
        },
    ]

    for group in scripts:
        for item in group['items']:
            item['url'] = f"https://wa.me/{whatsapp_number}?{urlencode({'text': item.pop('message')})}"

    return scripts


@require_POST
def logout_view(request):
    next_url = request.POST.get('next', '')
    parsed_next_url = urlsplit(next_url)
    return_to_contacts = (
        url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
        and parsed_next_url.path == reverse('contact_list')
    )

    if not return_to_contacts:
        last_page = request.session.get('last_page')
        if last_page:
            contact_params = {'page': last_page}
            last_query = request.session.get('last_query', '')
            if last_query:
                contact_params['q'] = last_query
            next_url = f"{reverse('contact_list')}?{urlencode(contact_params)}"
            return_to_contacts = True

    # Log the user out (this flushes the session)
    logout(request)

    # Redirect to the main landing page, but pass the 'next' parameter in the URL
    landing_url = reverse('landing_page')
    if return_to_contacts and next_url:
        return redirect(f"{landing_url}?{urlencode({'next': next_url})}")

    return redirect('landing_page')

@never_cache
@login_required
def upload_excel(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden('Only admins can upload lead files.')

    employees = User.objects.filter(
        is_active=True,
        is_staff=False,
        is_superuser=False,
    ).order_by('username')

    if request.method == 'POST':
        if 'excel_file' not in request.FILES:
            messages.error(request, "Please select a file to upload.")
            return redirect('upload_excel')

        try:
            assigned_user = employees.get(pk=request.POST.get('user_id'))
        except (User.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Please select an active telecaller to assign these contacts to.")
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

            # Fetch existing phone numbers for the selected telecaller into a fast-lookup set.
            existing_phones = set(
                Contact.objects.filter(user=assigned_user).values_list('phone_number', flat=True)
            )
            
            new_contacts = []
            phones_in_current_upload = set() 
            total_rows = 0

            for index, row in df.iterrows():
                clean_name = " ".join(str(row.get('cx name', '')).split())
                clean_phone = " ".join(str(row.get('contact', '')).split())
                
                if not clean_phone:
                    continue
                    
                # FIX: Remove the trailing '.0' that pandas adds when Excel treats phone numbers as floats
                if clean_phone.endswith('.0') and clean_phone[:-2].isdigit():
                    clean_phone = clean_phone[:-2]
                    
                total_rows += 1

                # If phone is completely new (not in DB, and not already seen in this file)
                if clean_phone not in existing_phones and clean_phone not in phones_in_current_upload:
                    new_contacts.append(
                        Contact(
                            user=assigned_user,
                            name=clean_name,
                            phone_number=clean_phone,
                            call_status='Pending'
                        )
                    )
                    phones_in_current_upload.add(clean_phone)
            
            if new_contacts:
                Contact.objects.bulk_create(new_contacts, batch_size=1000)

            added_count = len(new_contacts)
            skipped_count = total_rows - added_count
            
            # Clear the dashboard cache so the admin immediately sees the newly uploaded leads
            cache.delete('admin_dashboard_stats')
            
            messages.success(request, f"Successfully added {added_count} new contacts! ({skipped_count} duplicates skipped).")
            return redirect('contact_list')
            
        except Exception as e:
            messages.error(request, f"Error processing file: {str(e)}")
            return redirect('upload_excel')
        
    return render(request, 'upload.html', {'employees': employees})


@never_cache
@login_required
def contact_list(request):
    if request.user.is_staff or request.user.is_superuser:
        return redirect('dashboard')

    # 1. If user clicks "Clear", wipe the search query and restore the pre-search page
    if 'clear' in request.GET:
        request.session['last_query'] = ''
        request.session['last_status'] = ''
        restore_page = request.session.get('pre_search_page', 1)
        return redirect(f"/contacts/?page={restore_page}")

    # 2. If user clicks "View Contacts" from navbar, restore their exact last state
    if not request.GET and 'last_page' in request.session:
        last_page = request.session.get('last_page', 1)
        last_query = request.session.get('last_query', '')
        last_status = request.session.get('last_status', '')
        
        redirect_url = f"/contacts/?page={last_page}"
        if last_query:
            redirect_url += f"&q={last_query}"
        if last_status:
            redirect_url += f"&status={last_status}"
        return redirect(redirect_url)

    # 3. Process the current request
    raw_query = request.GET.get('q', '')
    page_number = request.GET.get('page', 1)
    status_filter = 'not_connected' if request.GET.get('status') == 'not_connected' else ''
    
    # If they are NOT searching right now, save this page as the safe return point
    if not raw_query:
        request.session['pre_search_page'] = page_number
        
    request.session['last_query'] = raw_query
    request.session['last_page'] = page_number
    request.session['last_status'] = status_filter
    
    cleaned_query = " ".join(raw_query.split())
    
    # Admins see all contacts, normal users only see their own
    if request.user.is_staff or request.user.is_superuser:
        base_contacts = Contact.objects.select_related('user').all()
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

    if status_filter == 'not_connected':
        contacts = contacts.filter(call_status='Not Connected')

    paginator = Paginator(contacts, 10)
    page_obj = paginator.get_page(page_number)
    for contact in page_obj:
        contact.call_uri = _india_phone_tel_uri(contact.phone_number)
        contact.whatsapp_script_groups = _whatsapp_script_groups(contact)

    return render(request, 'contact_list.html', {
        'page_obj': page_obj,
        'query': raw_query,
        'status_filter': status_filter,
    })

@never_cache
@login_required
def update_contacts(request):
    if request.method == 'POST':
        page = request.POST.get('current_page', 1)
        query = request.POST.get('current_query', '')
        
        # Determine base queryset for security
        if request.user.is_staff or request.user.is_superuser:
            base_contacts = Contact.objects.select_related('user').all()
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
                except (Contact.DoesNotExist, ValueError):
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
    if request.method != 'POST':
        return JsonResponse(
            {'success': False, 'error': 'Invalid request method.'},
            status=405,
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON request.'}, status=400)

    if not isinstance(data, dict) or not data.get('id'):
        return JsonResponse({'success': False, 'error': 'A contact ID is required.'}, status=400)

    try:
        contact_id = int(data['id'])
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid contact ID.'}, status=400)

    try:
        # Keep contact access scoped to the signed-in caller.
        if request.user.is_staff or request.user.is_superuser:
            contact = Contact.objects.get(id=contact_id)
        else:
            contact = Contact.objects.get(id=contact_id, user=request.user, is_active=True)

        if 'status' in data:
            contact.call_status = data['status']

        if 'description' in data:
            contact.description = data['description']

        if 'reminder_days' in data:
            reminder_days = data['reminder_days']
            if type(reminder_days) is not int or reminder_days not in (1, 3, 7):
                return JsonResponse({'success': False, 'error': 'Invalid reminder delay.'}, status=400)
            contact.reminder_date = timezone.localdate() + timedelta(days=reminder_days)
        elif 'reminder_date' in data:
            reminder_date = data['reminder_date']
            contact.reminder_date = reminder_date if reminder_date else None

        contact.save()
        return JsonResponse({
            'success': True,
            'reminder_date': contact.reminder_date.isoformat() if contact.reminder_date else '',
        })
    except Contact.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Contact not found.'}, status=404)
    except ValidationError:
        return JsonResponse({'success': False, 'error': 'Invalid contact data.'}, status=400)
    except Exception:
        logger.exception('Unexpected error updating contact via AJAX')
        return JsonResponse(
            {'success': False, 'error': 'Unable to update contact.'},
            status=500,
        )

@never_cache
@login_required
def export_excel(request):
    if request.user.is_staff or request.user.is_superuser:
        contacts = Contact.objects.select_related('user').all()
    else:
        contacts = Contact.objects.filter(user=request.user, is_active=True)
        
    # FIX: Ensure export respects the user's active search and status filters
    raw_query = request.GET.get('q', '')
    status_filter = request.GET.get('status', '')
    
    cleaned_query = " ".join(raw_query.split())
    if cleaned_query:
        escaped_query = re.escape(cleaned_query)
        regex_pattern = rf'^{escaped_query}[^a-zA-Z0-9]*$'
        contacts = contacts.filter(
            Q(name__iregex=regex_pattern) | Q(phone_number__icontains=cleaned_query)
        ).order_by('id')
        
    if status_filter == 'not_connected':
        contacts = contacts.filter(call_status='Not Connected')
        
    contacts = contacts.values('name', 'phone_number', 'call_status', 'description')
    
    # Initialize DataFrame with explicit columns so headers are always present, even if empty
    df = pd.DataFrame(list(contacts), columns=['name', 'phone_number', 'call_status', 'description'])
    
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
    start_of_today = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    end_of_today = start_of_today + timedelta(days=1)

    if request.user.is_staff or request.user.is_superuser:
        # --- ADMIN DASHBOARD ---
        
        search_query = request.GET.get('q', '').strip()
        search_results = None
        
        if search_query:
            search_results = Contact.objects.filter(
                # Matches if the name starts with the query OR if the query appears as a separate word (with a space)
                Q(name__istartswith=search_query) | 
                Q(name__icontains=f" {search_query}") | 
                Q(phone_number__icontains=search_query)
            ).select_related('user').order_by('-last_updated')[:50]

        # Use caching for expensive admin dashboard queries to avoid 3+ second page loads
        # Cache key based on whether we are searching (we don't cache search results)
        cache_key = 'admin_dashboard_stats'
        
        # Bypass cache in local development so you can see real-time changes instantly
        if settings.DEBUG:
            cached_data = None
        else:
            cached_data = cache.get(cache_key)

        if cached_data is None:
            # Calculate company-wide aggregate totals
            company_stats = Contact.objects.aggregate(
                total_leads=Count('id'),
                pending=Count('id', filter=Q(call_status='Pending')),
                total_called=Count('id', filter=~Q(call_status='Pending')),
                called_today=Count('id', filter=~Q(call_status='Pending') & Q(last_updated__gte=start_of_today) & Q(last_updated__lt=end_of_today)),
                connected=Count('id', filter=Q(call_status='Connected')),
                not_connected=Count('id', filter=Q(call_status='Not Connected'))
            )

            # Employee metrics
            employees = list(User.objects.filter(is_superuser=False, is_staff=False).annotate(
                total_leads=Count('contact'),
                pending=Count('contact', filter=Q(contact__call_status='Pending')),
                total_called=Count('contact', filter=~Q(contact__call_status='Pending')),
                called_today=Count('contact', filter=~Q(contact__call_status='Pending') & Q(contact__last_updated__gte=start_of_today) & Q(contact__last_updated__lt=end_of_today)),
                connected=Count('contact', filter=Q(contact__call_status='Connected')),
                not_connected=Count('contact', filter=Q(contact__call_status='Not Connected'))
            ).order_by('username'))
            
            # Daily history (last 30 days only to prevent long query times as DB grows)
            thirty_days_ago = start_of_today - timedelta(days=30)
            daily_history_raw = Contact.objects.filter(
                ~Q(call_status='Pending'), 
                last_updated__gte=thirty_days_ago
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

            cached_data = {
                'company_stats': company_stats,
                'employees': employees,
            }
            # Cache the heavily processed data for 60 seconds.
            cache.set(cache_key, cached_data, 60)

        context = {
            'is_admin': True,
            'employees': cached_data['employees'],
            'search_query': search_query,
            'search_results': search_results,
            'company_stats': cached_data['company_stats'],
        }
        return render(request, 'dashboard.html', context)
        
    else:
        # --- TELECALLER DASHBOARD ---
        base_contacts = Contact.objects.filter(user=request.user, is_active=True)
        total_leads = base_contacts.count()
        status_counts = base_contacts.aggregate(
            pending=Count('id', filter=Q(call_status='Pending')),
            connected=Count('id', filter=Q(call_status='Connected')),
            not_connected=Count('id', filter=Q(call_status='Not Connected')),
        )
            
        todays_reminders = base_contacts.filter(reminder_date__lte=today).order_by('reminder_date', 'name')
            
        context = {
            'is_admin': False,
            'total_leads': total_leads,
            'pending_leads': status_counts['pending'],
            'connected_leads': status_counts['connected'],
            'not_connected_leads': status_counts['not_connected'],
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
        user_id = int(user_id)
        call_date = date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid user or date.'}, status=400)
    
    try:
        start_of_day = timezone.make_aware(datetime.combine(call_date, datetime.min.time()))
        end_of_day = start_of_day + timedelta(days=1)

        # FIX: Removed .values() so Django formats the SQLite datetime correctly
        # FIX: Use range instead of __date to allow PostgreSQL to use the database index
        calls = Contact.objects.filter(
            user_id=user_id,
            last_updated__gte=start_of_day,
            last_updated__lt=end_of_day
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
    except Exception:
        logger.exception('Unexpected error loading daily call details via AJAX')
        return JsonResponse(
            {'success': False, 'error': 'Unable to load call details.'},
            status=500,
        )