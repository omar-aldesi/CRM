from django.urls import path

from core import views


app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("agents/", views.agent_list, name="agent-list"),
    path("agents/create/", views.agent_create, name="agent-create"),
    path("agents/<int:agent_id>/edit/", views.agent_edit, name="agent-edit"),
    path("phones/", views.phone_list, name="phone-list"),
    path("phone-numbers/", views.phone_number_list, name="phone-number-list"),
    path(
        "phone-numbers/<int:phone_number_id>/assign/",
        views.phone_number_assign,
        name="phone-number-assign",
    ),
    path("campaigns/", views.campaign_list, name="campaign-list"),
    path("campaigns/create/", views.campaign_create, name="campaign-create"),
    path(
        "campaigns/<int:campaign_id>/leads/create/",
        views.lead_create,
        name="lead-create",
    ),
    path("leads/", views.lead_list, name="lead-list"),
    path("leads/bulk-assign/", views.lead_bulk_assign, name="lead-bulk-assign"),
    path("leads/<int:lead_id>/", views.lead_detail, name="lead-detail"),
    path("leads/<int:lead_id>/assign/", views.lead_assign, name="lead-assign"),
    path("emails/", views.email_list, name="email-list"),
    path(
        "leads/<int:lead_id>/emails/generate/",
        views.lead_email_generate,
        name="lead-email-generate",
    ),
    path(
        "emails/<int:email_id>/sent/",
        views.email_mark_sent,
        name="email-mark-sent",
    ),
    path(
        "leads/<int:lead_id>/emails/sent/",
        views.lead_email_mark_sent,
        name="lead-email-mark-sent",
    ),
    path(
        "leads/<int:lead_id>/phone/called/",
        views.lead_phone_mark_called,
        name="lead-phone-mark-called",
    ),
    path(
        "leads/<int:lead_id>/calls/",
        views.lead_call_outcome,
        name="lead-call-outcome",
    ),
    path("activity/", views.activity_list, name="activity-list"),
]
