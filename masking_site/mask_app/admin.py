from django.contrib import admin
from .models import ApiKey, UsageLog, ContactMessage

@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'created_at', 'handled')
    list_filter = ('handled', 'created_at')
    search_fields = ('name', 'email', 'message')
    list_editable = ('handled',)

admin.site.register(ApiKey)
admin.site.register(UsageLog)
