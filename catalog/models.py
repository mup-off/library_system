from decimal import Decimal

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Author(models.Model):
    name = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Book(models.Model):
    title = models.CharField(max_length=300)
    isbn = models.CharField(
        max_length=13,
        unique=True,
        validators=[RegexValidator(r"^(\d{10}|\d{13})$", "ISBN must be 10 or 13 digits, no hyphens.")],
    )
    authors = models.ManyToManyField(Author, related_name="books")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="books")
    description = models.TextField(blank=True)
    published_year = models.PositiveSmallIntegerField(null=True, blank=True)
    total_copies = models.PositiveIntegerField(default=1)
    available_copies = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.CheckConstraint(condition=Q(available_copies__lte=F("total_copies")), name="available_lte_total"),
        ]

    def __str__(self):
        return self.title

    @property
    def is_available(self):
        return self.available_copies > 0


class Loan(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loans")
    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name="loans")
    borrowed_at = models.DateTimeField(default=timezone.now)
    due_date = models.DateField()
    returned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-borrowed_at"]
        constraints = [
            # A member cannot hold two copies of the same title at once (enforced by the DB, not just app code).
            models.UniqueConstraint(
                fields=["user", "book"],
                condition=Q(returned_at__isnull=True),
                name="one_active_loan_per_user_book",
            ),
        ]

    def __str__(self):
        return f"{self.user} -> {self.book}"

    @property
    def is_active(self):
        return self.returned_at is None

    @property
    def days_overdue(self):
        end = self.returned_at.date() if self.returned_at else timezone.localdate()
        return max((end - self.due_date).days, 0)

    @property
    def is_overdue(self):
        return self.is_active and self.days_overdue > 0

    @property
    def fine(self):
        return (Decimal(str(settings.FINE_PER_DAY)) * self.days_overdue).quantize(Decimal("0.01"))
