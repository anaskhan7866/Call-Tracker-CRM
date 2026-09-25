from django.contrib import admin
from .models import Contact

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    # Added 'user' and 'is_active' to the display
    list_display = ('id', 'name', 'phone_number', 'call_status', 'user', 'is_active')
    
    # Added 'is_active' and 'user' to the sidebar filters
    list_filter = ('call_status', 'is_active', 'user')
    
    search_fields = ('name', 'phone_number')
    list_display_links = ('id', 'name')
    ordering = ('id',)
    list_per_page = 50