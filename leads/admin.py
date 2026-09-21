from django.contrib import admin
from .models import Contact

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    # The columns that will show up in the admin table
    list_display = ('id', 'name', 'phone_number', 'call_status', 'description')
    
    # Adds a sidebar filter to quickly sort by status
    list_filter = ('call_status',)
    
    # Adds a search bar at the top to search by name or phone
    search_fields = ('name', 'phone_number')
    
    # Makes the ID and Name clickable to edit the record
    list_display_links = ('id', 'name')
    
    # Default sorting (newest first or by ID)
    ordering = ('id',)
    
    # Shows 50 leads per page in the admin panel
    list_per_page = 50