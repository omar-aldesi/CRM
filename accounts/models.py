from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        AGENT = "AGENT", "Agent"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.AGENT,
    )

    initials = models.CharField(max_length=5, blank=True)
    color = models.CharField(max_length=20, blank=True)

    def is_admin(self):
        return self.role == self.Role.ADMIN

    def is_agent(self):
        return self.role == self.Role.AGENT

    def __str__(self):
        return self.get_full_name() or self.username


class AgentPhoneNumber(models.Model):
    agent = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="phone_numbers",
        limit_choices_to={"role": User.Role.AGENT},
        null=True,
        blank=True,
    )

    phone = models.CharField(max_length=30)
    order = models.PositiveSmallIntegerField(default=1)
    usage_limit = models.PositiveIntegerField(default=30)
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["phone"],
                name="unique_agent_phone_number",
            )
        ]

    @property
    def usage_remaining(self):
        return max(self.usage_limit - self.usage_count, 0)

    @property
    def is_available(self):
        return self.usage_count < self.usage_limit

    def __str__(self):
        return f"{self.agent or 'Unassigned'} - {self.phone}"
