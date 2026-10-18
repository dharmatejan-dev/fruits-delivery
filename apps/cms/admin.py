from django.contrib import admin
from .models import DynamicSection, BannerItem, SectionCollectionItem


class BannerItemInline(admin.TabularInline):
    model = BannerItem
    extra = 1


class SectionCollectionItemInline(admin.TabularInline):
    model = SectionCollectionItem
    extra = 1


@admin.register(DynamicSection)
class DynamicSectionAdmin(admin.ModelAdmin):
    list_display = ('title', 'section_key', 'section_type', 'sort_order', 'is_published', 'active_from', 'active_to', 'created_at')
    list_filter = ('section_type', 'is_published')
    search_fields = ('title', 'section_key', 'subtitle')
    inlines = [BannerItemInline, SectionCollectionItemInline]


@admin.register(BannerItem)
class BannerItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'section', 'deeplink_type', 'target_id', 'sort_order', 'is_active', 'created_at')
    list_filter = ('deeplink_type', 'is_active')
    search_fields = ('title', 'target_id')


@admin.register(SectionCollectionItem)
class SectionCollectionItemAdmin(admin.ModelAdmin):
    list_display = ('section', 'product', 'sort_order', 'created_at')
    list_filter = ('section',)
    search_fields = ('section__title', 'product__name')
