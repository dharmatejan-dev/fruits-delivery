from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.operations.models import ClaimPhoto


class Command(BaseCommand):
    help = 'Copy existing local claim photos to Cloudinary and update their stored names.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--media-root',
            default=str(settings.BASE_DIR / 'media'),
            help='Path to the legacy Django media directory.',
        )

    def handle(self, *args, **options):
        local_storage = FileSystemStorage(location=options['media_root'])
        photos = ClaimPhoto.objects.exclude(image='')
        migrated = missing = 0

        for photo in photos.iterator():
            old_name = photo.image.name
            if old_name.startswith('orchard-harvest/'):
                continue
            if not local_storage.exists(old_name):
                missing += 1
                self.stderr.write(f'Missing legacy image: {old_name}')
                continue
            if options['dry_run']:
                self.stdout.write(f'Would upload: {old_name}')
                continue

            target_name = f'legacy-claims/{photo.claim_id}/{Path(old_name).name}'
            with local_storage.open(old_name, 'rb') as source:
                cloud_name = default_storage.save(target_name, File(source))
            photo.image.name = cloud_name
            photo.save(update_fields=['image'])
            migrated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Cloudinary migration complete: {migrated} uploaded, {missing} missing.'
        ))
        if missing:
            raise CommandError('Some legacy image files were not found; see the missing paths above.')
