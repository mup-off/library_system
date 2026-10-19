from django.contrib import admin, messages
from django.utils import timezone

from .models import Author, Book, Category, Loan


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("title", "isbn", "category", "available_copies", "total_copies")
    list_filter = ("category",)
    search_fields = ("title", "isbn", "authors__name")
    autocomplete_fields = ("authors",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.available_copies = obj.total_copies
        elif "total_copies" in form.changed_data:
            old = Book.objects.get(pk=obj.pk)
            obj.available_copies = max(0, old.available_copies + (obj.total_copies - old.total_copies))
        super().save_model(request, obj, form, change)


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("book", "user", "borrowed_at", "due_date", "returned_at")
    list_filter = ("returned_at", "due_date")
    search_fields = ("book__title", "user__email")
    readonly_fields = ("borrowed_at",)
    actions = ["mark_returned"]

    @admin.action(description="Mark selected loans as returned")
    def mark_returned(self, request, queryset):
        count = 0
        for loan in queryset.filter(returned_at__isnull=True).select_related("book"):
            loan.returned_at = timezone.now()
            loan.save(update_fields=["returned_at"])
            book = loan.book
            book.available_copies = min(book.available_copies + 1, book.total_copies)
            book.save(update_fields=["available_copies"])
            count += 1
        self.message_user(request, f"{count} loan(s) marked returned.", messages.SUCCESS)
