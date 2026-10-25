import logging
import re

import requests
from django.conf import settings
from rest_framework.exceptions import APIException, ValidationError

logger = logging.getLogger(__name__)


class OTPProviderUnavailable(APIException):
    status_code = 503
    default_detail = 'The OTP service is temporarily unavailable.'
    default_code = 'otp_provider_unavailable'


class DevelopmentMockProvider:
    name = 'development_mock'
    code_length = 4
    uses_remote_verification = False

    def __init__(self):
        if settings.APP_ENV != 'development' or not settings.DEBUG:
            raise OTPProviderUnavailable(
                'Development OTP provider requires APP_ENV=development and DEBUG=True.'
            )
        self.code = str(settings.OTP_MOCK_CODE)
        if not self.code.isdigit() or not 4 <= len(self.code) <= 6:
            raise OTPProviderUnavailable('Development OTP code configuration is invalid.')
        self.code_length = len(self.code)

    def generate_code(self):
        return self.code

    def send(self, phone_number, client_ip=None, code=None):
        logger.info('Development OTP mock accepted a request; no message was sent.')
        return {'session_id': '', 'debug_otp': self.code if settings.DEBUG else None}

    def verify(self, session_id, code, otp_hash):
        from django.contrib.auth.hashers import check_password

        return check_password(code, otp_hash)


class OTPComProvider:
    uses_remote_verification = True
    environment = None
    name = None
    key_prefix = None

    def __init__(self):
        api_key = settings.OTP_COM_SERVER_API_KEY
        if self.environment == 'test' and settings.APP_ENV == 'production':
            raise OTPProviderUnavailable(
                'OTP.com Test keys cannot be used in production.'
            )
        if self.environment == 'live' and settings.APP_ENV != 'production':
            raise OTPProviderUnavailable(
                'OTP.com Live keys can only be used when APP_ENV=production.'
            )
        if (
            settings.OTP_COM_ENVIRONMENT != self.environment
            or not api_key.startswith(self.key_prefix)
        ):
            raise OTPProviderUnavailable(
                f'OTP.com {self.environment.title()} requires a matching Server API key.'
            )
        if not 4 <= settings.OTP_COM_CODE_LENGTH <= 6:
            raise OTPProviderUnavailable('OTP.com code length configuration is invalid.')
        self.code_length = settings.OTP_COM_CODE_LENGTH
        self.api_key = api_key
        self.base_url = settings.OTP_COM_API_BASE_URL.rstrip('/')

    def generate_code(self):
        return None

    def _post(self, path, payload):
        try:
            response = requests.post(
                f'{self.base_url}{path}',
                json=payload,
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                },
                timeout=(3.05, 10),
            )
        except requests.RequestException as exc:
            logger.warning('OTP.com request failed (%s).', type(exc).__name__)
            raise OTPProviderUnavailable() from exc

        if response.status_code >= 400:
            logger.warning('OTP.com returned HTTP %s.', response.status_code)
            raise OTPProviderUnavailable()
        try:
            return response.json()
        except ValueError as exc:
            logger.warning('OTP.com returned a non-JSON response.')
            raise OTPProviderUnavailable() from exc

    def send(self, phone_number, client_ip=None, code=None):
        payload = {'recipient': phone_number, 'locale': 'en'}
        if client_ip:
            payload['client_ip'] = client_ip
        response = self._post('/otp/send', payload)
        otp_id = response.get('otp_id')
        if not isinstance(otp_id, str) or not otp_id:
            logger.warning('OTP.com send response omitted the OTP ID.')
            raise OTPProviderUnavailable()
        return {'session_id': otp_id, 'debug_otp': None}

    def verify(self, session_id, code, otp_hash=None):
        if not session_id:
            raise OTPProviderUnavailable()
        response = self._post('/otp/verify', {'otp_id': session_id, 'code': code})
        matched = response.get('matched')
        if not isinstance(matched, bool):
            logger.warning('OTP.com verification response omitted the match result.')
            raise OTPProviderUnavailable()
        return matched


class OTPComTestProvider(OTPComProvider):
    name = 'otp_com_test'
    environment = 'test'
    key_prefix = 'otp_test_'


class OTPComLiveProvider(OTPComProvider):
    name = 'otp_com_live'
    environment = 'live'
    key_prefix = 'otp_live_'


class TwoFactorProvider:
    """Server-side OTP generation and verification through 2Factor."""
    name = 'twofactor'
    code_length = 6
    uses_remote_verification = True

    def __init__(self):
        self.api_key = settings.TWOFACTOR_API_KEY.strip()
        if not self.api_key:
            raise OTPProviderUnavailable('2Factor API key is not configured.')
        self.base_url = settings.TWOFACTOR_API_BASE_URL.rstrip('/')

    def generate_code(self):
        # 2Factor creates the code and retains it for verification by session ID.
        return None

    def _get(self, path):
        try:
            response = requests.get(
                f'{self.base_url}/{self.api_key}/{path}',
                timeout=(3.05, 10),
            )
        except requests.RequestException as exc:
            # Do not log the URL: the 2Factor API key is part of its path.
            logger.warning('2Factor request failed (%s).', type(exc).__name__)
            raise OTPProviderUnavailable() from None

        if response.status_code >= 400:
            logger.warning('2Factor returned HTTP %s.', response.status_code)
            raise OTPProviderUnavailable()
        try:
            result = response.json()
        except ValueError as exc:
            logger.warning('2Factor returned a non-JSON response.')
            raise OTPProviderUnavailable() from exc
        if not isinstance(result, dict):
            raise OTPProviderUnavailable()
        return result

    def send(self, phone_number, client_ip=None, code=None):
        # 2Factor's India AUTOGEN endpoint takes a 10-digit mobile number.
        local_number = phone_number.removeprefix('+91')
        result = self._get(f'SMS/{local_number}/AUTOGEN')
        session_id = result.get('Details')
        if (
            str(result.get('Status', '')).casefold() != 'success'
            or not isinstance(session_id, str)
            or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', session_id)
        ):
            logger.warning('2Factor did not accept the OTP request.')
            raise OTPProviderUnavailable()
        return {'session_id': session_id, 'debug_otp': None}

    def verify(self, session_id, code, otp_hash=None):
        if (
            not re.fullmatch(r'[A-Za-z0-9-]{1,100}', session_id or '')
            or not code.isdigit()
            or len(code) != self.code_length
        ):
            return False
        result = self._get(f'SMS/VERIFY/{session_id}/{code}')
        details = str(result.get('Details', '')).casefold()
        if (
            str(result.get('Status', '')).casefold() == 'success'
            and 'otp matched' in details
        ):
            return True
        if str(result.get('Status', '')).casefold() != 'success':
            if any(term in details for term in ('api key', 'balance', 'service unavailable', 'server error')):
                logger.warning('2Factor rejected OTP verification due to provider configuration or service status.')
                raise OTPProviderUnavailable()
            return False
        logger.warning('2Factor returned an unexpected OTP verification result.')
        raise OTPProviderUnavailable()


class MSG91ProductionProvider:
    name = 'msg91'
    code_length = 6
    uses_remote_verification = False

    def __init__(self):
        self.auth_key = settings.MSG91_AUTH_KEY
        self.template_id = settings.MSG91_OTP_TEMPLATE_ID
        if not self.auth_key or self.auth_key.startswith('mock_') or not self.template_id:
            raise OTPProviderUnavailable('MSG91 production credentials are not configured.')

    def generate_code(self):
        import secrets

        return str(secrets.randbelow(900000) + 100000)

    def send(self, phone_number, client_ip=None, code=None):
        payload = {
            'template_id': self.template_id,
            'mobile': phone_number.replace('+', ''),
            'otp': code,
            'sender': settings.MSG91_SENDER_ID,
        }
        try:
            response = requests.post(
                'https://control.msg91.com/api/v5/otp',
                json=payload,
                headers={'authkey': self.auth_key, 'Content-Type': 'application/json'},
                timeout=(3.05, 10),
            )
        except requests.RequestException as exc:
            logger.warning('MSG91 request failed (%s).', type(exc).__name__)
            raise OTPProviderUnavailable() from exc
        if response.status_code != 200:
            logger.warning('MSG91 returned HTTP %s.', response.status_code)
            raise OTPProviderUnavailable()
        try:
            response_data = response.json()
        except ValueError as exc:
            raise OTPProviderUnavailable() from exc
        if response_data.get('type') != 'success':
            logger.warning('MSG91 did not accept the OTP request.')
            raise OTPProviderUnavailable()
        return {'session_id': str(response_data.get('request_id', '')), 'debug_otp': None}

    def verify(self, session_id, code, otp_hash):
        from django.contrib.auth.hashers import check_password

        return check_password(code, otp_hash)


def get_otp_provider(name=None):
    provider_name = name or settings.CUSTOMER_OTP_PROVIDER
    providers = {
        'development_mock': DevelopmentMockProvider,
        'otp_com_test': OTPComTestProvider,
        'otp_com_live': OTPComLiveProvider,
        'twofactor': TwoFactorProvider,
        'msg91': MSG91ProductionProvider,
    }
    provider_class = providers.get(provider_name)
    if provider_class is None:
        raise OTPProviderUnavailable('Configured OTP provider is not supported.')
    return provider_class()
