from django.core.management.base import BaseCommand

from catalog.models import Author, Book, Category

SAMPLE = [
    ("Pride and Prejudice", "9780141439518", "Jane Austen", "Classics", 1813, 3),
    ("Nineteen Eighty-Four", "9780451524935", "George Orwell", "Classics", 1949, 2),
    ("Dune", "9780441013593", "Frank Herbert", "Science Fiction", 1965, 2),
    ("The Left Hand of Darkness", "9780441478125", "Ursula K. Le Guin", "Science Fiction", 1969, 1),
    ("A Brief History of Time", "9780553380163", "Stephen Hawking", "Science", 1988, 2),
    ("The Selfish Gene", "9780198788607", "Richard Dawkins", "Science", 1976, 1),
    ("Clean Code", "9780132350884", "Robert C. Martin", "Technology", 2008, 2),
    ("The Pragmatic Programmer", "9780135957059", "Andrew Hunt", "Technology", 2019, 2),
    ("Sapiens", "9780062316097", "Yuval Noah Harari", "History", 2011, 3),
    ("Persepolis", "9780375714832", "Marjane Satrapi", "Graphic Novels", 2000, 1),
    ("Beloved", "9781400033416", "Toni Morrison", "Classics", 1987, 1),
    ("Norwegian Wood", "9780375704024", "Haruki Murakami", "Fiction", 1987, 2),
]


class Command(BaseCommand):
    help = "Load sample authors, categories and books (idempotent)."

    def handle(self, *args, **opts):
        created = 0
        for title, isbn, author, cat, year, copies in SAMPLE:
            a, _ = Author.objects.get_or_create(name=author)
            c, _ = Category.objects.get_or_create(name=cat)
            book, was_new = Book.objects.get_or_create(
                isbn=isbn,
                defaults={"title": title, "category": c, "published_year": year,
                          "total_copies": copies, "available_copies": copies,
                          "description": f"Sample record for “{title}”."},
            )
            book.authors.add(a)
            created += was_new
        self.stdout.write(self.style.SUCCESS(f"Seeded {created} new book(s); {Book.objects.count()} total."))
