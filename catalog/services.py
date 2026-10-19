"""Borrow / return business rules. All stock changes happen under a row lock."""
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Book, Loan


class LibraryError(Exception):
    """A rule violation whose message is safe to show to the member."""


def borrow_book(user, book_id):
    with transaction.atomic():
        book = get_object_or_404(Book.objects.select_for_update(), pk=book_id)
        active = Loan.objects.filter(user=user, returned_at__isnull=True)
        if active.filter(book=book).exists():
            raise LibraryError("You already have this book on loan.")
        if active.count() >= settings.MAX_ACTIVE_LOANS:
            raise LibraryError(f"You can borrow at most {settings.MAX_ACTIVE_LOANS} books at a time.")
        if book.available_copies < 1:
            raise LibraryError("No copies are available right now.")
        try:
            with transaction.atomic():
                loan = Loan.objects.create(
                    user=user, book=book, due_date=timezone.localdate() + timedelta(days=settings.LOAN_DAYS)
                )
        except IntegrityError:
            raise LibraryError("You already have this book on loan.")
        book.available_copies -= 1
        book.save(update_fields=["available_copies"])
        return loan


def return_loan(user, loan_id):
    """Members can only return their own loans (queryset is scoped to the user -> 404 otherwise)."""
    with transaction.atomic():
        loan = get_object_or_404(Loan.objects.select_for_update().select_related("book"), pk=loan_id, user=user)
        if loan.returned_at is not None:
            raise LibraryError("This book was already returned.")
        loan.returned_at = timezone.now()
        loan.save(update_fields=["returned_at"])
        book = Book.objects.select_for_update().get(pk=loan.book_id)
        book.available_copies = min(book.available_copies + 1, book.total_copies)
        book.save(update_fields=["available_copies"])
        return loan
