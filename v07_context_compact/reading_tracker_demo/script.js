// Personal Reading Tracker
const STORAGE_KEY = 'reading-tracker-books';

let books = loadBooks();
let currentFilter = 'all';

const form = document.getElementById('book-form');
const titleInput = document.getElementById('title-input');
const authorInput = document.getElementById('author-input');
const statusInput = document.getElementById('status-input');
const bookList = document.getElementById('book-list');
const emptyMessage = document.getElementById('empty-message');
const filterButtons = document.querySelectorAll('.filter-btn');

function loadBooks() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    return [];
  }
}

function saveBooks() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(books));
}

function addBook(title, author, status) {
  books.push({
    id: Date.now().toString() + Math.random().toString(16).slice(2),
    title,
    author,
    status
  });
  saveBooks();
}

function deleteBook(id) {
  books = books.filter((book) => book.id !== id);
  saveBooks();
}

function updateBookStatus(id, status) {
  const book = books.find((b) => b.id === id);
  if (book) {
    book.status = status;
    saveBooks();
  }
}

function getFilteredBooks() {
  if (currentFilter === 'all') return books;
  return books.filter((book) => book.status === currentFilter);
}

function updateSummary() {
  const counts = { 'to-read': 0, reading: 0, completed: 0 };
  books.forEach((book) => {
    if (counts[book.status] !== undefined) counts[book.status] += 1;
  });

  document.getElementById('count-to-read').textContent = counts['to-read'];
  document.getElementById('count-reading').textContent = counts.reading;
  document.getElementById('count-completed').textContent = counts.completed;
  document.getElementById('count-total').textContent = books.length;
}

function createBookItem(book) {
  const li = document.createElement('li');
  li.className = 'book-item';
  li.dataset.id = book.id;

  const info = document.createElement('div');
  info.className = 'book-info';

  const title = document.createElement('p');
  title.className = 'book-title';
  title.textContent = book.title;

  const author = document.createElement('p');
  author.className = 'book-author';
  author.textContent = book.author;

  info.append(title, author);

  const controls = document.createElement('div');
  controls.className = 'book-controls';

  const badge = document.createElement('span');
  badge.className = 'badge badge-' + book.status;
  badge.textContent = statusLabel(book.status);

  const select = document.createElement('select');
  select.className = 'status-select';
  select.setAttribute('aria-label', 'Update status for ' + book.title);
  ['to-read', 'reading', 'completed'].forEach((s) => {
    const option = document.createElement('option');
    option.value = s;
    option.textContent = statusLabel(s);
    if (s === book.status) option.selected = true;
    select.appendChild(option);
  });
  select.addEventListener('change', (e) => {
    updateBookStatus(book.id, e.target.value);
    render();
  });

  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'btn-delete';
  deleteBtn.textContent = 'Delete';
  deleteBtn.setAttribute('aria-label', 'Delete ' + book.title);
  deleteBtn.addEventListener('click', () => {
    deleteBook(book.id);
    render();
  });

  controls.append(badge, select, deleteBtn);
  li.append(info, controls);
  return li;
}

function render() {
  bookList.innerHTML = '';
  const filtered = getFilteredBooks();

  if (filtered.length === 0) {
    emptyMessage.hidden = false;
    emptyMessage.textContent = books.length > 0
      ? 'No books match this filter.'
      : 'No books yet. Add your first book above!';
  } else {
    emptyMessage.hidden = true;
    const fragment = document.createDocumentFragment();
    filtered.forEach((book) => fragment.appendChild(createBookItem(book)));
    bookList.appendChild(fragment);
  }

  updateSummary();
}

function statusLabel(status) {
  switch (status) {
    case 'to-read': return 'To Read';
    case 'reading': return 'Reading';
    case 'completed': return 'Completed';
    default: return status;
  }
}

// Form submit
form.addEventListener('submit', (e) => {
  e.preventDefault();
  const title = titleInput.value.trim();
  const author = authorInput.value.trim();
  if (!title || !author) return;

  addBook(title, author, statusInput.value);
  titleInput.value = '';
  authorInput.value = '';
  titleInput.focus();
  render();
});

// Filter buttons
filterButtons.forEach((btn) => {
  btn.addEventListener('click', () => {
    currentFilter = btn.dataset.filter;
    filterButtons.forEach((b) => b.classList.toggle('active', b === btn));
    render();
  });
});

render();