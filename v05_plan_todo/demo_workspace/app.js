(function () {
  'use strict';

  const canvas = document.getElementById('gameCanvas');
  const ctx = canvas.getContext('2d');
  const scoreEl = document.getElementById('score');
  const highScoreEl = document.getElementById('highScore');
  const startBtn = document.getElementById('startBtn');
  const overlay = document.getElementById('overlay');
  const overlayTitle = document.getElementById('overlayTitle');

  const GRID_SIZE = 20;
  const CELL_SIZE = canvas.width / GRID_SIZE;
  const GAME_SPEED = 120; // 毫秒

  let snake = [];
  let food = { x: 0, y: 0 };
  let direction = { x: 1, y: 0 };
  let nextDirection = { x: 1, y: 0 };
  let score = 0;
  let highScore = parseInt(localStorage.getItem('snakeHighScore')) || 0;
  let gameInterval = null;
  let isGameRunning = false;

  highScoreEl.textContent = highScore;

  function initGame() {
    snake = [
      { x: 10, y: 10 },
      { x: 9, y: 10 },
      { x: 8, y: 10 }
    ];
    direction = { x: 1, y: 0 };
    nextDirection = { x: 1, y: 0 };
    score = 0;
    scoreEl.textContent = score;
    generateFood();
  }

  function generateFood() {
    let newFood;
    do {
      newFood = {
        x: Math.floor(Math.random() * GRID_SIZE),
        y: Math.floor(Math.random() * GRID_SIZE)
      };
    } while (snake.some(seg => seg.x === newFood.x && seg.y === newFood.y));
    food = newFood;
  }

  function draw() {
    // 清空画布
    ctx.fillStyle = '#0f0f1a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // 绘制网格线
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.03)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= GRID_SIZE; i++) {
      ctx.beginPath();
      ctx.moveTo(i * CELL_SIZE, 0);
      ctx.lineTo(i * CELL_SIZE, canvas.height);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, i * CELL_SIZE);
      ctx.lineTo(canvas.width, i * CELL_SIZE);
      ctx.stroke();
    }

    // 绘制食物
    ctx.fillStyle = '#e94560';
    ctx.shadowColor = '#e94560';
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.arc(
      food.x * CELL_SIZE + CELL_SIZE / 2,
      food.y * CELL_SIZE + CELL_SIZE / 2,
      CELL_SIZE / 2 - 2,
      0,
      Math.PI * 2
    );
    ctx.fill();
    ctx.shadowBlur = 0;

    // 绘制蛇
    snake.forEach((seg, i) => {
      const ratio = i / snake.length;
      const r = Math.round(233 - ratio * 80);
      const g = Math.round(69 + ratio * 30);
      const b = Math.round(96 + ratio * 30);
      ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
      ctx.shadowColor = i === 0 ? '#e94560' : 'transparent';
      ctx.shadowBlur = i === 0 ? 8 : 0;
      
      const x = seg.x * CELL_SIZE + 1;
      const y = seg.y * CELL_SIZE + 1;
      const size = CELL_SIZE - 2;
      
      ctx.beginPath();
      ctx.roundRect(x, y, size, size, 4);
      ctx.fill();
      ctx.shadowBlur = 0;
    });
  }

  function update() {
    direction = nextDirection;

    const head = {
      x: snake[0].x + direction.x,
      y: snake[0].y + direction.y
    };

    // 撞墙检测
    if (head.x < 0 || head.x >= GRID_SIZE || head.y < 0 || head.y >= GRID_SIZE) {
      gameOver();
      return;
    }

    // 撞自身检测
    if (snake.some(seg => seg.x === head.x && seg.y === head.y)) {
      gameOver();
      return;
    }

    snake.unshift(head);

    // 吃到食物
    if (head.x === food.x && head.y === food.y) {
      score += 10;
      scoreEl.textContent = score;
      generateFood();
    } else {
      snake.pop();
    }

    draw();
  }

  function startGame() {
    if (isGameRunning) return;
    isGameRunning = true;
    overlay.classList.add('hidden');
    initGame();
    draw();
    gameInterval = setInterval(update, GAME_SPEED);
  }

  function gameOver() {
    clearInterval(gameInterval);
    gameInterval = null;
    isGameRunning = false;

    if (score > highScore) {
      highScore = score;
      highScoreEl.textContent = highScore;
      localStorage.setItem('snakeHighScore', highScore);
    }

    overlayTitle.textContent = '游戏结束';
    overlayTitle.style.color = '#e94560';
    overlay.querySelector('.overlay-hint').textContent = '分数: ' + score + '，按空格键或点击重新开始';
    overlay.classList.remove('hidden');
    startBtn.textContent = '重新开始';
  }

  function handleKeyPress(e) {
    const key = e.key.toLowerCase();
    
    // 空格键开始/重新开始
    if (key === ' ') {
      e.preventDefault();
      startGame();
      return;
    }

    // 方向控制
    switch (key) {
      case 'arrowup':
      case 'w':
        if (direction.y !== 1) nextDirection = { x: 0, y: -1 };
        break;
      case 'arrowdown':
      case 's':
        if (direction.y !== -1) nextDirection = { x: 0, y: 1 };
        break;
      case 'arrowleft':
      case 'a':
        if (direction.x !== 1) nextDirection = { x: -1, y: 0 };
        break;
      case 'arrowright':
      case 'd':
        if (direction.x !== -1) nextDirection = { x: 1, y: 0 };
        break;
    }
  }

  startBtn.addEventListener('click', startGame);
  document.addEventListener('keydown', handleKeyPress);

  // 初始绘制
  initGame();
  draw();
})();
