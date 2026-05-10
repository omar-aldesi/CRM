from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import AgentPhoneNumber
from core.models import ActivityLog, Campaign, EmailLog, Lead, LeadNote
from core.services import (
    assign_lead,
    change_lead_status,
    create_manual_lead,
    generate_email_for_lead,
    mark_lead_email_sent,
    mark_email_sent,
    mark_phone_called,
    record_call_outcome,
)


User = get_user_model()


class LeadWorkflowServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="admin",
            password="password",
            role=User.Role.ADMIN,
        )
        cls.agent = User.objects.create_user(
            username="agent",
            password="password",
            role=User.Role.AGENT,
        )
        cls.other_agent = User.objects.create_user(
            username="other-agent",
            password="password",
            role=User.Role.AGENT,
        )
        cls.campaign = Campaign.objects.create(name="Spring Campaign")

    def make_lead(self, **overrides):
        data = {
            "campaign": self.campaign,
            "business_name": "Acme Roofing",
            "phone": "",
        }
        data.update(overrides)
        return Lead.objects.create(**data)

    def test_create_manual_lead_logs_import_and_optional_assignment(self):
        lead = create_manual_lead(
            campaign=self.campaign,
            business_name="  Orange County Roofing  ",
            created_by=self.admin,
            assigned_to=self.agent,
            phone=" 555-0100 ",
            email=" sam@example.com ",
            city=" Anaheim ",
            owner_name=" Sam ",
            hook=" Mention storm repairs. ",
        )

        self.assertEqual(lead.business_name, "Orange County Roofing")
        self.assertEqual(lead.phone, "555-0100")
        self.assertEqual(lead.email, "sam@example.com")
        self.assertEqual(lead.city, "Anaheim")
        self.assertEqual(lead.owner_name, "Sam")
        self.assertEqual(lead.hook, "Mention storm repairs.")
        self.assertEqual(lead.assigned_to, self.agent)

        self.assertEqual(ActivityLog.objects.filter(lead=lead).count(), 2)
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.admin,
                action_type=ActivityLog.ActionType.LEAD_IMPORTED,
            ).exists()
        )
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.admin,
                action_type=ActivityLog.ActionType.LEAD_ASSIGNED,
            ).exists()
        )

    def test_create_manual_lead_rejects_blank_business_name(self):
        with self.assertRaises(ValidationError):
            create_manual_lead(
                campaign=self.campaign,
                business_name="   ",
                created_by=self.admin,
            )

        self.assertEqual(Lead.objects.count(), 0)
        self.assertEqual(ActivityLog.objects.count(), 0)

    def test_assign_lead_sets_agent_and_logs_activity(self):
        lead = self.make_lead()

        assign_lead(lead=lead, agent=self.agent, assigned_by=self.admin)

        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.agent)
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.admin,
                action_type=ActivityLog.ActionType.LEAD_ASSIGNED,
                message=f"Lead assigned to {self.agent}.",
            ).exists()
        )

    def test_assign_lead_rejects_admin_as_assignee(self):
        lead = self.make_lead()

        with self.assertRaises(ValidationError):
            assign_lead(lead=lead, agent=self.admin, assigned_by=self.admin)

        lead.refresh_from_db()
        self.assertIsNone(lead.assigned_to)
        self.assertEqual(ActivityLog.objects.filter(lead=lead).count(), 0)

    def test_assign_lead_is_idempotent_for_same_agent(self):
        lead = self.make_lead(assigned_to=self.agent)

        assign_lead(lead=lead, agent=self.agent, assigned_by=self.admin)

        self.assertEqual(ActivityLog.objects.filter(lead=lead).count(), 0)

    def test_change_lead_status_updates_status_and_logs_activity(self):
        lead = self.make_lead(status=Lead.Status.NEW)

        change_lead_status(
            lead=lead,
            status=Lead.Status.DEMO_BOOKED,
            changed_by=self.admin,
        )

        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.DEMO_BOOKED)
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.admin,
                action_type=ActivityLog.ActionType.STATUS_CHANGED,
                outcome=Lead.Status.DEMO_BOOKED,
            ).exists()
        )

    def test_generate_email_for_assigned_agent_creates_email_and_activity(self):
        lead = self.make_lead(
            assigned_to=self.agent,
            owner_name="Taylor",
            city="Anaheim",
            business_type="Roofing",
            hook="Your reviews mention emergency repairs.",
        )

        email_log = generate_email_for_lead(lead=lead, created_by=self.agent)

        self.assertEqual(email_log.lead, lead)
        self.assertEqual(email_log.created_by, self.agent)
        self.assertEqual(email_log.status, EmailLog.Status.GENERATED)
        self.assertEqual(email_log.provider, EmailLog.Provider.TEMPLATE)
        self.assertIn("Taylor", email_log.body)
        self.assertIn("emergency repairs", email_log.body)
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.agent,
                action_type=ActivityLog.ActionType.EMAIL_GENERATED,
            ).exists()
        )

    def test_generate_email_rejects_agent_for_unassigned_lead(self):
        lead = self.make_lead(assigned_to=self.other_agent)

        with self.assertRaises(ValidationError):
            generate_email_for_lead(lead=lead, created_by=self.agent)

        self.assertEqual(EmailLog.objects.filter(lead=lead).count(), 0)
        self.assertEqual(ActivityLog.objects.filter(lead=lead).count(), 0)

    def test_mark_email_sent_updates_email_and_lead_flags_and_logs_activity(self):
        lead = self.make_lead(assigned_to=self.agent)
        email_log = EmailLog.objects.create(
            lead=lead,
            created_by=self.agent,
            subject="Quick idea",
            body="Hello",
        )

        mark_email_sent(
            email_log=email_log,
            sent_by=self.agent,
            note="Sent the intro email.",
        )

        email_log.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual(email_log.status, EmailLog.Status.SENT)
        self.assertIsNotNone(email_log.sent_at)
        self.assertTrue(lead.email_sent)
        self.assertIsNotNone(lead.email_sent_at)
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.agent,
                action_type=ActivityLog.ActionType.EMAIL_SENT,
            ).exists()
        )
        self.assertTrue(
            LeadNote.objects.filter(
                lead=lead,
                agent=self.agent,
                channel=LeadNote.Channel.EMAIL,
                note="Sent the intro email.",
            ).exists()
        )

    def test_mark_lead_email_sent_creates_log_and_email_note(self):
        lead = self.make_lead(assigned_to=self.agent, email="owner@example.com")

        email_log = mark_lead_email_sent(
            lead=lead,
            sent_by=self.agent,
            note="Sent proposal to owner.",
        )

        lead.refresh_from_db()
        self.assertEqual(email_log.status, EmailLog.Status.SENT)
        self.assertTrue(lead.email_sent)
        self.assertTrue(
            LeadNote.objects.filter(
                lead=lead,
                agent=self.agent,
                channel=LeadNote.Channel.EMAIL,
                note="Sent proposal to owner.",
            ).exists()
        )

    def test_mark_email_sent_is_idempotent(self):
        lead = self.make_lead(assigned_to=self.agent)
        email_log = EmailLog.objects.create(
            lead=lead,
            created_by=self.agent,
            subject="Quick idea",
            body="Hello",
        )

        mark_email_sent(email_log=email_log, sent_by=self.agent)
        mark_email_sent(email_log=email_log, sent_by=self.agent)

        self.assertEqual(
            ActivityLog.objects.filter(
                lead=lead,
                action_type=ActivityLog.ActionType.EMAIL_SENT,
            ).count(),
            1,
        )

    def test_mark_phone_called_updates_lead_flags_status_and_activity(self):
        lead = self.make_lead(assigned_to=self.agent, status=Lead.Status.NEW)

        mark_phone_called(lead=lead, called_by=self.agent)

        lead.refresh_from_db()
        self.assertTrue(lead.phone_called)
        self.assertIsNotNone(lead.phone_called_at)
        self.assertIsNotNone(lead.last_contacted_at)
        self.assertEqual(lead.status, Lead.Status.CALLED)
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.agent,
                action_type=ActivityLog.ActionType.CALL_LOGGED,
                message="Phone marked as called.",
            ).exists()
        )
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.agent,
                action_type=ActivityLog.ActionType.STATUS_CHANGED,
                outcome=Lead.Status.CALLED,
            ).exists()
        )

    def test_mark_phone_called_is_idempotent(self):
        lead = self.make_lead(assigned_to=self.agent)

        mark_phone_called(lead=lead, called_by=self.agent)
        mark_phone_called(lead=lead, called_by=self.agent)

        self.assertEqual(
            ActivityLog.objects.filter(
                lead=lead,
                action_type=ActivityLog.ActionType.CALL_LOGGED,
            ).count(),
            1,
        )

    def test_mark_phone_called_does_not_reuse_number_when_already_called(self):
        lead = self.make_lead(assigned_to=self.agent, phone_called=True)
        phone_number = AgentPhoneNumber.objects.create(
            agent=self.agent,
            phone="555-0107",
            usage_limit=2,
        )

        mark_phone_called(
            lead=lead,
            called_by=self.agent,
            agent_phone_number=phone_number,
        )

        phone_number.refresh_from_db()
        self.assertEqual(phone_number.usage_count, 0)
        self.assertEqual(ActivityLog.objects.filter(lead=lead).count(), 0)

    def test_record_call_outcome_creates_note_updates_lead_and_logs_activity(self):
        lead = self.make_lead(assigned_to=self.agent, status=Lead.Status.NEW)
        phone_number = AgentPhoneNumber.objects.create(
            agent=self.agent,
            phone="555-0101",
            usage_limit=2,
        )

        lead_note = record_call_outcome(
            lead=lead,
            agent=self.agent,
            note=" Owner wants pricing tomorrow. ",
            outcome=Lead.Status.INTERESTED,
            agent_phone_number=phone_number,
        )

        phone_number.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.INTERESTED)
        self.assertTrue(lead.phone_called)
        self.assertIsNotNone(lead.phone_called_at)
        self.assertIsNotNone(lead.last_contacted_at)
        self.assertEqual(lead_note.note, "Owner wants pricing tomorrow.")
        self.assertEqual(lead_note.outcome, Lead.Status.INTERESTED)
        self.assertEqual(lead_note.channel, LeadNote.Channel.PHONE)
        self.assertEqual(lead_note.phone_number, phone_number)
        self.assertEqual(phone_number.usage_count, 1)
        self.assertEqual(LeadNote.objects.filter(lead=lead).count(), 1)
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.agent,
                action_type=ActivityLog.ActionType.CALL_LOGGED,
                outcome=Lead.Status.INTERESTED,
                message="Owner wants pricing tomorrow. Used 555-0101.",
            ).exists()
        )
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                user=self.agent,
                action_type=ActivityLog.ActionType.STATUS_CHANGED,
                outcome=Lead.Status.INTERESTED,
            ).exists()
        )

    def test_record_call_outcome_rejects_blank_note(self):
        lead = self.make_lead(assigned_to=self.agent)
        phone_number = AgentPhoneNumber.objects.create(
            agent=self.agent,
            phone="555-0102",
            usage_limit=1,
        )

        with self.assertRaises(ValidationError):
            record_call_outcome(
                lead=lead,
                agent=self.agent,
                note="   ",
                outcome=Lead.Status.NO_ANSWER,
                agent_phone_number=phone_number,
            )

        phone_number.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.NEW)
        self.assertFalse(lead.phone_called)
        self.assertIsNone(lead.phone_called_at)
        self.assertIsNone(lead.last_contacted_at)
        self.assertEqual(phone_number.usage_count, 0)
        self.assertEqual(LeadNote.objects.filter(lead=lead).count(), 0)
        self.assertEqual(ActivityLog.objects.filter(lead=lead).count(), 0)

    def test_record_call_outcome_rejects_phone_number_at_limit(self):
        lead = self.make_lead(assigned_to=self.agent)
        phone_number = AgentPhoneNumber.objects.create(
            agent=self.agent,
            phone="555-0103",
            usage_limit=1,
            usage_count=1,
        )

        with self.assertRaises(ValidationError):
            record_call_outcome(
                lead=lead,
                agent=self.agent,
                note="Reached owner.",
                outcome=Lead.Status.CALLED,
                agent_phone_number=phone_number,
            )

        lead.refresh_from_db()
        phone_number.refresh_from_db()
        self.assertFalse(lead.phone_called)
        self.assertEqual(phone_number.usage_count, 1)
        self.assertEqual(LeadNote.objects.filter(lead=lead).count(), 0)


class LeadWorkflowViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="view-admin",
            password="password",
            role=User.Role.ADMIN,
        )
        cls.agent = User.objects.create_user(
            username="view-agent",
            password="password",
            role=User.Role.AGENT,
        )
        cls.other_agent = User.objects.create_user(
            username="view-other-agent",
            password="password",
            role=User.Role.AGENT,
        )
        cls.campaign = Campaign.objects.create(name="View Campaign")

    def json_request(self):
        return {"HTTP_ACCEPT": "application/json"}

    def make_lead(self, **overrides):
        data = {
            "campaign": self.campaign,
            "business_name": "View Lead",
            "phone": "",
        }
        data.update(overrides)
        return Lead.objects.create(**data)

    def test_lead_list_requires_login(self):
        response = self.client.get(reverse("core:lead-list"), **self.json_request())

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_agent_lead_list_only_includes_assigned_leads(self):
        assigned_lead = self.make_lead(
            business_name="Assigned Lead",
            assigned_to=self.agent,
            phone="555-0101",
            email="assigned@example.com",
        )
        self.make_lead(
            business_name="Other Lead",
            assigned_to=self.other_agent,
            phone="555-0201",
            email="other@example.com",
        )
        self.client.force_login(self.agent)

        response = self.client.get(reverse("core:lead-list"), **self.json_request())

        self.assertEqual(response.status_code, 200)
        lead_ids = [lead["id"] for lead in response.json()["leads"]]
        self.assertEqual(lead_ids, [assigned_lead.id])

    def test_admin_can_create_campaign(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("core:campaign-create"),
            {
                "name": "Created Campaign",
                "niche": "Roofing",
                "location": "Anaheim",
                "is_active": "on",
            },
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["campaign"]["name"], "Created Campaign")
        self.assertTrue(Campaign.objects.filter(name="Created Campaign").exists())

    def test_admin_can_create_lead_with_assignment(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("core:lead-create", args=[self.campaign.id]),
            {
                "assigned_to": str(self.agent.id),
                "business_name": "Created Lead",
                "phone": "555-0111",
                "email": "created@example.com",
                "business_type": "Roofing",
                "tier": str(Lead.Tier.HOT),
                "status": Lead.Status.NEW,
            },
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()["lead"]
        lead = Lead.objects.get(pk=payload["id"])
        self.assertEqual(lead.assigned_to, self.agent)
        self.assertEqual(lead.email, "created@example.com")
        self.assertEqual(
            ActivityLog.objects.filter(lead=lead).count(),
            2,
        )

    def test_agent_cannot_create_lead(self):
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-create", args=[self.campaign.id]),
            {
                "business_name": "Blocked Lead",
                "tier": str(Lead.Tier.WARM),
                "status": Lead.Status.NEW,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Lead.objects.filter(business_name="Blocked Lead").exists())

    def test_admin_can_assign_lead(self):
        lead = self.make_lead()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("core:lead-assign", args=[lead.id]),
            {"assigned_to": str(self.agent.id)},
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.agent)
        self.assertEqual(response.json()["lead"]["assigned_to"]["id"], self.agent.id)

    def test_admin_can_bulk_assign_leads(self):
        first_lead = self.make_lead(business_name="Bulk Lead 1")
        second_lead = self.make_lead(business_name="Bulk Lead 2")
        self.client.force_login(self.admin)

        list_response = self.client.get(reverse("core:lead-list"))
        self.assertContains(list_response, "Select all")
        self.assertContains(list_response, reverse("core:lead-bulk-assign"))

        response = self.client.post(
            reverse("core:lead-bulk-assign"),
            {
                "lead_ids": [str(first_lead.id), str(second_lead.id)],
                "assigned_to": str(self.agent.id),
            },
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 200)
        first_lead.refresh_from_db()
        second_lead.refresh_from_db()
        self.assertEqual(first_lead.assigned_to, self.agent)
        self.assertEqual(second_lead.assigned_to, self.agent)
        self.assertEqual(response.json()["assigned_count"], 2)
        self.assertEqual(response.json()["selected_count"], 2)
        self.assertEqual(
            ActivityLog.objects.filter(
                action_type=ActivityLog.ActionType.LEAD_ASSIGNED,
                user=self.admin,
            ).count(),
            2,
        )

    def test_agent_cannot_bulk_assign_leads(self):
        lead = self.make_lead()
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-bulk-assign"),
            {
                "lead_ids": [str(lead.id)],
                "assigned_to": str(self.agent.id),
            },
        )

        self.assertEqual(response.status_code, 403)
        lead.refresh_from_db()
        self.assertIsNone(lead.assigned_to)

    def test_agent_cannot_assign_lead(self):
        lead = self.make_lead()
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-assign", args=[lead.id]),
            {"assigned_to": str(self.agent.id)},
        )

        self.assertEqual(response.status_code, 403)
        lead.refresh_from_db()
        self.assertIsNone(lead.assigned_to)

    def test_assigned_agent_can_generate_and_mark_email_sent(self):
        lead = self.make_lead(assigned_to=self.agent)
        self.client.force_login(self.agent)

        generate_response = self.client.post(
            reverse("core:lead-email-generate", args=[lead.id]),
            {"provider": EmailLog.Provider.TEMPLATE},
            **self.json_request(),
        )

        self.assertEqual(generate_response.status_code, 201)
        email_id = generate_response.json()["email"]["id"]

        sent_response = self.client.post(
            reverse("core:email-mark-sent", args=[email_id]),
            **self.json_request(),
        )

        self.assertEqual(sent_response.status_code, 200)
        lead.refresh_from_db()
        self.assertTrue(lead.email_sent)
        self.assertEqual(sent_response.json()["email"]["status"], EmailLog.Status.SENT)

    def test_assigned_agent_can_mark_lead_email_done_with_note(self):
        lead = self.make_lead(
            assigned_to=self.agent,
            email="owner@example.com",
        )
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-email-mark-sent", args=[lead.id]),
            {"note": "Sent the intro email."},
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 200)
        lead.refresh_from_db()
        self.assertTrue(lead.email_sent)
        self.assertEqual(response.json()["lead"]["email"], "owner@example.com")
        self.assertTrue(
            LeadNote.objects.filter(
                lead=lead,
                agent=self.agent,
                channel=LeadNote.Channel.EMAIL,
                note="Sent the intro email.",
            ).exists()
        )

    def test_mark_email_sent_ignores_external_next_url(self):
        lead = self.make_lead(assigned_to=self.agent)
        email_log = EmailLog.objects.create(
            lead=lead,
            created_by=self.agent,
            subject="Quick idea",
            body="Hello",
        )
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:email-mark-sent", args=[email_log.id]),
            {"next": "https://example.com/outside"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:lead-detail", args=[lead.id]))

    def test_agent_cannot_generate_email_for_unassigned_lead(self):
        lead = self.make_lead(assigned_to=self.other_agent)
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-email-generate", args=[lead.id]),
            {"provider": EmailLog.Provider.TEMPLATE},
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(EmailLog.objects.filter(lead=lead).count(), 0)

    def test_assigned_agent_can_record_call_outcome(self):
        lead = self.make_lead(assigned_to=self.agent, status=Lead.Status.NEW)
        phone_number = AgentPhoneNumber.objects.create(
            agent=self.agent,
            phone="555-0301",
            usage_limit=2,
        )
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-call-outcome", args=[lead.id]),
            {
                "agent_phone_number": str(phone_number.id),
                "outcome": Lead.Status.INTERESTED,
                "note": "Owner asked for details.",
            },
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 201)
        phone_number.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.INTERESTED)
        self.assertTrue(lead.phone_called)
        self.assertTrue(response.json()["lead"]["phone_called"])
        self.assertEqual(phone_number.usage_count, 1)
        self.assertEqual(LeadNote.objects.filter(lead=lead).count(), 1)
        self.assertEqual(
            response.json()["note"]["phone_number"]["id"],
            phone_number.id,
        )
        self.assertTrue(
            ActivityLog.objects.filter(
                lead=lead,
                action_type=ActivityLog.ActionType.CALL_LOGGED,
            ).exists()
        )

    def test_assigned_agent_can_mark_phone_called(self):
        lead = self.make_lead(assigned_to=self.agent, status=Lead.Status.NEW)
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-phone-mark-called", args=[lead.id]),
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 200)
        lead.refresh_from_db()
        self.assertTrue(lead.phone_called)
        self.assertIsNotNone(lead.phone_called_at)
        self.assertEqual(lead.status, Lead.Status.CALLED)
        self.assertTrue(response.json()["lead"]["phone_called"])
        self.assertEqual(response.json()["lead"]["status"], Lead.Status.CALLED)

    def test_agent_cannot_mark_other_agent_phone_called(self):
        lead = self.make_lead(assigned_to=self.other_agent)
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-phone-mark-called", args=[lead.id]),
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 404)
        lead.refresh_from_db()
        self.assertFalse(lead.phone_called)

    def test_mark_phone_called_can_return_to_lead_list(self):
        lead = self.make_lead(assigned_to=self.agent)
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-phone-mark-called", args=[lead.id]),
            {"next": reverse("core:lead-list")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:lead-list"))
        lead.refresh_from_db()
        self.assertTrue(lead.phone_called)

    def test_agent_call_outcome_can_return_to_lead_list(self):
        lead = self.make_lead(assigned_to=self.agent, status=Lead.Status.NEW)
        phone_number = AgentPhoneNumber.objects.create(
            agent=self.agent,
            phone="555-0302",
            usage_limit=2,
        )
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("core:lead-call-outcome", args=[lead.id]),
            {
                "agent_phone_number": str(phone_number.id),
                "outcome": Lead.Status.CALLED,
                "note": "Reached the office and asked for the owner.",
                "next": reverse("core:lead-list"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:lead-list"))
        phone_number.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.CALLED)
        self.assertEqual(phone_number.usage_count, 1)
        self.assertTrue(
            LeadNote.objects.filter(
                lead=lead,
                agent=self.agent,
                note="Reached the office and asked for the owner.",
            ).exists()
        )

    def test_call_outcome_form_excludes_non_call_statuses(self):
        lead = self.make_lead(assigned_to=self.agent)
        available_number = AgentPhoneNumber.objects.create(
            agent=self.agent,
            phone="555-0303",
            usage_limit=1,
        )
        AgentPhoneNumber.objects.create(
            agent=self.agent,
            phone="555-0304",
            usage_limit=1,
            usage_count=1,
        )
        self.client.force_login(self.agent)

        response = self.client.get(reverse("core:lead-call-outcome", args=[lead.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Call note")
        self.assertContains(response, "Calling number")
        self.assertContains(response, available_number.phone)
        self.assertNotContains(response, "555-0304")
        self.assertContains(response, "Called")
        self.assertNotContains(response, 'value="NEW"')

    def test_dashboard_is_admin_only_and_agent_redirects_to_lead_list(self):
        self.make_lead(assigned_to=self.agent, status=Lead.Status.NEW)
        self.make_lead(assigned_to=self.other_agent, status=Lead.Status.CLOSED)

        self.client.force_login(self.agent)
        agent_json_response = self.client.get(
            reverse("core:dashboard"),
            **self.json_request(),
        )
        agent_html_response = self.client.get(reverse("core:dashboard"))

        self.client.force_login(self.admin)
        admin_response = self.client.get(
            reverse("core:dashboard"),
            **self.json_request(),
        )

        self.assertEqual(agent_json_response.status_code, 403)
        self.assertEqual(agent_html_response.status_code, 302)
        self.assertEqual(agent_html_response["Location"], reverse("core:lead-list"))
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.json()["stats"]["total_leads"], 2)

    def test_dashboard_renders_html_for_browser_request(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/dashboard.html")
        self.assertContains(response, "Team Dashboard")

    def test_agent_can_open_assigned_leads_but_not_activity(self):
        lead = self.make_lead(
            assigned_to=self.agent,
            phone="555-0101",
            email="lead@example.com",
        )
        self.client.force_login(self.agent)

        list_response = self.client.get(reverse("core:lead-list"))
        detail_response = self.client.get(reverse("core:lead-detail", args=[lead.id]))
        activity_response = self.client.get(reverse("core:activity-list"))
        list_json_response = self.client.get(
            reverse("core:lead-list"),
            **self.json_request(),
        )
        detail_json_response = self.client.get(
            reverse("core:lead-detail", args=[lead.id]),
            **self.json_request(),
        )
        activity_json_response = self.client.get(
            reverse("core:activity-list"),
            **self.json_request(),
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertTemplateUsed(list_response, "core/lead_list.html")
        self.assertContains(list_response, "My Leads")
        self.assertContains(list_response, lead.business_name)
        self.assertEqual(detail_response.status_code, 200)
        self.assertTemplateUsed(detail_response, "core/lead_detail.html")
        self.assertContains(detail_response, "Phone work")
        self.assertContains(detail_response, "Email work")
        self.assertEqual(activity_response.status_code, 302)
        self.assertEqual(activity_response["Location"], reverse("core:lead-list"))
        self.assertEqual(list_json_response.status_code, 200)
        self.assertEqual(detail_json_response.status_code, 200)
        self.assertEqual(detail_json_response.json()["activity"], [])
        self.assertEqual(activity_json_response.status_code, 403)

    def test_agent_lead_detail_exposes_phone_and_email_work(self):
        lead = self.make_lead(
            assigned_to=self.agent,
            phone="555-0101",
            email="lead@example.com",
        )
        LeadNote.objects.create(
            lead=lead,
            agent=self.agent,
            channel=LeadNote.Channel.PHONE,
            outcome=Lead.Status.CALLED,
            note="Reached owner.",
        )
        self.client.force_login(self.agent)

        response = self.client.get(reverse("core:lead-detail", args=[lead.id]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/lead_detail.html")
        self.assertContains(response, "Phone work")
        self.assertContains(response, "Email work")
        self.assertContains(response, "Mark email done")
        self.assertContains(response, "Save phone note")
        self.assertContains(response, "Reached owner.")
        self.assertContains(
            response,
            reverse("core:lead-call-outcome", args=[lead.id]),
        )
        self.assertContains(
            response,
            reverse("core:lead-email-mark-sent", args=[lead.id]),
        )

    def test_lead_list_can_filter_by_phone_done_status(self):
        called_lead = self.make_lead(
            business_name="Called Lead",
            assigned_to=self.agent,
            phone="555-0101",
            phone_called=True,
        )
        self.make_lead(
            business_name="Uncalled Lead",
            assigned_to=self.agent,
            phone="555-0102",
            phone_called=False,
        )
        self.client.force_login(self.agent)

        response = self.client.get(
            reverse("core:lead-list"),
            {"phone_called": "yes"},
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 200)
        lead_ids = [lead["id"] for lead in response.json()["leads"]]
        self.assertEqual(lead_ids, [called_lead.id])

    def test_lead_list_ignores_invalid_filter_values(self):
        lead = self.make_lead(business_name="Filter Safe Lead")
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("core:lead-list"),
            {
                "campaign": "abc",
                "tier": "abc",
                "assigned_to": "abc",
                "status": "NOPE",
            },
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 200)
        lead_ids = [row["id"] for row in response.json()["leads"]]
        self.assertIn(lead.id, lead_ids)

    def test_admin_can_assign_lead_from_lead_list_and_return_to_list(self):
        lead = self.make_lead()
        self.client.force_login(self.admin)

        list_response = self.client.get(reverse("core:lead-list"))
        self.assertContains(list_response, "Save")
        self.assertContains(list_response, self.agent.username)

        response = self.client.post(
            reverse("core:lead-assign", args=[lead.id]),
            {
                "assigned_to": str(self.agent.id),
                "next": reverse("core:lead-list"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:lead-list"))
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.agent)

    def test_agent_lead_list_does_not_show_caller_id_tools(self):
        self.make_lead(assigned_to=self.agent, phone="555-1000")
        AgentPhoneNumber.objects.create(agent=self.agent, phone="555-0101", order=1)
        AgentPhoneNumber.objects.create(agent=self.agent, phone="555-0102", order=2)
        AgentPhoneNumber.objects.create(
            agent=self.other_agent,
            phone="555-0201",
            order=1,
        )
        self.client.force_login(self.agent)

        response = self.client.get(reverse("core:lead-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Leads")
        self.assertNotContains(response, "Your Caller IDs")
        self.assertNotContains(response, "Switch number every 30 calls")
        self.assertNotContains(response, "555-0101")
        self.assertNotContains(response, "555-0102")
        self.assertNotContains(response, "555-0201")

    def test_admin_lead_list_does_not_show_agent_phone_bar(self):
        self.make_lead()
        AgentPhoneNumber.objects.create(agent=self.agent, phone="555-0101", order=1)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("core:lead-list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Your Numbers")
        self.assertNotContains(response, "Switch number every 30 calls")
        self.assertNotContains(response, "555-0101")

    def test_sidebar_sections_match_role_scope(self):
        self.client.force_login(self.agent)
        agent_response = self.client.get(reverse("core:lead-list"))
        agent_html = agent_response.content.decode()

        self.assertContains(agent_response, "Workspace")
        self.assertNotContains(agent_response, "Tools")
        self.assertIn(reverse("core:lead-list"), agent_html)
        self.assertNotIn(reverse("core:phone-list"), agent_html)
        self.assertNotIn(reverse("core:email-list"), agent_html)
        self.assertNotIn('href="/"', agent_html)
        self.assertNotIn(reverse("core:activity-list"), agent_html)
        self.assertNotIn(reverse("core:agent-list"), agent_html)
        self.assertNotIn(reverse("core:phone-number-list"), agent_html)
        self.assertNotIn(reverse("core:campaign-list"), agent_html)

        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse("core:dashboard"))
        admin_html = admin_response.content.decode()

        self.assertContains(admin_response, "Admin")
        self.assertNotContains(admin_response, "Tools")
        self.assertIn(reverse("core:agent-list"), admin_html)
        self.assertIn(reverse("core:phone-number-list"), admin_html)
        self.assertIn(reverse("core:campaign-list"), admin_html)
        self.assertIn(reverse("core:activity-list"), admin_html)

    def test_admin_shell_pages_render_html(self):
        self.make_lead(assigned_to=self.agent)
        self.client.force_login(self.admin)

        campaign_response = self.client.get(reverse("core:campaign-list"))
        agent_response = self.client.get(reverse("core:agent-list"))
        agent_edit_response = self.client.get(
            reverse("core:agent-edit", args=[self.agent.id])
        )
        phone_response = self.client.get(reverse("core:phone-number-list"))
        email_response = self.client.get(reverse("core:email-list"))
        activity_response = self.client.get(reverse("core:activity-list"))

        self.assertEqual(campaign_response.status_code, 200)
        self.assertTemplateUsed(campaign_response, "core/campaign_list.html")
        self.assertEqual(agent_response.status_code, 200)
        self.assertTemplateUsed(agent_response, "core/agent_list.html")
        self.assertEqual(agent_edit_response.status_code, 200)
        self.assertTemplateUsed(agent_edit_response, "core/agent_form.html")
        self.assertEqual(phone_response.status_code, 200)
        self.assertTemplateUsed(phone_response, "core/phone_number_list.html")
        self.assertEqual(email_response.status_code, 200)
        self.assertTemplateUsed(email_response, "core/email_list.html")
        self.assertEqual(activity_response.status_code, 200)
        self.assertTemplateUsed(activity_response, "core/activity_list.html")

    def test_admin_can_create_agent(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("core:agent-create"),
            {
                "username": "new-agent",
                "password": "StrongPass123!",
                "first_name": "New",
                "last_name": "Agent",
                "email": "agent@example.com",
                "initials": "",
                "color": "#5b8cff",
            },
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 201)
        agent = User.objects.get(username="new-agent")
        self.assertEqual(agent.role, User.Role.AGENT)
        self.assertTrue(agent.check_password("StrongPass123!"))
        self.assertEqual(response.json()["agent"]["username"], "new-agent")

    def test_admin_cannot_create_agent_with_weak_password(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("core:agent-create"),
            {
                "username": "weak-agent",
                "password": "password123",
                "first_name": "Weak",
                "last_name": "Agent",
                "email": "weak@example.com",
                "initials": "WA",
                "color": "#5b8cff",
            },
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="weak-agent").exists())
        self.assertIn("password", response.json()["errors"])

    def test_admin_can_update_agent_without_changing_password(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("core:agent-edit", args=[self.agent.id]),
            {
                "username": "updated-agent",
                "new_password": "",
                "first_name": "Updated",
                "last_name": "Agent",
                "email": "updated@example.com",
                "initials": "UA",
                "color": "#0f766e",
                "is_active": "on",
            },
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.username, "updated-agent")
        self.assertEqual(self.agent.first_name, "Updated")
        self.assertEqual(self.agent.initials, "UA")
        self.assertEqual(self.agent.role, User.Role.AGENT)
        self.assertTrue(self.agent.check_password("password"))
        self.assertEqual(response.json()["agent"]["username"], "updated-agent")

    def test_admin_can_update_agent_password(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("core:agent-edit", args=[self.agent.id]),
            {
                "username": self.agent.username,
                "new_password": "new-password123",
                "first_name": self.agent.first_name,
                "last_name": self.agent.last_name,
                "email": self.agent.email,
                "initials": self.agent.initials,
                "color": self.agent.color,
                "is_active": "on",
            },
            **self.json_request(),
        )

        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.check_password("new-password123"))

    def test_admin_can_create_and_assign_phone_number(self):
        self.client.force_login(self.admin)

        create_response = self.client.post(
            reverse("core:phone-number-list"),
            {
                "phone": "555-0300",
                "agent": "",
                "order": "1",
                "usage_limit": "5",
                "usage_count": "0",
            },
            **self.json_request(),
        )

        self.assertEqual(create_response.status_code, 201)
        phone_number = AgentPhoneNumber.objects.get(phone="555-0300")
        self.assertIsNone(phone_number.agent)
        self.assertEqual(phone_number.usage_limit, 5)
        self.assertEqual(phone_number.usage_count, 0)

        assign_response = self.client.post(
            reverse("core:phone-number-assign", args=[phone_number.id]),
            {
                "agent": str(self.agent.id),
                "order": "2",
                "usage_limit": "10",
                "usage_count": "3",
            },
            **self.json_request(),
        )

        self.assertEqual(assign_response.status_code, 200)
        phone_number.refresh_from_db()
        self.assertEqual(phone_number.agent, self.agent)
        self.assertEqual(phone_number.order, 2)
        self.assertEqual(phone_number.usage_limit, 10)
        self.assertEqual(phone_number.usage_count, 3)

    def test_legacy_phone_list_redirects_to_leads(self):
        self.client.force_login(self.agent)

        response = self.client.get(reverse("core:phone-list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:lead-list"))

    def test_login_uses_normal_django_auth_template(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/login.html")
        self.assertContains(response, "Username")
        self.assertContains(response, "Password")

    def test_record_call_outcome_rejects_unassigned_agent(self):
        lead = self.make_lead(assigned_to=self.other_agent)
        phone_number = AgentPhoneNumber.objects.create(
            agent=self.agent,
            phone="555-0999",
            usage_limit=1,
        )

        with self.assertRaises(ValidationError):
            record_call_outcome(
                lead=lead,
                agent=self.agent,
                note="Reached owner.",
                outcome=Lead.Status.CALLED,
                agent_phone_number=phone_number,
            )

        phone_number.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.NEW)
        self.assertEqual(phone_number.usage_count, 0)
        self.assertEqual(LeadNote.objects.filter(lead=lead).count(), 0)
        self.assertEqual(ActivityLog.objects.filter(lead=lead).count(), 0)
