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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} — {self.name}"

class UsageLog(models.Model):
    api_key = models.ForeignKey(ApiKey, on_delete=models.CASCADE)
    rows_processed = models.IntegerField()
    fields_masked = models.JSONField(default=list)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']