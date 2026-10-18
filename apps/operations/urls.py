from django.urls import path
from .views import (
    AdminPartnerListCreateView,
    AdminPartnerDetailView,
    AdminPartnerAvailabilityView,
    AdminPartnerActivationView,
    CustomerQualityClaimListCreateView,
    CustomerQualityClaimDetailView,
    AdminQualityClaimListView,
    AdminQualityClaimDetailView,
    AdminQualityClaimReviewView,
    AdminQualityClaimResolveView,
    AdminCustomerListView,
    AdminCustomerDetailView,
    AdminAuditLogListView,
    AdminOperationsSummaryView,
    AdminNotificationQueueView,
    PushDeviceRegistrationView,
)

urlpatterns = [
    # ─── Partner Provisioning (Admin) ───────────────────────────────────
    # GET  /api/v1/operations/partners/              → list partners
    # POST /api/v1/operations/partners/              → create partner
    path('partners/', AdminPartnerListCreateView.as_view(), name='partner-list-create'),

    # GET   /api/v1/operations/partners/<id>/        → partner detail
    # PATCH /api/v1/operations/partners/<id>/        → update profile
    path('partners/<uuid:partner_id>/', AdminPartnerDetailView.as_view(), name='partner-detail'),

    # PATCH /api/v1/operations/partners/<id>/availability/  → toggle availability
    path('partners/<uuid:partner_id>/availability/', AdminPartnerAvailabilityView.as_view(), name='partner-availability'),

    # PATCH /api/v1/operations/partners/<id>/activation/    → activate/deactivate
    path('partners/<uuid:partner_id>/activation/', AdminPartnerActivationView.as_view(), name='partner-activation'),

    # ─── Quality Claims (Customer) ──────────────────────────────────────
    # GET  /api/v1/operations/claims/                → customer's own claims
    # POST /api/v1/operations/claims/                → file new claim with photos
    path('claims/', CustomerQualityClaimListCreateView.as_view(), name='claim-list-create'),

    # GET  /api/v1/operations/claims/<id>/           → customer claim detail
    path('claims/<uuid:claim_id>/', CustomerQualityClaimDetailView.as_view(), name='claim-detail'),

    # ─── Quality Claims (Admin) ─────────────────────────────────────────
    # GET  /api/v1/operations/admin/claims/          → all claims (admin)
    path('admin/claims/', AdminQualityClaimListView.as_view(), name='admin-claim-list'),

    # GET  /api/v1/operations/admin/claims/<id>/      → claim detail (admin)
    path('admin/claims/<uuid:claim_id>/', AdminQualityClaimDetailView.as_view(), name='admin-claim-detail'),

    # PATCH /api/v1/operations/admin/claims/<id>/review/    → mark under review
    path('admin/claims/<uuid:claim_id>/review/', AdminQualityClaimReviewView.as_view(), name='admin-claim-review'),

    # POST  /api/v1/operations/admin/claims/<id>/resolve/   → resolve/reject
    path('admin/claims/<uuid:claim_id>/resolve/', AdminQualityClaimResolveView.as_view(), name='admin-claim-resolve'),

    # ─── Customers, reporting, and audit history ───────────────────────
    path('admin/customers/', AdminCustomerListView.as_view(), name='admin-customer-list'),
    path('admin/customers/<uuid:customer_id>/', AdminCustomerDetailView.as_view(), name='admin-customer-detail'),
    path('admin/audit-logs/', AdminAuditLogListView.as_view(), name='admin-audit-logs'),
    path('admin/notifications/queue/', AdminNotificationQueueView.as_view(), name='admin-notification-queue'),
    path('push-devices/', PushDeviceRegistrationView.as_view(), name='push-device-registration'),
    path('admin/reports/summary/', AdminOperationsSummaryView.as_view(), name='admin-operations-summary'),
]
