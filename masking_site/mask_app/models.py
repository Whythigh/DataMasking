from django.db import models
import uuid


class ApiKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, default='Default key')
    key_hash = models.CharField(max_length=64, unique=True)
    email = models.EmailField()
    tier = models.CharField(max_length=20, default='free')
    active = models.BooleanField(default=True)
    rows_used_this_month = models.IntegerField(default=0)
    stripe_customer_id = models.CharField(max_length=100, blank=True, default='')
    raw_key_temp = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} — {self.name}"

class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} <{self.email}> — {self.created_at:%Y-%m-%d}"


class UsageLog(models.Model):
    api_key = models.ForeignKey(ApiKey, on_delete=models.CASCADE)
    rows_processed = models.IntegerField()
    fields_masked = models.JSONField(default=list)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']