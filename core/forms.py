from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import F

from accounts.models import AgentPhoneNumber, User
from core.models import Campaign, EmailLog, Lead


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["name", "niche", "location", "description", "is_active"]


class ManualLeadForm(forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=User.objects.none(), required=False)
    business_name = forms.CharField(max_length=255)
    phone = forms.CharField(max_length=30, required=False)
    email = forms.EmailField(required=False)
    city = forms.CharField(max_length=100, required=False)
    owner_name = forms.CharField(max_length=255, required=False)
    business_type = forms.CharField(max_length=100, required=False)
    score = forms.CharField(max_length=20, required=False)
    tier = forms.TypedChoiceField(
        choices=Lead.Tier.choices,
        coerce=int,
        empty_value=Lead.Tier.WARM,
        required=False,
    )
    status = forms.ChoiceField(choices=Lead.Status.choices, required=False)
    hook = forms.CharField(required=False, widget=forms.Textarea)
    warning = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = _agent_queryset()
        self.fields["tier"].initial = Lead.Tier.WARM
        self.fields["status"].initial = Lead.Status.NEW

    def clean_status(self):
        return self.cleaned_data["status"] or Lead.Status.NEW


class LeadAssignmentForm(forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=User.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = _agent_queryset()


class EmailGenerateForm(forms.Form):
    subject = forms.CharField(max_length=255, required=False)
    body = forms.CharField(required=False, widget=forms.Textarea)
    provider = forms.ChoiceField(
        choices=EmailLog.Provider.choices,
        initial=EmailLog.Provider.TEMPLATE,
        required=False,
    )

    def clean_provider(self):
        return self.cleaned_data["provider"] or EmailLog.Provider.TEMPLATE


class CallOutcomeForm(forms.Form):
    agent_phone_number = forms.ModelChoiceField(
        label="Calling number",
        queryset=AgentPhoneNumber.objects.none(),
        required=True,
    )
    outcome = forms.ChoiceField(
        label="Outcome",
        choices=[
            (Lead.Status.CALLED, "Called"),
            (Lead.Status.NO_ANSWER, "No Answer"),
            (Lead.Status.INTERESTED, "Interested"),
            (Lead.Status.DEMO_BOOKED, "Demo Booked"),
            (Lead.Status.NOT_QUALIFIED, "Not Qualified"),
            (Lead.Status.CLOSED, "Closed"),
        ],
    )
    note = forms.CharField(
        label="Call note",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "What happened on the call?",
            }
        ),
    )

    def __init__(self, *args, agent=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = AgentPhoneNumber.objects.none()
        if agent is not None:
            queryset = AgentPhoneNumber.objects.filter(
                agent=agent,
                usage_count__lt=F("usage_limit"),
            ).order_by("order", "phone")

        self.fields["agent_phone_number"].queryset = queryset
        self.fields["agent_phone_number"].empty_label = (
            "Choose a number" if queryset.exists() else "No available numbers"
        )


class AgentCreateForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "initials",
            "color",
        ]

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def save(self, commit=True):
        password = self.cleaned_data.pop("password")
        user = super().save(commit=False)
        user.role = User.Role.AGENT
        user.set_password(password)
        if not user.initials:
            display_name = user.get_full_name() or user.username
            user.initials = display_name[:2].upper()
        if commit:
            user.save()
        return user


class AgentUpdateForm(forms.ModelForm):
    new_password = forms.CharField(
        help_text="Leave blank to keep the current password.",
        label="New password",
        required=False,
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "initials",
            "color",
            "is_active",
        ]

    def clean_new_password(self):
        password = self.cleaned_data["new_password"]
        if password:
            validate_password(password, self.instance)
        return password

    def save(self, commit=True):
        password = self.cleaned_data.pop("new_password")
        user = super().save(commit=False)
        user.role = User.Role.AGENT
        if password:
            user.set_password(password)
        if not user.initials:
            display_name = user.get_full_name() or user.username
            user.initials = display_name[:2].upper()
        if commit:
            user.save()
        return user


class PhoneNumberCreateForm(forms.ModelForm):
    class Meta:
        model = AgentPhoneNumber
        fields = ["phone", "agent", "order", "usage_limit", "usage_count"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["agent"].queryset = _agent_queryset()
        self.fields["agent"].required = False

    def clean(self):
        cleaned_data = super().clean()
        _validate_usage(cleaned_data)
        return cleaned_data


class PhoneNumberAssignmentForm(forms.ModelForm):
    class Meta:
        model = AgentPhoneNumber
        fields = ["agent", "order", "usage_limit", "usage_count"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["agent"].queryset = _agent_queryset()
        self.fields["agent"].required = False

    def clean(self):
        cleaned_data = super().clean()
        _validate_usage(cleaned_data)
        return cleaned_data


def _agent_queryset():
    return User.objects.filter(role=User.Role.AGENT).order_by("username")


def _validate_usage(cleaned_data):
    usage_count = cleaned_data.get("usage_count")
    usage_limit = cleaned_data.get("usage_limit")
    if (
        usage_count is not None
        and usage_limit is not None
        and usage_count > usage_limit
    ):
        raise ValidationError("Usage cannot be greater than the limit.")
