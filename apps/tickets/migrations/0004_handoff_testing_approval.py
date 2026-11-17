from django.db import migrations


def to_testing_approval(apps, schema_editor):
    """Solved tickets saved with the old wording now read "... Testing/approval"."""
    Ticket = apps.get_model("tickets", "Ticket")
    for t in Ticket.objects.filter(handoff_status__iendswith=" testing"):
        t.handoff_status = t.handoff_status + "/approval"
        t.save(update_fields=["handoff_status"])


def back_to_testing(apps, schema_editor):
    Ticket = apps.get_model("tickets", "Ticket")
    for t in Ticket.objects.filter(handoff_status__iendswith=" testing/approval"):
        t.handoff_status = t.handoff_status[: -len("/approval")]
        t.save(update_fields=["handoff_status"])


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0003_ticket_solved_status'),
    ]

    operations = [
        migrations.RunPython(to_testing_approval, back_to_testing),
    ]
