"""Firebase Cloud Messaging adapter used by the notification outbox worker."""
import json

from django.conf import settings


def get_firebase_app():
    import firebase_admin

    try:
        return firebase_admin.get_app()
    except ValueError:
        options = {}
        project_id = getattr(settings, 'FIREBASE_CLOUD_PROJECT', '')
        if project_id:
            options['projectId'] = project_id
        return firebase_admin.initialize_app(options=options or None)


def send_event_to_device(event, device, app):
    from firebase_admin import messaging

    title = 'Orchard Harvest update'
    body = event.event_type.replace('.', ' ').replace('_', ' ').capitalize()
    data = {
        'event_id': str(event.pk),
        'event_type': event.event_type,
        'object_type': event.object_type,
        'object_id': event.object_id,
        'payload': json.dumps(event.payload, separators=(',', ':'), default=str),
    }
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data=data,
        token=device.registration_token,
    )
    return messaging.send(message, app=app)
