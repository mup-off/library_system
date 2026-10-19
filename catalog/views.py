from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .models import Book, Category, Loan


@login_required
def book_list(request):
    qs = Book.objects.select_related("category").prefetch_related("authors")
    q = request.GET.get("q", "").strip()[:100]
    cat = request.GET.get("category", "")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(authors__name__icontains=q) | Q(isbn__icontains=q)).distinct()
    if cat.isdigit():
        qs = qs.filter(category_id=int(cat))
    page = Paginator(qs, 12).get_page(request.GET.get("page"))
    return render(request, "catalog/book_list.html", {
        "page": page, "q": q, "categories": Category.objects.all(), "selected_category": cat,
    })


@login_required
def book_detail(request, pk):
    book = get_object_or_404(Book.objects.select_related("category").prefetch_related("authors"), pk=pk)
    has_loan = Loan.objects.filter(user=request.user, book=book, returned_at__isnull=True).exists()
    return render(request, "catalog/book_detail.html", {"book": book, "has_loan": has_loan})


@login_required
@require_POST
def borrow(request, pk):
    try:
        loan = services.borrow_book(request.user, pk)
        messages.success(request, f"Borrowed “{loan.book.title}”. Due {loan.due_date:%d %b %Y}.")
    except services.LibraryError as exc:
        messages.error(request, str(exc))
    return redirect("catalog:book_detail", pk=pk)


@login_required
@require_POST
def return_book(request, loan_id):
    try:
        loan = services.return_loan(request.user, loan_id)
        msg = f"Returned “{loan.book.title}”."
        if loan.fine:
            msg += f" Late fine: {loan.fine}."
        messages.success(request, msg)
    except services.LibraryError as exc:
        messages.error(request, str(exc))
    return redirect("catalog:my_loans")


@login_required
def my_loans(request):
    loans = Loan.objects.filter(user=request.user).select_related("book").prefetch_related("book__authors")
    return render(request, "catalog/my_loans.html", {
        "active": [l for l in loans if l.is_active],
        "history": [l for l in loans if not l.is_active][:20],
    })
