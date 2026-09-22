from django.db import models

class Contact(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Connected', 'Connected'),
        ('Not Connected', 'Not Connected'),
    ]
    
    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20)
    call_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    description = models.TextField(blank=True, null=True)
    reminder_date = models.DateField(blank=True, null=True)
    def __str__(self):
        return self.name