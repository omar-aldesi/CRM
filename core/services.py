from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import AgentPhoneNumber, User
from core.models import ActivityLog, EmailLog, Lead, LeadNote


def create_activity_log(*, user, lead, action_type, outcome="", message=""):
    return ActivityLog.objects.create(
        user=user,
        lead=lead,
        action_type=action_type,
        outcome=outcome,
        message=message,
    )


@transaction.atomic
def create_manual_lead(
    *,
    campaign,
    business_name,
    created_by=None,
    assigned_to=None,
    phone="",
    email="",
    city="",
    owner_name="",
    business_type="Roofing",
    score="",
    tier=Lead.Tier.WARM,
    status=Lead.Status.NEW,
    hook="",
    warning="",
):
    if assigned_to is not None:
        _ensure_agent(assigned_to)

    _ensure_valid_choice(tier, Lead.Tier.values, "tier")
    _ensure_valid_choice(status, Lead.Status.values, "status")

    business_name = business_name.strip()
    if not business_name:
        raise ValidationError({"business_name": "Business name cannot be blank."})

    lead = Lead.objects.create(
        campaign=campaign,
        assigned_to=assigned_to,
        business_name=business_name,
        phone=phone.strip(),
        email=email.strip(),
        city=city.strip(),
        owner_name=owner_name.strip(),
        business_type=business_type.strip() or "Roofing",
        score=score.strip(),
        tier=tier,
        status=status,
        hook=hook.strip(),
        warning=warning.strip(),
    )

    create_activity_log(
        user=created_by,
        lead=lead,
        action_type=ActivityLog.ActionType.LEAD_IMPORTED,
        message="Lead manually added.",
    )

    if assigned_to is not None:
        create_activity_log(
            user=created_by,
            lead=lead,
            action_type=ActivityLog.ActionType.LEAD_ASSIGNED,
            message=f"Lead assigned to {assigned_to}.",
        )

    return lead


@transaction.atomic
def assign_lead(*, lead, agent, assigned_by=None):
    if agent is not None:
        _ensure_agent(agent)

    previous_agent = lead.assigned_to
    if previous_agent == agent:
        return lead

    lead.assigned_to = agent
    lead.save(update_fields=["assigned_to", "updated_at"])

    if agent is None:
        message = "Lead unassigned."
    elif previous_agent is None:
        message = f"Lead assigned to {agent}."
    else:
        message = f"Lead reassigned from {previous_agent} to {agent}."

    create_activity_log(
        user=assigned_by,
        lead=lead,
        action_type=ActivityLog.ActionType.LEAD_ASSIGNED,
        message=message,
    )
    return lead


@transaction.atomic
def change_lead_status(*, lead, status, changed_by=None, message=""):
    _ensure_valid_choice(status, Lead.Status.values, "status")

    previous_status = lead.status
    if previous_status == status:
        return lead

    lead.status = status
    lead.save(update_fields=["status", "updated_at"])

    create_activity_log(
        user=changed_by,
        lead=lead,
        action_type=ActivityLog.ActionType.STATUS_CHANGED,
        outcome=status,
        message=message or f"Status changed from {previous_status} to {status}.",
    )
    return lead


@transaction.atomic
def generate_email_for_lead(
    *,
    lead,
    created_by,
    subject="",
    body="",
    provider=EmailLog.Provider.TEMPLATE,
):
    _ensure_user_can_work_lead(created_by, lead)
    _ensure_valid_choice(provider, EmailLog.Provider.values, "provider")

    if not subject or not body:
        default_subject, default_body = build_lead_email(lead)
        subject = subject or default_subject
        body = body or default_body

    email_log = EmailLog.objects.create(
        lead=lead,
        created_by=created_by,
        subject=subject,
        body=body,
        status=EmailLog.Status.GENERATED,
        provider=provider,
    )

    create_activity_log(
        user=created_by,
        lead=lead,
        action_type=ActivityLog.ActionType.EMAIL_GENERATED,
        message=f"Email generated with {email_log.get_provider_display()}.",
    )
    return email_log


@transaction.atomic
def mark_email_sent(*, email_log, sent_by=None, note=""):
    _ensure_user_can_work_lead(sent_by, email_log.lead)

    if email_log.status == EmailLog.Status.SENT:
        return email_log

    email_log.mark_sent()
    email_log.refresh_from_db()

    create_activity_log(
        user=sent_by or email_log.created_by,
        lead=email_log.lead,
        action_type=ActivityLog.ActionType.EMAIL_SENT,
        message=f"Email sent: {email_log.subject}",
    )

    note = note.strip()
    if note:
        LeadNote.objects.create(
            lead=email_log.lead,
            agent=sent_by,
            channel=LeadNote.Channel.EMAIL,
            note=note,
        )
        create_activity_log(
            user=sent_by,
            lead=email_log.lead,
            action_type=ActivityLog.ActionType.NOTE_ADDED,
            message=note,
        )

    return email_log


@transaction.atomic
def mark_lead_email_sent(*, lead, sent_by, note=""):
    _ensure_user_can_work_lead(sent_by, lead)

    email_log = lead.emails.order_by("-created_at").first()
    if email_log is None:
        subject, body = build_lead_email(lead)
        email_log = EmailLog.objects.create(
            lead=lead,
            created_by=sent_by,
            subject=subject,
            body=body,
            status=EmailLog.Status.GENERATED,
            provider=EmailLog.Provider.TEMPLATE,
        )

    return mark_email_sent(email_log=email_log, sent_by=sent_by, note=note)


@transaction.atomic
def mark_phone_called(*, lead, called_by, agent_phone_number=None):
    _ensure_agent(called_by)
    _ensure_user_can_work_lead(called_by, lead)

    if lead.phone_called:
        return lead

    if agent_phone_number is not None:
        agent_phone_number = _record_phone_number_usage(
            agent_phone_number=agent_phone_number,
            agent=called_by,
        )

    now = timezone.now()
    previous_status = lead.status
    update_fields = [
        "phone_called",
        "phone_called_at",
        "last_contacted_at",
        "updated_at",
    ]

    lead.phone_called = True
    lead.phone_called_at = now
    lead.last_contacted_at = now
    if lead.status == Lead.Status.NEW:
        lead.status = Lead.Status.CALLED
        update_fields.append("status")

    lead.save(update_fields=update_fields)

    create_activity_log(
        user=called_by,
        lead=lead,
        action_type=ActivityLog.ActionType.CALL_LOGGED,
        outcome=lead.status,
        message=_phone_call_message("Phone marked as called.", agent_phone_number),
    )

    if previous_status != lead.status:
        create_activity_log(
            user=called_by,
            lead=lead,
            action_type=ActivityLog.ActionType.STATUS_CHANGED,
            outcome=lead.status,
            message=f"Status changed from {previous_status} to {lead.status}.",
        )

    return lead


@transaction.atomic
def record_call_outcome(*, lead, agent, note, outcome, agent_phone_number):
    _ensure_agent(agent)
    _ensure_user_can_work_lead(agent, lead)
    _ensure_valid_choice(outcome, Lead.Status.values, "outcome")

    note = note.strip()
    if not note:
        raise ValidationError({"note": "Call note cannot be blank."})

    agent_phone_number = _record_phone_number_usage(
        agent_phone_number=agent_phone_number,
        agent=agent,
    )

    now = timezone.now()
    previous_status = lead.status

    lead.status = outcome
    lead.phone_called = True
    lead.phone_called_at = now
    lead.last_contacted_at = now
    lead.save(
        update_fields=[
            "status",
            "phone_called",
            "phone_called_at",
            "last_contacted_at",
            "updated_at",
        ]
    )

    lead_note = LeadNote.objects.create(
        lead=lead,
        agent=agent,
        phone_number=agent_phone_number,
        channel=LeadNote.Channel.PHONE,
        outcome=outcome,
        note=note,
    )

    create_activity_log(
        user=agent,
        lead=lead,
        action_type=ActivityLog.ActionType.CALL_LOGGED,
        outcome=outcome,
        message=_phone_call_message(note, agent_phone_number),
    )

    if previous_status != outcome:
        create_activity_log(
            user=agent,
            lead=lead,
            action_type=ActivityLog.ActionType.STATUS_CHANGED,
            outcome=outcome,
            message=f"Status changed from {previous_status} to {outcome}.",
        )

    return lead_note


def _record_phone_number_usage(*, agent_phone_number, agent):
    if not isinstance(agent_phone_number, AgentPhoneNumber):
        raise ValidationError({"agent_phone_number": "A phone number is required."})

    phone_number = AgentPhoneNumber.objects.select_for_update().get(
        pk=agent_phone_number.pk
    )
    if phone_number.agent_id != agent.id:
        raise ValidationError(
            {"agent_phone_number": "Agents can only use their assigned numbers."}
        )

    if phone_number.usage_count >= phone_number.usage_limit:
        raise ValidationError(
            {"agent_phone_number": "This phone number has reached its limit."}
        )

    phone_number.usage_count += 1
    phone_number.save(update_fields=["usage_count"])
    return phone_number


def _phone_call_message(message, agent_phone_number):
    if agent_phone_number is None:
        return message

    return f"{message} Used {agent_phone_number.phone}."


def build_lead_email(lead):
    greeting_name = lead.owner_name.strip() if lead.owner_name else "there"
    business_type = lead.business_type.strip().lower() if lead.business_type else "business"
    city_phrase = f" in {lead.city.strip()}" if lead.city else ""
    hook = lead.hook.strip()

    subject = f"Quick idea for {lead.business_name}"
    body_parts = [
        f"Hi {greeting_name},",
        (
            f"I came across {lead.business_name} and noticed you work in "
            f"{business_type}{city_phrase}."
        ),
    ]

    if hook:
        body_parts.append(hook)

    body_parts.extend(
        [
            (
                "I wanted to share a quick idea that may help bring in more "
                "qualified leads without adding extra admin work."
            ),
            "Would you be open to a short call this week?",
            "Best,",
        ]
    )
    return subject, "\n\n".join(body_parts)


def _ensure_agent(user):
    if not isinstance(user, User) or user.role != User.Role.AGENT:
        raise ValidationError({"agent": "Selected user must be an agent."})


def _ensure_user_can_work_lead(user, lead):
    if user is None:
        return

    if not isinstance(user, User):
        raise ValidationError({"user": "A CRM user is required."})

    if user.role == User.Role.AGENT and lead.assigned_to_id != user.id:
        raise ValidationError(
            {"lead": "Agents can only work leads assigned to them."}
        )


def _ensure_valid_choice(value, choices, field_name):
    if value not in choices:
        raise ValidationError({field_name: f"{value!r} is not a valid choice."})
