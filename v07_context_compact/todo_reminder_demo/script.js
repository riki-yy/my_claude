(function () {
  'use strict';

  const STORAGE_KEY = 'bookTracker.books';

  // ---- DOM references ----
  const form = document.getElementById('add-book-form');
  const titleInput = document.getElementById('book-title');
  const authorInput = document.getElementById('book-author');
  const statusSelect = document.getElementById('book-status');
  const bookListEl = document.getElementById('book-list');
  const bookCountEl = document.getElementById('book-count');
  const emptyMessageEl = document.getElementById('empty-message');
  const filterBtns = document.querySelectorAll('.filter-btn');
  const statTotalEl = document.getElementById('stat-total');
  const statReadingEl = document.getElementById('stat-reading');
  const statCompletedEl = document.getElementById('stat-completed');
  const statWishlistEl = document.getElementById('stat-wishlist');

  // ---- State ----
  let books = loadBooks();
  let currentFilter = 'all';

  // ---- Storage helpers ----
  function loadBooks() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const data = JSON.parse(raw);
      return Array.isArray(data) ? data : [];
    } catch (e) {
      console.error('读取本地存储失败:', e);
      return [];
    }
  }

  function saveBooks() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(books));
    } catch (e) {
      console.error('保存到本地存储失败:', e);
    }
  }

  // ---- Status helpers ----
  const STATUS_LABELS = {
    reading: '在读',
    completed: '已读',
    wishlist: '想读'
  };

  const STATUS_CLASSES = {
    reading: 'status-reading',
    completed: 'status-completed',
    wishlist: 'status-wishlist'
  };

  // ---- Render ----
  function render() {
    const filteredBooks = books.filter(function (book) {
      if (currentFilter === 'all') return true;
      return book.status === currentFilter;
    });

    bookListEl.innerHTML = '';
    bookCountEl.textContent = String(filteredBooks.length);

    filteredBooks.forEach(function (book) {
      const li = document.createElement('li');
      li.className = 'book-item';
      li.dataset.id = book.id;

      const info = document.createElement('div');
      info.className = 'book-info';

      const title = document.createElement('div');
      title.className = 'book-title';
      title.textContent = book.title;
      info.appendChild(title);

      if (book.author) {
        const author = document.createElement('div');
        author.className = 'book-author';
        author.textContent = book.author;
        info.appendChild(author);
      }

      const badge = document.createElement('span');
      badge.className = 'status-badge ' + (STATUS_CLASSES[book.status] || '');
      badge.textContent = STATUS_LABELS[book.status] || book.status;
      info.appendChild(badge);

      li.appendChild(info);

      const actions = document.createElement('div');
      actions.className = 'book-actions';

      const select = document.createElement('select');
      select.setAttribute('aria-label', '更新「' + book.title + '」的阅读状态');
      select.innerHTML =
        '<option value="reading">在读</option>' +
        '<option value="completed">已读</option>' +
        '<option value="wishlist">想读</option>';
      select.value = book.status;
      select.addEventListener('change', function () {
        updateStatus(book.id, select.value);
      });
      actions.appendChild(select);

      const deleteBtn = document.createElement('button');
      deleteBtn.className = 'btn btn-delete';
      deleteBtn.type = 'button';
      deleteBtn.textContent = '删除';
      deleteBtn.setAttribute('aria-label', '删除《' + book.title + '》');
      deleteBtn.addEventListener('click', function () {
        deleteBook(book.id);
      });
      actions.appendChild(deleteBtn);

      li.appendChild(actions);
      bookListEl.appendChild(li);
    });

    const isEmpty = filteredBooks.length === 0;
    emptyMessageEl.classList.toggle('visible', isEmpty);
    bookListEl.style.display = isEmpty ? 'none' : '';

    renderStats();
  }

  function renderStats() {
    const counts = {
      total: books.length,
      reading: books.filter(function (b) { return b.status === 'reading'; }).length,
      completed: books.filter(function (b) { return b.status === 'completed'; }).length,
      wishlist: books.filter(function (b) { return b.status === 'wishlist'; }).length
    };

    statTotalEl.textContent = String(counts.total);
    statReadingEl.textContent = String(counts.reading);
    statCompletedEl.textContent = String(counts.completed);
    statWishlistEl.textContent = String(counts.wishlist);
  }

  // ---- Actions ----
  function addBook(title, author, status) {
    const book = {
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
      title: title,
      author: author || '',
      status: status || 'reading',
      createdAt: Date.now()
    };
    books.push(book);
    saveBooks();
    render();
  }

  function updateStatus(id, newStatus) {
    const book = books.find(function (b) { return b.id === id; });
    if (book) {
      book.status = newStatus;
      saveBooks();
      render();
    }
  }

  function deleteBook(id) {
    books = books.filter(function (b) { return b.id !== id; });
    saveBooks();
    render();
  }

  function setFilter(filter) {
    currentFilter = filter;
    filterBtns.forEach(function (btn) {
      const isActive = btn.dataset.filter === filter;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
    render();
  }

  // ---- Event listeners ----
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    const title = titleInput.value.trim();
    if (!title) return;
    addBook(title, authorInput.value.trim(), statusSelect.value);
    form.reset();
    titleInput.focus();
  });

  filterBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      setFilter(btn.dataset.filter);
    });
  });

  // ---- Init ----
  setFilter('all');
})();