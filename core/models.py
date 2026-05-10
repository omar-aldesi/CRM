from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Campaign(models.Model):
    name = models.CharField(max_length=255)

    niche = models.CharField(
        max_length=100,
        blank=True,
        help_text="Example: Roofing, HVAC, Plumbing",
    )

    location = models.CharField(
        max_length=100,
        blank=True,
        help_text="Example: OC, Anaheim, Los Angeles",
    )

    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Lead(models.Model):
    class Tier(models.IntegerChoices):
        HOT = 1, "Tier 1 - Hot"
        WARM = 2, "Tier 2 - Warm"
        SKIP = 3, "Tier 3 - Skip"

    class Status(models.TextChoices):
        NEW = "NEW", "New"
        NO_ANSWER = "NO_ANSWER", "No Answer"
        CALLED = "CALLED", "Called"
        INTERESTED = "INTERESTED", "Interested"
        DEMO_BOOKED = "DEMO_BOOKED", "Demo Booked"
        CLOSED = "CLOSED", "Closed"
        NOT_QUALIFIED = "NOT_QUALIFIED", "Not Qualified"

    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="leads",
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_leads",
    )

    business_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    owner_name = models.CharField(max_length=255, blank=True)

    business_type = models.CharField(max_length=100, default="Roofing")
    score = models.CharField(max_length=20, blank=True)

    tier = models.PositiveSmallIntegerField(
        choices=Tier.choices,
        default=Tier.WARM,
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.NEW,
    )

    hook = models.TextField(blank=True)
    warning = models.TextField(blank=True)

    email_sent = models.BooleanField(default=False)
    email_sent_at = models.DateTimeField(null=True, blank=True)

    phone_called = models.BooleanField(default=False)
    phone_called_at = models.DateTimeField(null=True, blank=True)

    last_contacted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["tier"]),
            models.Index(fields=["assigned_to"]),
            models.Index(fields=["email_sent"]),
            models.Index(fields=["email"]),
            models.Index(fields=["phone_called"]),
            models.Index(fields=["business_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "phone"],
                condition=~Q(phone=""),
                name="unique_lead_phone_per_campaign",
            )
        ]

    def __str__(self):
        return self.business_name


class LeadNote(models.Model):
    class Channel(models.TextChoices):
        GENERAL = "GENERAL", "General"
        PHONE = "PHONE", "Phone"
        EMAIL = "EMAIL", "Email"

    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name="notes",
    )

    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_notes",
    )

    phone_number = models.ForeignKey(
        "accounts.AgentPhoneNumber",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_notes",
    )

    outcome = models.CharField(
        max_length=30,
        choices=Lead.Status.choices,
        blank=True,
    )

    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.GENERAL,
    )

    note = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["channel"]),
        ]

    def __str__(self):
        return f"{self.lead.business_name} - {self.outcome or 'Note'}"


class EmailLog(models.Model):
    class Status(models.TextChoices):
        GENERATED = "GENERATED", "Generated"
        COPIED = "COPIED", "Copied"
        SENT = "SENT", "Sent"

    class Provider(models.TextChoices):
        TEMPLATE = "TEMPLATE", "Template"
        ANTHROPIC = "ANTHROPIC", "Anthropic"
        OPENAI = "OPENAI", "OpenAI"

    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name="emails",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_logs",
    )

    subject = models.CharField(max_length=255)
    body = models.TextField()

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.GENERATED,
    )

    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.TEMPLATE,
    )

    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def mark_sent(self):
        now = timezone.now()

        self.status = self.Status.SENT
        self.sent_at = now
        self.save(update_fields=["status", "sent_at"])

        self.lead.email_sent = True
        self.lead.email_sent_at = now
        self.lead.save(update_fields=["email_sent", "email_sent_at"])

    def __str__(self):
        return f"Email for {self.lead.business_name}"


class ActivityLog(models.Model):
    class ActionType(models.TextChoices):
        LEAD_IMPORTED = "LEAD_IMPORTED", "Lead Imported"
        LEAD_ASSIGNED = "LEAD_ASSIGNED", "Lead Assigned"
        NOTE_ADDED = "NOTE_ADDED", "Note Added"
        CALL_LOGGED = "CALL_LOGGED", "Call Logged"
        EMAIL_GENERATED = "EMAIL_GENERATED", "Email Generated"
        EMAIL_SENT = "EMAIL_SENT", "Email Sent"
        STATUS_CHANGED = "STATUS_CHANGED", "Status Changed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )

    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )

    action_type = models.CharField(
        max_length=40,
        choices=ActionType.choices,
    )

    outcome = models.CharField(
        max_length=30,
        choices=Lead.Status.choices,
        blank=True,
    )

    message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action_type} - {self.lead or 'No lead'}"
