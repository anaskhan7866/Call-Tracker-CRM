from django.urls import path
from . import views

urlpatterns = [
    path('', views.upload_excel, name='upload_excel'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('contacts/', views.contact_list, name='contact_list'),
    path('contacts/update/', views.update_contacts, name='update_contacts'),
    
    # This is the line your app is looking for:
    path('contacts/auto-update/', views.auto_update_contact, name='auto_update_contact'), 
    
    path('contacts/export/', views.export_excel, name='export_excel'),
]