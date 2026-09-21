from django.urls import path
from . import views
from leads import views
from django.contrib import admin
urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.landing_page, name='landing_page'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('contacts/', views.contact_list, name='contact_list'),
    path('contacts/update/', views.update_contacts, name='update_contacts'),
    
    # This is the line your app is looking for:
    path('contacts/auto-update/', views.auto_update_contact, name='auto_update_contact'), 
    path('upload_excel/', views.upload_view, name='upload_excel'),
    path('contacts/export/', views.export_excel, name='export_excel'),
]