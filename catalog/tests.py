"""Authorization and business-rule tests for the catalogue and loans."""
from datetime import timedelta

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .models import Author, Book, Category, Loan
from .services import LibraryError, borrow_book, return_loan


def user(email="m@example.com"):
    return User.objects.create_user(email=email, password="Correct-Horse-Battery-9", full_name="Member One")


def book(title="Dune", isbn="9780441013593", copies=1):
    b = Book.objects.create(title=title, isbn=isbn, total_copies=copies, available_copies=copies)
    b.authors.add(Author.objects.get_or_create(name="Frank Herbert")[0])
    return b


class AccessControlTests(TestCase):
    def test_anonymous_users_are_redirected_everywhere(self):
        b = book()
        for url in [reverse("catalog:book_list"), reverse("catalog:book_detail", args=[b.pk]), reverse("catalog:my_loans")]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn(reverse("accounts:login"), r["Location"])
        for url in [reverse("catalog:borrow", args=[b.pk]), reverse("catalog:return_book", args=[1])]:
            self.assertEqual(self.client.post(url).status_code, 302, url)
        self.assertEqual(Loan.objects.count(), 0)

    def test_borrow_and_return_reject_get(self):
        u = user(); b = book(); self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("catalog:borrow", args=[b.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse("catalog:return_book", args=[1])).status_code, 405)

    def test_csrf_enforced_on_borrow(self):
        u = user(); b = book()
        c = Client(enforce_csrf_checks=True); c.force_login(u)
        self.assertEqual(c.post(reverse("catalog:borrow", args=[b.pk])).status_code, 403)

    def test_cannot_return_someone_elses_loan_idor(self):
        alice, mallory = user("alice@example.com"), user("mallory@example.com")
        b = book()
        loan = borrow_book(alice, b.pk)
        self.client.force_login(mallory)
        r = self.client.post(reverse("catalog:return_book", args=[loan.pk]))
        self.assertEqual(r.status_code, 404)
        loan.refresh_from_db()
        self.assertIsNone(loan.returned_at)

    def test_my_loans_only_shows_own_loans(self):
        alice, bob = user("alice@example.com"), user("bob@example.com")
        borrow_book(alice, book("Secret Alice Book", "9780000000001").pk)
        self.client.force_login(bob)
        self.assertNotContains(self.client.get(reverse("catalog:my_loans")), "Secret Alice Book")

    def test_search_input_is_escaped(self):
        u = user(); self.client.force_login(u)
        r = self.client.get(reverse("catalog:book_list"), {"q": "<script>alert(1)</script>"})
        self.assertNotContains(r, "<script>alert(1)</script>")

    def test_non_numeric_category_filter_is_ignored(self):
        u = user(); self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("catalog:book_list"), {"category": "1 OR 1=1"}).status_code, 200)


class LoanRuleTests(TestCase):
    def setUp(self):
        self.u = user()

    def test_borrow_decrements_stock_and_sets_due_date(self):
        b = book(copies=2)
        loan = borrow_book(self.u, b.pk)
        b.refresh_from_db()
        self.assertEqual(b.available_copies, 1)
        self.assertEqual(loan.due_date, timezone.localdate() + timedelta(days=settings.LOAN_DAYS))

    def test_cannot_borrow_when_no_copies(self):
        b = book(copies=1)
        borrow_book(user("first@example.com"), b.pk)
        with self.assertRaises(LibraryError):
            borrow_book(self.u, b.pk)

    def test_cannot_borrow_same_book_twice(self):
        b = book(copies=3)
        borrow_book(self.u, b.pk)
        with self.assertRaises(LibraryError):
            borrow_book(self.u, b.pk)
        b.refresh_from_db()
        self.assertEqual(b.available_copies, 2)

    def test_max_active_loans_enforced(self):
        for i in range(settings.MAX_ACTIVE_LOANS):
            borrow_book(self.u, book(f"B{i}", f"97800000000{i:02d}").pk)
        with self.assertRaises(LibraryError):
            borrow_book(self.u, book("Extra", "9789999999999").pk)

    def test_return_restores_stock_and_cannot_double_return(self):
        b = book()
        loan = borrow_book(self.u, b.pk)
        return_loan(self.u, loan.pk)
        b.refresh_from_db()
        self.assertEqual(b.available_copies, 1)
        with self.assertRaises(LibraryError):
            return_loan(self.u, loan.pk)
        b.refresh_from_db()
        self.assertEqual(b.available_copies, 1)

    def test_overdue_fine_calculation(self):
        b = book()
        loan = borrow_book(self.u, b.pk)
        loan.due_date = timezone.localdate() - timedelta(days=4)
        loan.save()
        self.assertTrue(loan.is_overdue)
        self.assertEqual(loan.days_overdue, 4)
        self.assertEqual(str(loan.fine), "1.00")

    def test_borrow_unknown_book_404(self):
        self.client.force_login(self.u)
        self.assertEqual(self.client.post(reverse("catalog:borrow", args=[9999])).status_code, 404)

    def test_end_to_end_via_views(self):
        b = book(); self.client.force_login(self.u)
        self.client.post(reverse("catalog:borrow", args=[b.pk]))
        self.assertEqual(Loan.objects.filter(user=self.u, returned_at__isnull=True).count(), 1)
        loan = Loan.objects.get()
        self.client.post(reverse("catalog:return_book", args=[loan.pk]))
        loan.refresh_from_db()
        self.assertIsNotNone(loan.returned_at)

    def test_isbn_validation(self):
        from django.core.exceptions import ValidationError
        b = Book(title="Bad", isbn="12-34", total_copies=1, available_copies=1)
        with self.assertRaises(ValidationError):
            b.full_clean()
