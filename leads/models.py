from django.db import models
from django.contrib.auth.models import User

class Contact(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Connected', 'Connected'),
        ('Not Connected', 'Not Connected'),
    ]
    
    # NEW: Link every contact to the user who uploaded it.
    # We use null=True so your existing older database records don't crash.
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    
    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20)
    call_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    description = models.TextField(blank=True, null=True)
    reminder_date = models.DateField(blank=True, null=True)

    is_active = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.name