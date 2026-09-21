import pandas as pd
import re
import json
from django.shortcuts import render, redirect
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import Contact

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

            count = 0
            for index, row in df.iterrows():
                clean_name = " ".join(str(row.get('cx name', '')).split())
                clean_phone = " ".join(str(row.get('contact', '')).split())
                
                Contact.objects.create(
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
    
    if cleaned_query:
        escaped_query = re.escape(cleaned_query)
        
        # NEW REGEX: Matches the exact name, and forgives trailing dots, dashes, or spaces
        regex_pattern = rf'^{escaped_query}[^a-zA-Z0-9]*$'
        
        contacts = Contact.objects.filter(
            Q(name__iregex=regex_pattern) | Q(phone_number__icontains=cleaned_query)
        ).order_by('id')
    else:
        contacts = Contact.objects.all().order_by('id')

    paginator = Paginator(contacts, 10)
    page_obj = paginator.get_page(page_number)

    return render(request, 'contact_list.html', {'page_obj': page_obj, 'query': raw_query})

@login_required
def update_contacts(request):
    if request.method == 'POST':
        page = request.POST.get('current_page', 1)
        query = request.POST.get('current_query', '')
        
        for key, value in request.POST.items():
            if key.startswith('status_'):
                contact_id = key.split('_')[1]
                try:
                    contact = Contact.objects.get(id=contact_id)
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

@login_required
def auto_update_contact(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            contact_id = data.get('id')
            contact = Contact.objects.get(id=contact_id)
            
            if 'status' in data:
                contact.call_status = data['status']
            
            if 'description' in data:
                contact.description = data['description']
                
            contact.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@login_required
def export_excel(request):
    contacts = Contact.objects.all().values('name', 'phone_number', 'call_status', 'description')
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

@login_required
def dashboard(request):
    total_leads = Contact.objects.count()
    status_metrics = Contact.objects.values('call_status').annotate(total=Count('call_status'))
    
    labels = []
    counts = []
    
    for metric in status_metrics:
        labels.append(metric['call_status'])
        counts.append(metric['total'])
        
    context = {
        'total_leads': total_leads,
        'labels': labels,
        'counts': counts,
    }
    
    return render(request, 'dashboard.html', context)