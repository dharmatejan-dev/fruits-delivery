from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.operations.fcm import get_firebase_app, send_event_to_device
from apps.operations.models import NotificationEvent, PushDevice


class Command(BaseCommand):
    help = 'Send queued notification outbox events through Firebase Cloud Messaging.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--retry-failed', action='store_true',
                            help='Requeue failed events before processing.')

    def handle(self, *args, **options):
        limit = options['limit']
        if limit < 1:
            raise CommandError('--limit must be at least 1.')
        try:
            app = get_firebase_app()
        except Exception as exc:
            raise CommandError(
                'Firebase is not configured. Set GOOGLE_APPLICATION_CREDENTIALS or '
                f'configure Application Default Credentials. Details: {exc}'
            ) from exc

        if options['retry_failed']:
            NotificationEvent.objects.filter(status=NotificationEvent.Status.FAILED).update(
                status=NotificationEvent.Status.QUEUED
            )

        sent = failed = skipped = 0
        event_ids = list(NotificationEvent.objects.filter(
            status=NotificationEvent.Status.QUEUED
        ).order_by('created_at').values_list('pk', flat=True)[:limit])

        for event_id in event_ids:
            with transaction.atomic():
                event = NotificationEvent.objects.select_for_update().get(pk=event_id)
                if event.status != NotificationEvent.Status.QUEUED:
                    continue
                devices = list(PushDevice.objects.filter(user_id=event.recipient_id, active=True))
                if not devices:
                    skipped += 1
                    continue

                successes = 0
                errors = []
                for device in devices:
                    try:
                        send_event_to_device(event, device, app)
                        successes += 1
                    except Exception as exc:
                        errors.append(str(exc)[:200])
                        try:
                            from firebase_admin import messaging
                            if isinstance(exc, messaging.UnregisteredError):
                                device.delete()
                        except (ImportError, AttributeError):
                            pass

                event.attempts += 1
                event.last_error = '; '.join(errors)[:500]
                if successes:
                    event.status = NotificationEvent.Status.SENT
                    event.sent_at = timezone.now()
                    sent += 1
                else:
                    event.status = NotificationEvent.Status.FAILED
                    failed += 1
                event.save(update_fields=['attempts', 'last_error', 'status', 'sent_at'])

        self.stdout.write(self.style.SUCCESS(
            f'Processed {len(event_ids)} events: {sent} sent, {failed} failed, {skipped} without active devices.'
        ))
