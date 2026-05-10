from django.contrib import admin

from core.models import ActivityLog, Campaign, EmailLog, Lead, LeadNote

# Register your models here.
admin.site.register(Campaign)
admin.site.register(Lead)
admin.site.register(LeadNote)
admin.site.register(EmailLog)
admin.site.register(ActivityLog)
