const grid = document.querySelector("[data-book-grid]");

if (grid) {
  const cards = Array.from(grid.querySelectorAll("[data-book-card]"));
  const savedFilter = document.querySelector("[data-saved-filter]");
  const savedCount = document.querySelector("[data-saved-count]");
  const sortControl = document.querySelector("[data-sort]");
  const clientEmpty = document.querySelector("[data-client-empty]");
  const storageKey = "reading-room-saved-books";
  const storedBooks = JSON.parse(localStorage.getItem(storageKey) || "[]");
  const savedBooks = new Set(Array.isArray(storedBooks) ? storedBooks : []);

  const updateSavedCount = () => {
    savedCount.textContent = String(savedBooks.size);
  };

  const updateVisibleCards = () => {
    const onlySaved = savedFilter.getAttribute("aria-pressed") === "true";
    let visibleCount = 0;

    cards.forEach((card) => {
      const visible = !onlySaved || savedBooks.has(card.dataset.bookId);
      card.hidden = !visible;
      if (visible) visibleCount += 1;
    });

    clientEmpty.hidden = visibleCount > 0;
  };

  cards.forEach((card) => {
    const saveButton = card.querySelector("[data-save-book]");
    const bookId = card.querySelector(".book-cover").getAttribute("href").split("/").filter(Boolean).at(-1);
    const cover = card.querySelector("[data-cover-image]");
    card.dataset.bookId = bookId;

    if (cover.complete && cover.naturalWidth === 0) {
      cover.closest(".book-cover").classList.add("cover-missing");
    }
    cover.addEventListener("error", () => cover.closest(".book-cover").classList.add("cover-missing"));

    const isSaved = savedBooks.has(bookId);
    saveButton.setAttribute("aria-pressed", String(isSaved));
    saveButton.querySelector("span").textContent = isSaved ? "♥" : "♡";

    saveButton.addEventListener("click", () => {
      if (savedBooks.has(bookId)) {
        savedBooks.delete(bookId);
      } else {
        savedBooks.add(bookId);
      }

      const nowSaved = savedBooks.has(bookId);
      saveButton.setAttribute("aria-pressed", String(nowSaved));
      saveButton.querySelector("span").textContent = nowSaved ? "♥" : "♡";
      localStorage.setItem(storageKey, JSON.stringify(Array.from(savedBooks)));
      updateSavedCount();
      updateVisibleCards();
    });
  });

  savedFilter.addEventListener("click", () => {
    const pressed = savedFilter.getAttribute("aria-pressed") === "true";
    savedFilter.setAttribute("aria-pressed", String(!pressed));
    savedFilter.querySelector("span").textContent = !pressed ? "♥" : "♡";
    updateVisibleCards();
  });

  sortControl.addEventListener("change", () => {
    const sortBy = sortControl.value;
    const sortedCards = [...cards].sort((first, second) => {
      if (sortBy === "year") {
        return Number(second.dataset.year) - Number(first.dataset.year)
          || first.dataset.title.localeCompare(second.dataset.title);
      }
      if (sortBy === "available") {
        return Number(second.dataset.available) - Number(first.dataset.available)
          || first.dataset.title.localeCompare(second.dataset.title);
      }
      return first.dataset.title.localeCompare(second.dataset.title);
    });

    sortedCards.forEach((card) => grid.appendChild(card));
  });

  updateSavedCount();
  updateVisibleCards();
}
