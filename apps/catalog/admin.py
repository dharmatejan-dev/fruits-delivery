from django.contrib import admin
from .models import Category, Product, ProductImage, ProductVariant


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'sort_order', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('title', 'slug', 'description')
    prepopulated_fields = {'slug': ('title',)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'is_seasonal', 'is_featured', 'is_active', 'created_at')
    list_filter = ('category', 'is_seasonal', 'is_featured', 'is_active')
    search_fields = ('name', 'slug', 'short_description')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductVariantInline, ProductImageInline]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ('product', 'unit_quantity', 'unit_type', 'mrp_inr', 'selling_price_inr', 'is_default', 'is_active')
    list_filter = ('unit_type', 'is_default', 'is_active')
    search_fields = ('product__name',)


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ('product', 'is_primary', 'sort_order', 'created_at')
    list_filter = ('is_primary',)
