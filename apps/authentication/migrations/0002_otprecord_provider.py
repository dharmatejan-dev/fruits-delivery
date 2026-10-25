from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='otprecord',
            name='provider',
            field=models.CharField(
                choices=[
                    ('development_mock', 'Development Mock'),
                    ('otp_com_test', 'OTP.com Test'),
                    ('otp_com_live', 'OTP.com Live'),
                    ('msg91', 'MSG91'),
                ],
                default='msg91',
                max_length=20,
            ),
        ),
    ]
