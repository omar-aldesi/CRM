from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import AgentPhoneNumber, User
from core.forms import (
    AgentCreateForm,
    AgentUpdateForm,
    CallOutcomeForm,
    CampaignForm,
    EmailGenerateForm,
    LeadAssignmentForm,
    ManualLeadForm,
    PhoneNumberAssignmentForm,
    PhoneNumberCreateForm,
)
from core.models import ActivityLog, Campaign, EmailLog, Lead, LeadNote
from core.services import (
    assign_lead,
    create_manual_lead,
    generate_email_for_lead,
    mark_lead_email_sent,
    mark_email_sent,
    mark_phone_called,
    record_call_outcome,
)
import openpyxl
from django.db import transaction


@login_required
@require_GET
def dashboard(request):
    permission_response = _require_admin_view(request, agent_redirect="core:lead-list")
    if permission_response:
        return permission_response

    leads = _lead_queryset_for_user(request.user)
    activity = _activity_queryset_for_user(request.user)[:10]
    stats = _dashboard_stats(leads)

    if _is_admin(request.user):
        stats["active_campaigns"] = Campaign.objects.filter(is_active=True).count()
        stats["assigned_leads"] = leads.filter(assigned_to__isnull=False).count()

    if _wants_json(request):
        return JsonResponse(
            {
                "stats": stats,
                "recent_activity": [_serialize_activity(log) for log in activity],
            }
        )

    return render(
        request,
        "core/dashboard.html",
        {
            "page_title": "Dashboard",
            "stats": stats,
            "recent_activity": activity,
            "status_rows": _status_rows(leads),
        },
    )


@login_required
@require_GET
def agent_list(request):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    agents = (
        User.objects.filter(role=User.Role.AGENT)
        .annotate(
            total_leads=Count("assigned_leads"),
            interested_leads=Count(
                "assigned_leads",
                filter=Q(assigned_leads__status=Lead.Status.INTERESTED),
            ),
            demo_leads=Count(
                "assigned_leads",
                filter=Q(assigned_leads__status=Lead.Status.DEMO_BOOKED),
            ),
        )
        .order_by("username")
    )

    if _wants_json(request):
        return JsonResponse({"agents": [_serialize_agent(agent) for agent in agents]})

    return render(
        request,
        "core/agent_list.html",
        {
            "page_title": "Agents",
            "agents": agents,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def agent_create(request):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    if request.method == "GET":
        form = AgentCreateForm()
        if _wants_json(request):
            return JsonResponse({"fields": list(AgentCreateForm.base_fields)})
        return render(
            request,
            "core/agent_form.html",
            {
                "page_title": "New Agent",
                "form": form,
                "form_title": "New Agent",
                "form_subtitle": "Agents log in normally and only see leads assigned to them.",
                "submit_label": "Create agent",
            },
        )

    form = AgentCreateForm(request.POST)
    if form.is_valid():
        agent = form.save()
        if _wants_json(request):
            return JsonResponse({"agent": _serialize_agent(agent)}, status=201)
        messages.success(request, "Agent created.")
        return redirect("core:agent-list")

    if _wants_json(request):
        return _form_error_response(form)

    return render(
        request,
        "core/agent_form.html",
        {
            "page_title": "New Agent",
            "form": form,
            "form_title": "New Agent",
            "form_subtitle": "Agents log in normally and only see leads assigned to them.",
            "submit_label": "Create agent",
        },
        status=400,
    )


@login_required
@require_http_methods(["GET", "POST"])
def agent_edit(request, agent_id):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    agent = get_object_or_404(User.objects.filter(role=User.Role.AGENT), pk=agent_id)

    if request.method == "GET":
        form = AgentUpdateForm(instance=agent)
        if _wants_json(request):
            return JsonResponse(
                {
                    "agent": _serialize_agent(agent),
                    "fields": list(AgentUpdateForm.base_fields),
                }
            )
        return render(
            request,
            "core/agent_form.html",
            {
                "page_title": f"Edit {agent}",
                "form": form,
                "form_title": "Edit Agent",
                "form_subtitle": str(agent),
                "submit_label": "Save changes",
                "agent": agent,
                "agent_stats": _agent_stats(agent),
                "phone_numbers": agent.phone_numbers.order_by("order", "phone"),
            },
        )

    form = AgentUpdateForm(request.POST, instance=agent)
    if form.is_valid():
        agent = form.save()
        if _wants_json(request):
            return JsonResponse({"agent": _serialize_agent(agent)})
        messages.success(request, "Agent updated.")
        return redirect("core:agent-list")

    if _wants_json(request):
        return _form_error_response(form)

    return render(
        request,
        "core/agent_form.html",
        {
            "page_title": f"Edit {agent}",
            "form": form,
            "form_title": "Edit Agent",
            "form_subtitle": str(agent),
            "submit_label": "Save changes",
            "agent": agent,
            "agent_stats": _agent_stats(agent),
            "phone_numbers": agent.phone_numbers.order_by("order", "phone"),
        },
        status=400,
    )


@login_required
@require_http_methods(["GET", "POST"])
def phone_number_list(request):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    if request.method == "POST":
        form = PhoneNumberCreateForm(request.POST)
        if form.is_valid():
            phone_number = form.save()
            if _wants_json(request):
                return JsonResponse(
                    {"phone_number": _serialize_phone_number(phone_number)},
                    status=201,
                )
            messages.success(request, "Phone number saved.")
            return redirect("core:phone-number-list")
    else:
        form = PhoneNumberCreateForm()

    phone_numbers = AgentPhoneNumber.objects.select_related("agent").order_by(
        "agent__username",
        "order",
        "phone",
    )
    phone_stats = {
        "total": phone_numbers.count(),
        "assigned": phone_numbers.filter(agent__isnull=False).count(),
        "available": phone_numbers.filter(usage_count__lt=F("usage_limit")).count(),
        "limited": phone_numbers.filter(usage_count__gte=F("usage_limit")).count(),
    }

    if _wants_json(request) and request.method == "POST" and form.errors:
        return _form_error_response(form)

    if _wants_json(request):
        return JsonResponse(
            {
                "phone_numbers": [
                    _serialize_phone_number(phone_number)
                    for phone_number in phone_numbers
                ]
            },
            status=400 if request.method == "POST" and form.errors else 200,
        )

    return render(
        request,
        "core/phone_number_list.html",
        {
            "page_title": "Phone Numbers",
            "form": form,
            "phone_numbers": phone_numbers,
            "phone_stats": phone_stats,
            "agents": _agent_queryset(),
            "assignment_form": PhoneNumberAssignmentForm(),
        },
        status=400 if request.method == "POST" and form.errors else 200,
    )


@login_required
@require_POST
def phone_number_assign(request, phone_number_id):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    phone_number = get_object_or_404(AgentPhoneNumber, pk=phone_number_id)
    form = PhoneNumberAssignmentForm(request.POST, instance=phone_number)

    if form.is_valid():
        phone_number = form.save()
        if _wants_json(request):
            return JsonResponse({"phone_number": _serialize_phone_number(phone_number)})
        messages.success(request, "Phone assignment updated.")
        return redirect("core:phone-number-list")

    if _wants_json(request):
        return _form_error_response(form)

    messages.error(request, "Phone assignment could not be saved.")
    return redirect("core:phone-number-list")


@login_required
@require_GET
def phone_list(request):
    return redirect("core:lead-list")


@login_required
@require_GET
def campaign_list(request):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    campaigns = Campaign.objects.annotate(leads_count=Count("leads")).order_by(
        "-created_at"
    )

    if _wants_json(request):
        return JsonResponse(
            {"campaigns": [_serialize_campaign(campaign) for campaign in campaigns]}
        )

    return render(
        request,
        "core/campaign_list.html",
        {
            "page_title": "Campaigns",
            "campaigns": campaigns,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def campaign_create(request):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    if request.method == "GET":
        form = CampaignForm()
        if _wants_json(request):
            return JsonResponse({"fields": list(CampaignForm.base_fields)})
        return render(
            request,
            "core/campaign_form.html",
            {"page_title": "New Campaign", "form": form},
        )

    form = CampaignForm(request.POST)
    if not form.is_valid():
        if _wants_json(request):
            return _form_error_response(form)
        return render(
            request,
            "core/campaign_form.html",
            {"page_title": "New Campaign", "form": form},
            status=400,
        )

    campaign = form.save()
    if _wants_json(request):
        return JsonResponse({"campaign": _serialize_campaign(campaign)}, status=201)

    messages.success(request, "Campaign created.")
    return redirect("core:campaign-list")


@login_required
@require_GET
def lead_list(request):
    leads = (
        _filtered_leads_for_request(request)
        .select_related(
            "campaign",
            "assigned_to",
        )
        .annotate(notes_count=Count("notes", distinct=True))
    )

    if _wants_json(request):
        return JsonResponse({"leads": [_serialize_lead(lead) for lead in leads[:100]]})

    return render(
        request,
        "core/lead_list.html",
        {
            "page_title": "Leads",
            "leads": leads[:200],
            "campaigns": Campaign.objects.filter(is_active=True).order_by("name"),
            "agents": _agent_queryset(),
            "status_choices": Lead.Status.choices,
            "tier_choices": Lead.Tier.choices,
            "filters": request.GET,
        },
    )


@login_required
@require_POST
def lead_bulk_assign(request):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    next_url = _next_url_from_request(request)
    raw_lead_ids = request.POST.getlist("lead_ids")
    form = LeadAssignmentForm(request.POST)

    if not raw_lead_ids:
        if _wants_json(request):
            return JsonResponse(
                {"errors": {"lead_ids": [{"message": "Select at least one lead."}]}},
                status=400,
            )
        messages.error(request, "Select at least one lead.")
        return redirect(next_url or "core:lead-list")

    try:
        lead_ids = [int(lead_id) for lead_id in raw_lead_ids]
    except (TypeError, ValueError):
        if _wants_json(request):
            return JsonResponse(
                {"errors": {"lead_ids": [{"message": "Selected leads are invalid."}]}},
                status=400,
            )
        messages.error(request, "Selected leads are invalid.")
        return redirect(next_url or "core:lead-list")

    if not form.is_valid() or form.cleaned_data["assigned_to"] is None:
        if _wants_json(request):
            return JsonResponse(
                {
                    "errors": {
                        "assigned_to": [
                            {"message": "Choose an agent for bulk assignment."}
                        ]
                    }
                },
                status=400,
            )
        messages.error(request, "Choose an agent for bulk assignment.")
        return redirect(next_url or "core:lead-list")

    agent = form.cleaned_data["assigned_to"]
    leads = list(Lead.objects.filter(pk__in=lead_ids).select_related("assigned_to"))
    if not leads:
        if _wants_json(request):
            return JsonResponse(
                {
                    "errors": {
                        "lead_ids": [{"message": "No matching leads were found."}]
                    }
                },
                status=400,
            )
        messages.error(request, "No matching leads were found.")
        return redirect(next_url or "core:lead-list")

    assigned_count = 0

    for lead in leads:
        before_agent_id = lead.assigned_to_id
        assign_lead(lead=lead, agent=agent, assigned_by=request.user)
        if before_agent_id != agent.id:
            assigned_count += 1

    if _wants_json(request):
        return JsonResponse(
            {
                "assigned_count": assigned_count,
                "selected_count": len(leads),
                "agent": _serialize_user(agent),
            }
        )

    if assigned_count:
        messages.success(
            request,
            f"Assigned {assigned_count} lead{'' if assigned_count == 1 else 's'} to {agent}.",
        )
    else:
        messages.success(request, f"Selected leads were already assigned to {agent}.")

    return redirect(next_url or "core:lead-list")


@login_required
@require_POST
def lead_import(request):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    campaign_id = request.POST.get("campaign_id")
    if not campaign_id:
        messages.error(request, "Select a campaign before importing leads.")
        return redirect("core:lead-list")

    campaign = get_object_or_404(Campaign, pk=campaign_id)

    if "excel_file" not in request.FILES:
        messages.error(request, "Please select a file to import.")
        return redirect("core:lead-list")

    excel_file = request.FILES["excel_file"]

    workbook = openpyxl.load_workbook(excel_file)
    sheet = workbook.active

    header_skipped = False

    def clean(value):
        return str(value).strip() if value is not None else ""

    try:
        with transaction.atomic():
            for row in sheet.iter_rows(values_only=True):

                if all(cell is None for cell in row):
                    continue

                if not header_skipped:
                    header_skipped = True
                    continue

                name = row[0]
                phone = row[1]
                city = row[2]
                state = row[3]
                full_location = row[4]
                rating = row[5]
                num_reviews = row[6]
                website = row[7]

                Lead.objects.get_or_create(
                    campaign=campaign,
                    business_name=clean(name),
                    defaults={
                        "phone": clean(phone),
                        "city": clean(city),
                        "state": clean(state),
                        "score": clean(rating),
                        "num_of_reviews": clean(num_reviews),
                        "website": clean(website),
                        "location": clean(full_location),
                    },
                )

    except Exception as e:
        messages.error(request, f"Import failed, nothing was saved. Reason: {str(e)}")
        return redirect("core:lead-list")

    messages.success(request, "Leads imported successfully.")
    return redirect("core:lead-list")


@login_required
@require_http_methods(["GET", "POST"])
def lead_create(request, campaign_id):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    campaign = get_object_or_404(Campaign, pk=campaign_id)

    if request.method == "GET":
        form = ManualLeadForm()
        if _wants_json(request):
            return JsonResponse(
                {
                    "campaign": _serialize_campaign(campaign),
                    "fields": list(ManualLeadForm.base_fields),
                }
            )
        return render(
            request,
            "core/lead_form.html",
            {
                "page_title": "New Lead",
                "campaign": campaign,
                "form": form,
            },
        )

    form = ManualLeadForm(request.POST)
    if form.is_valid():
        try:
            lead = create_manual_lead(
                campaign=campaign,
                created_by=request.user,
                **form.cleaned_data,
            )
        except IntegrityError:
            form.add_error("phone", "Lead phone already exists in this campaign.")
        except Exception as error:
            return _service_error_response(error)
        else:
            if _wants_json(request):
                return JsonResponse({"lead": _serialize_lead(lead)}, status=201)
            messages.success(request, "Lead added.")
            return redirect("core:lead-detail", lead_id=lead.id)

    if _wants_json(request):
        return _form_error_response(form)

    return render(
        request,
        "core/lead_form.html",
        {
            "page_title": "New Lead",
            "campaign": campaign,
            "form": form,
        },
        status=400,
    )


@login_required
@require_http_methods(["GET", "POST"])
def lead_edit(request, lead_id):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    lead = get_object_or_404(Lead, pk=lead_id)
    campaign = lead.campaign

    if request.method == "GET":
        form = ManualLeadForm(
            initial={
                "assigned_to": lead.assigned_to,
                "business_name": lead.business_name,
                "phone": lead.phone,
                "email": lead.email,
                "city": lead.city,
                "owner_name": lead.owner_name,
                "business_type": lead.business_type,
                "score": lead.score,
                "tier": lead.tier,
                "status": lead.status,
                "hook": lead.hook,
                "warning": lead.warning,
            }
        )
        if _wants_json(request):
            return JsonResponse(
                {
                    "lead": _serialize_lead(lead),
                    "fields": list(ManualLeadForm.base_fields),
                }
            )
        return render(
            request,
            "core/lead_form.html",
            {
                "page_title": "Edit Lead",
                "submit_text": "Save changes",
                "campaign": campaign,
                "form": form,
            },
        )

    form = ManualLeadForm(request.POST)
    if form.is_valid():
        try:
            lead.assigned_to = form.cleaned_data["assigned_to"]
            lead.business_name = form.cleaned_data["business_name"].strip()
            lead.phone = form.cleaned_data["phone"].strip()
            lead.email = form.cleaned_data["email"].strip()
            lead.city = form.cleaned_data["city"].strip()
            lead.owner_name = form.cleaned_data["owner_name"].strip()
            lead.business_type = form.cleaned_data["business_type"].strip() or "Roofing"
            lead.score = form.cleaned_data["score"].strip()
            lead.tier = form.cleaned_data["tier"]
            lead.status = form.cleaned_data["status"]
            lead.hook = form.cleaned_data["hook"].strip()
            lead.warning = form.cleaned_data["warning"].strip()
            lead.save()
        except IntegrityError:
            form.add_error("phone", "Lead phone already exists in this campaign.")
        except Exception as error:
            return _service_error_response(error)
        else:
            if _wants_json(request):
                return JsonResponse({"lead": _serialize_lead(lead)})
            messages.success(request, "Lead updated.")
            return redirect("core:lead-detail", lead_id=lead.id)

    if _wants_json(request):
        return _form_error_response(form)

    return render(
        request,
        "core/lead_form.html",
        {
            "page_title": "Edit Lead",
            "submit_text": "Save changes",
            "campaign": campaign,
            "form": form,
        },
        status=400,
    )


@login_required
@require_GET
def lead_detail(request, lead_id):
    lead = get_object_or_404(
        _lead_queryset_for_user(request.user).select_related("campaign", "assigned_to"),
        pk=lead_id,
    )

    notes = lead.notes.select_related("agent")
    phone_notes = notes.filter(channel=LeadNote.Channel.PHONE)
    email_notes = notes.filter(channel=LeadNote.Channel.EMAIL)
    emails = lead.emails.select_related("created_by").order_by("-created_at")
    activity = ActivityLog.objects.none()
    if _is_admin(request.user):
        activity = lead.activities.select_related("user", "lead").order_by(
            "-created_at"
        )[:25]

    if _wants_json(request):
        return JsonResponse(
            {
                "lead": _serialize_lead(lead),
                "notes": [_serialize_note(note) for note in notes],
                "phone_notes": [_serialize_note(note) for note in phone_notes],
                "email_notes": [_serialize_note(note) for note in email_notes],
                "emails": [_serialize_email(email) for email in emails],
                "activity": [_serialize_activity(log) for log in activity],
            }
        )

    return render(
        request,
        "core/lead_detail.html",
        {
            "page_title": lead.business_name,
            "lead": lead,
            "notes": notes,
            "phone_notes": phone_notes,
            "email_notes": email_notes,
            "emails": emails,
            "activity": activity,
            "email_form": EmailGenerateForm(),
            "call_form": CallOutcomeForm(agent=request.user),
            "assignment_form": LeadAssignmentForm(
                initial={"assigned_to": lead.assigned_to}
            ),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def lead_assign(request, lead_id):
    permission_response = _require_admin(request.user)
    if permission_response:
        return permission_response

    next_url = _next_url_from_request(request)
    lead = get_object_or_404(
        Lead.objects.select_related("campaign", "assigned_to"),
        pk=lead_id,
    )

    if request.method == "GET":
        form = LeadAssignmentForm(initial={"assigned_to": lead.assigned_to})
        if _wants_json(request):
            return JsonResponse(
                {
                    "lead": _serialize_lead(lead),
                    "fields": list(LeadAssignmentForm.base_fields),
                }
            )
        return render(
            request,
            "core/lead_assign.html",
            {
                "page_title": "Assign Lead",
                "lead": lead,
                "form": form,
                "next_url": next_url,
            },
        )

    form = LeadAssignmentForm(request.POST)
    if form.is_valid():
        try:
            assign_lead(
                lead=lead,
                agent=form.cleaned_data["assigned_to"],
                assigned_by=request.user,
            )
        except Exception as error:
            return _service_error_response(error)
        else:
            lead.refresh_from_db()
            if _wants_json(request):
                return JsonResponse({"lead": _serialize_lead(lead)})
            messages.success(request, "Lead assignment updated.")
            if next_url:
                return redirect(next_url)
            return redirect("core:lead-detail", lead_id=lead.id)

    if _wants_json(request):
        return _form_error_response(form)

    return render(
        request,
        "core/lead_assign.html",
        {
            "page_title": "Assign Lead",
            "lead": lead,
            "form": form,
            "next_url": next_url,
        },
        status=400,
    )


@login_required
@require_GET
def email_list(request):
    leads = (
        _filtered_leads_for_request(request)
        .exclude(email="")
        .select_related(
            "campaign",
            "assigned_to",
        )
    )

    rows = [
        {
            "lead": lead,
            "latest_email": lead.emails.order_by("-created_at").first(),
            "latest_note": lead.notes.select_related("agent")
            .order_by("-created_at")
            .first(),
        }
        for lead in leads[:200]
    ]

    if _wants_json(request):
        return JsonResponse({"leads": [_serialize_lead(row["lead"]) for row in rows]})

    return render(
        request,
        "core/email_list.html",
        {
            "page_title": "Emails",
            "rows": rows,
            "agents": _agent_queryset(),
            "tier_choices": Lead.Tier.choices,
            "filters": request.GET,
            "stats": {
                "sent": leads.filter(email_sent=True).count(),
                "not_sent": leads.filter(email_sent=False).count(),
            },
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def lead_email_generate(request, lead_id):
    lead = get_object_or_404(
        _lead_queryset_for_user(request.user).select_related("campaign", "assigned_to"),
        pk=lead_id,
    )
    next_url = _next_url_from_request(request)

    if request.method == "GET":
        form = EmailGenerateForm()
        if _wants_json(request):
            return JsonResponse(
                {
                    "lead": _serialize_lead(lead),
                    "fields": list(EmailGenerateForm.base_fields),
                }
            )
        return render(
            request,
            "core/email_form.html",
            {
                "page_title": "Generate Email",
                "lead": lead,
                "form": form,
                "next_url": next_url,
            },
        )

    form = EmailGenerateForm(request.POST)
    if form.is_valid():
        try:
            email_log = generate_email_for_lead(
                lead=lead,
                created_by=request.user,
                **form.cleaned_data,
            )
        except Exception as error:
            return _service_error_response(error)
        else:
            if _wants_json(request):
                return JsonResponse({"email": _serialize_email(email_log)}, status=201)
            messages.success(request, "Email generated.")
            if next_url:
                return redirect(next_url)
            return redirect("core:lead-detail", lead_id=lead.id)

    if _wants_json(request):
        return _form_error_response(form)

    return render(
        request,
        "core/email_form.html",
        {
            "page_title": "Generate Email",
            "lead": lead,
            "form": form,
            "next_url": next_url,
        },
        status=400,
    )


@login_required
@require_POST
def email_mark_sent(request, email_id):
    email_log = get_object_or_404(
        _email_queryset_for_user(request.user).select_related(
            "lead",
            "created_by",
            "lead__campaign",
            "lead__assigned_to",
        ),
        pk=email_id,
    )

    try:
        email_log = mark_email_sent(
            email_log=email_log,
            sent_by=request.user,
            note=request.POST.get("note", ""),
        )
    except Exception as error:
        return _service_error_response(error)

    email_log.lead.refresh_from_db()
    if _wants_json(request):
        return JsonResponse(
            {
                "email": _serialize_email(email_log),
                "lead": _serialize_lead(email_log.lead),
            }
        )

    messages.success(request, "Email marked as sent.")
    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect("core:lead-detail", lead_id=email_log.lead_id)


@login_required
@require_POST
def lead_email_mark_sent(request, lead_id):
    lead = get_object_or_404(
        _lead_queryset_for_user(request.user).select_related("campaign", "assigned_to"),
        pk=lead_id,
    )

    try:
        email_log = mark_lead_email_sent(
            lead=lead,
            sent_by=request.user,
            note=request.POST.get("note", ""),
        )
    except Exception as error:
        return _service_error_response(error)

    lead.refresh_from_db()
    if _wants_json(request):
        return JsonResponse(
            {
                "email": _serialize_email(email_log),
                "lead": _serialize_lead(lead),
            }
        )

    messages.success(request, "Email marked as done.")
    next_url = _next_url_from_request(request)
    if next_url:
        return redirect(next_url)
    return redirect("core:lead-detail", lead_id=lead.id)


@login_required
@require_POST
def lead_phone_mark_called(request, lead_id):
    permission_response = _require_agent(request.user)
    if permission_response:
        return permission_response

    lead = get_object_or_404(
        _lead_queryset_for_user(request.user).select_related("campaign", "assigned_to"),
        pk=lead_id,
    )

    try:
        lead = mark_phone_called(lead=lead, called_by=request.user)
    except Exception as error:
        return _service_error_response(error)

    lead.refresh_from_db()
    if _wants_json(request):
        return JsonResponse({"lead": _serialize_lead(lead)})

    messages.success(request, "Phone marked as called.")
    next_url = _next_url_from_request(request)
    if next_url:
        return redirect(next_url)
    return redirect("core:lead-detail", lead_id=lead.id)


@login_required
@require_http_methods(["GET", "POST"])
def lead_call_outcome(request, lead_id):
    permission_response = _require_agent(request.user)
    if permission_response:
        return permission_response

    next_url = _next_url_from_request(request)
    lead = get_object_or_404(
        _lead_queryset_for_user(request.user).select_related("campaign", "assigned_to"),
        pk=lead_id,
    )

    if request.method == "GET":
        form = CallOutcomeForm(agent=request.user)
        if _wants_json(request):
            return JsonResponse(
                {
                    "lead": _serialize_lead(lead),
                    "fields": list(CallOutcomeForm.base_fields),
                }
            )
        return render(
            request,
            "core/call_form.html",
            {
                "page_title": "Add Phone Note",
                "lead": lead,
                "form": form,
                "next_url": next_url,
            },
        )

    form = CallOutcomeForm(request.POST, agent=request.user)
    if form.is_valid():
        try:
            lead_note = record_call_outcome(
                lead=lead,
                agent=request.user,
                note=form.cleaned_data["note"],
                outcome=form.cleaned_data["outcome"],
                agent_phone_number=form.cleaned_data["agent_phone_number"],
            )
        except Exception as error:
            return _service_error_response(error)
        else:
            lead.refresh_from_db()
            if _wants_json(request):
                return JsonResponse(
                    {
                        "lead": _serialize_lead(lead),
                        "note": _serialize_note(lead_note),
                    },
                    status=201,
                )
            messages.success(request, "Call outcome saved.")
            if next_url:
                return redirect(next_url)
            return redirect("core:lead-detail", lead_id=lead.id)

    if _wants_json(request):
        return _form_error_response(form)

    return render(
        request,
        "core/call_form.html",
        {
            "page_title": "Add Phone Note",
            "lead": lead,
            "form": form,
            "next_url": next_url,
        },
        status=400,
    )


@login_required
@require_GET
def activity_list(request):
    permission_response = _require_admin_view(request, agent_redirect="core:lead-list")
    if permission_response:
        return permission_response

    activity = _activity_queryset_for_user(request.user)[:200]

    if _wants_json(request):
        return JsonResponse(
            {"activity": [_serialize_activity(log) for log in activity]}
        )

    return render(
        request,
        "core/activity_list.html",
        {"page_title": "Activity", "activity": activity},
    )


def _filtered_leads_for_request(request):
    leads = _lead_queryset_for_user(request.user)

    search = request.GET.get("q", "").strip()
    if search:
        leads = leads.filter(
            Q(business_name__icontains=search)
            | Q(phone__icontains=search)
            | Q(email__icontains=search)
            | Q(owner_name__icontains=search)
            | Q(city__icontains=search)
        )

    campaign_id = request.GET.get("campaign")
    if _is_positive_int(campaign_id):
        leads = leads.filter(campaign_id=campaign_id)

    status = request.GET.get("status")
    if status in Lead.Status.values:
        leads = leads.filter(status=status)

    tier = request.GET.get("tier")
    if _is_positive_int(tier):
        leads = leads.filter(tier=tier)

    email_sent = request.GET.get("email_sent")
    if email_sent == "yes":
        leads = leads.filter(email_sent=True)
    elif email_sent == "no":
        leads = leads.filter(email_sent=False)

    phone_called = request.GET.get("phone_called")
    if phone_called == "yes":
        leads = leads.filter(phone_called=True)
    elif phone_called == "no":
        leads = leads.filter(phone_called=False)

    assigned_to = request.GET.get("assigned_to")
    if _is_admin(request.user) and assigned_to:
        if assigned_to == "unassigned":
            leads = leads.filter(assigned_to__isnull=True)
        elif _is_positive_int(assigned_to):
            leads = leads.filter(assigned_to_id=assigned_to)

    return leads.order_by("-created_at")


def _is_positive_int(value):
    return isinstance(value, str) and value.isdecimal() and int(value) > 0


def _dashboard_stats(leads):
    status_counts = {
        row["status"]: row["count"]
        for row in leads.values("status").annotate(count=Count("id"))
    }
    return {
        "total_leads": leads.count(),
        "new_leads": leads.filter(status=Lead.Status.NEW).count(),
        "email_sent": leads.filter(email_sent=True).count(),
        "contacted": leads.filter(phone_called=True).count(),
        "by_status": status_counts,
    }


def _agent_stats(agent):
    leads = Lead.objects.filter(assigned_to=agent)
    return {
        "total_leads": leads.count(),
        "interested_leads": leads.filter(status=Lead.Status.INTERESTED).count(),
        "demo_leads": leads.filter(status=Lead.Status.DEMO_BOOKED).count(),
        "phone_numbers": agent.phone_numbers.count(),
    }


def _status_rows(leads):
    total = leads.count()
    rows = []
    counts = {
        row["status"]: row["count"]
        for row in leads.values("status").annotate(count=Count("id"))
    }
    for value, label in Lead.Status.choices:
        count = counts.get(value, 0)
        rows.append(
            {
                "value": value,
                "label": label,
                "count": count,
                "percent": round((count / total) * 100) if total else 0,
            }
        )
    return rows


def _lead_queryset_for_user(user):
    if _is_admin(user):
        return Lead.objects.all()

    if _is_agent(user):
        return Lead.objects.filter(assigned_to=user)

    return Lead.objects.none()


def _email_queryset_for_user(user):
    if _is_admin(user):
        return EmailLog.objects.all()

    if _is_agent(user):
        return EmailLog.objects.filter(lead__assigned_to=user)

    return EmailLog.objects.none()


def _activity_queryset_for_user(user):
    if _is_admin(user):
        return ActivityLog.objects.select_related("user", "lead").order_by(
            "-created_at"
        )

    if _is_agent(user):
        return (
            ActivityLog.objects.select_related("user", "lead")
            .filter(lead__assigned_to=user)
            .order_by("-created_at")
        )

    return ActivityLog.objects.none()


def _agent_queryset():
    return User.objects.filter(role=User.Role.AGENT).order_by("username")


def _require_admin(user):
    if not _is_admin(user):
        return JsonResponse({"error": "Admin access is required."}, status=403)

    return None


def _require_admin_view(request, *, agent_redirect=None):
    if _is_admin(request.user):
        return None

    if agent_redirect and _is_agent(request.user) and not _wants_json(request):
        return redirect(agent_redirect)

    return JsonResponse({"error": "Admin access is required."}, status=403)


def _require_agent(user):
    if not _is_agent(user):
        return JsonResponse({"error": "Agent access is required."}, status=403)

    return None


def _is_admin(user):
    return user.is_authenticated and (
        user.is_superuser or getattr(user, "role", None) == User.Role.ADMIN
    )


def _is_agent(user):
    return user.is_authenticated and getattr(user, "role", None) == User.Role.AGENT


def _wants_json(request):
    if request.GET.get("format") == "json":
        return True

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True

    return "application/json" in request.headers.get("accept", "")


def _safe_redirect_url(request, next_url):
    if not next_url:
        return None

    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url

    return None


def _next_url_from_request(request):
    return _safe_redirect_url(
        request,
        request.POST.get("next") or request.GET.get("next"),
    )


def _form_error_response(form):
    return JsonResponse({"errors": form.errors.get_json_data()}, status=400)


def _service_error_response(error):
    if hasattr(error, "message_dict"):
        return JsonResponse({"errors": error.message_dict}, status=400)

    if hasattr(error, "messages"):
        return JsonResponse({"errors": {"__all__": error.messages}}, status=400)

    raise error


def _serialize_campaign(campaign):
    return {
        "id": campaign.id,
        "name": campaign.name,
        "niche": campaign.niche,
        "location": campaign.location,
        "description": campaign.description,
        "is_active": campaign.is_active,
        "created_at": _isoformat(campaign.created_at),
        "updated_at": _isoformat(campaign.updated_at),
    }


def _serialize_lead(lead):
    notes_count = getattr(lead, "notes_count", None)
    if notes_count is None:
        notes_count = lead.notes.count()

    return {
        "id": lead.id,
        "campaign": {
            "id": lead.campaign_id,
            "name": lead.campaign.name if lead.campaign_id else "",
        },
        "assigned_to": _serialize_user(lead.assigned_to),
        "business_name": lead.business_name,
        "phone": lead.phone,
        "email": lead.email,
        "city": lead.city,
        "owner_name": lead.owner_name,
        "business_type": lead.business_type,
        "score": lead.score,
        "tier": lead.tier,
        "tier_display": lead.get_tier_display(),
        "status": lead.status,
        "status_display": lead.get_status_display(),
        "hook": lead.hook,
        "warning": lead.warning,
        "email_sent": lead.email_sent,
        "email_sent_at": _isoformat(lead.email_sent_at),
        "phone_called": lead.phone_called,
        "phone_called_at": _isoformat(lead.phone_called_at),
        "last_contacted_at": _isoformat(lead.last_contacted_at),
        "notes_count": notes_count,
        "created_at": _isoformat(lead.created_at),
        "updated_at": _isoformat(lead.updated_at),
    }


def _serialize_email(email):
    return {
        "id": email.id,
        "lead_id": email.lead_id,
        "created_by": _serialize_user(email.created_by),
        "subject": email.subject,
        "body": email.body,
        "status": email.status,
        "status_display": email.get_status_display(),
        "provider": email.provider,
        "provider_display": email.get_provider_display(),
        "sent_at": _isoformat(email.sent_at),
        "created_at": _isoformat(email.created_at),
    }


def _serialize_note(note):
    return {
        "id": note.id,
        "lead_id": note.lead_id,
        "agent": _serialize_user(note.agent),
        "phone_number": (
            _serialize_phone_number(note.phone_number) if note.phone_number_id else None
        ),
        "channel": note.channel,
        "channel_display": note.get_channel_display(),
        "outcome": note.outcome,
        "outcome_display": note.get_outcome_display() if note.outcome else "",
        "note": note.note,
        "created_at": _isoformat(note.created_at),
    }


def _serialize_activity(activity):
    return {
        "id": activity.id,
        "user": _serialize_user(activity.user),
        "lead_id": activity.lead_id,
        "lead": str(activity.lead) if activity.lead_id else "",
        "action_type": activity.action_type,
        "action_type_display": activity.get_action_type_display(),
        "outcome": activity.outcome,
        "outcome_display": activity.get_outcome_display() if activity.outcome else "",
        "message": activity.message,
        "created_at": _isoformat(activity.created_at),
    }


def _serialize_user(user):
    if user is None:
        return None

    return {
        "id": user.id,
        "username": user.username,
        "name": str(user),
        "role": user.role,
    }


def _serialize_agent(agent):
    return {
        "id": agent.id,
        "username": agent.username,
        "name": str(agent),
        "email": agent.email,
        "initials": agent.initials,
        "color": agent.color,
        "total_leads": getattr(agent, "total_leads", agent.assigned_leads.count()),
        "interested_leads": getattr(agent, "interested_leads", 0),
        "demo_leads": getattr(agent, "demo_leads", 0),
        "phone_numbers": [
            _serialize_phone_number(phone_number)
            for phone_number in agent.phone_numbers.all()
        ],
    }


def _serialize_phone_number(phone_number):
    return {
        "id": phone_number.id,
        "phone": phone_number.phone,
        "order": phone_number.order,
        "usage_limit": phone_number.usage_limit,
        "usage_count": phone_number.usage_count,
        "usage_remaining": phone_number.usage_remaining,
        "is_available": phone_number.is_available,
        "agent": _serialize_user(phone_number.agent),
    }


def _isoformat(value):
    if value is None:
        return None

    return value.isoformat()
