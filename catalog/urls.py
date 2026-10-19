from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.book_list, name="book_list"),
    path("books/<int:pk>/", views.book_detail, name="book_detail"),
    path("books/<int:pk>/borrow/", views.borrow, name="borrow"),
    path("loans/", views.my_loans, name="my_loans"),
    path("loans/<int:loan_id>/return/", views.return_book, name="return_book"),
]
