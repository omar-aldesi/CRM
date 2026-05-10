from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import AgentPhoneNumber, User


class AgentPhoneNumberInline(admin.TabularInline):
    model = AgentPhoneNumber
    fields = ("phone", "order", "usage_limit", "usage_count")
    extra = 0


@admin.register(User)
class CRMUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("CRM", {"fields": ("role", "initials", "color")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("CRM", {"fields": ("role", "initials", "color")}),
    )
    inlines = [AgentPhoneNumberInline]
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "is_staff",
        "is_superuser",
        "is_active",
    )
    list_filter = UserAdmin.list_filter + ("role",)


@admin.register(AgentPhoneNumber)
class AgentPhoneNumberAdmin(admin.ModelAdmin):
    list_display = (
        "agent",
        "phone",
        "order",
        "usage_count",
        "usage_limit",
        "usage_remaining",
        "is_available",
    )
    list_filter = ("agent",)
    search_fields = ("agent__username", "agent__first_name", "agent__last_name", "phone")
