from django.urls import path
from .views import (
    AdminAssignPartnerView,
    AdminReassignPartnerView,
    AdminAssignmentListView,
    AdminLiveMapView,
    AdminUpdateOrderStatusView,
    PartnerJobListView,
    PartnerJobDetailView,
    PartnerStatusTransitionView,
    PartnerGPSPingView,
    CustomerLiveTrackingView,
)

urlpatterns = [
    # ─── Admin Dispatch Endpoints ───────────────────────────────────────
    # POST   /api/v1/dispatch/assign/             → assign partner to packed order
    path('assign/', AdminAssignPartnerView.as_view(), name='dispatch-assign'),

    # POST   /api/v1/dispatch/reassign/           → reassign to a different partner
    path('reassign/', AdminReassignPartnerView.as_view(), name='dispatch-reassign'),

    # GET    /api/v1/dispatch/assignments/        → list all assignments (admin)
    path('assignments/', AdminAssignmentListView.as_view(), name='dispatch-assignment-list'),

    # GET    /api/v1/dispatch/live-map/           → admin live delivery map
    path('live-map/', AdminLiveMapView.as_view(), name='dispatch-live-map'),

    # PATCH  /api/v1/dispatch/orders/<id>/pack/  → mark order as packed (admin)
    path('orders/<uuid:order_id>/pack/', AdminUpdateOrderStatusView.as_view(), name='dispatch-pack-order'),

    # ─── Partner Endpoints ──────────────────────────────────────────────
    # GET    /api/v1/dispatch/partner/jobs/                          → partner job list
    path('partner/jobs/', PartnerJobListView.as_view(), name='partner-job-list'),

    # GET    /api/v1/dispatch/partner/jobs/<id>/                     → partner job detail
    path('partner/jobs/<uuid:assignment_id>/', PartnerJobDetailView.as_view(), name='partner-job-detail'),

    # POST   /api/v1/dispatch/partner/jobs/<id>/transition/          → state machine transition
    path('partner/jobs/<uuid:assignment_id>/transition/', PartnerStatusTransitionView.as_view(), name='partner-job-transition'),

    # POST   /api/v1/dispatch/partner/jobs/<id>/gps/                 → GPS ping submission
    path('partner/jobs/<uuid:assignment_id>/gps/', PartnerGPSPingView.as_view(), name='partner-gps-ping'),

    # ─── Customer Tracking ──────────────────────────────────────────────
    # GET    /api/v1/dispatch/track/<order_id>/   → live tracking for customer
    path('track/<uuid:order_id>/', CustomerLiveTrackingView.as_view(), name='customer-live-tracking'),
]
