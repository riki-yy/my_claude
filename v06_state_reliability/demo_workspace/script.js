// ===== 贪吃蛇小游戏：游戏逻辑 =====

(function () {
  "use strict";

  // ---------- 常量 ----------
  const GRID_SIZE = 20;            // 网格尺寸（20x20）
  const CELL_SIZE = 20;            // 每个单元格像素
  const CANVAS_SIZE = 400;         // 画布尺寸
  const BASE_SPEED = 180;          // 基础移动间隔（毫秒）
  const MIN_SPEED = 80;            // 最快移动间隔
  const SPEED_STEP = 5;            // 每吃一个食物减少的间隔
  const HIGH_SCORE_KEY = "snake-high-score";

  // ---------- DOM 元素 ----------
  const canvas = document.getElementById("game-canvas");
  const ctx = canvas.getContext("2d");
  const scoreEl = document.getElementById("score");
  const highScoreEl = document.getElementById("high-score");
  const overlayEl = document.getElementById("overlay");
  const overlayTitleEl = document.getElementById("overlay-title");
  const overlaySubtitleEl = document.getElementById("overlay-subtitle");
  const restartBtn = document.getElementById("restart-btn");
  const statusTipEl = document.getElementById("status-tip");

  // ---------- 游戏状态 ----------
  let snake = [];                 // 蛇身数组，每个元素为 {x, y}
  let direction = { x: 1, y: 0 }; // 当前移动方向
  let nextDirection = null;       // 下一帧移动方向（支持连续快按换向）
  let food = null;                // 食物位置
  let score = 0;
  let highScore = 0;
  let running = false;            // 游戏是否进行中
  let gameOver = false;           // 是否已结束
  let started = false;            // 是否已开始（用于"按方向键开始"）
  let timerId = null;
  let speed = BASE_SPEED;

  // ---------- 初始化 ----------
  function loadHighScore() {
    const stored = parseInt(localStorage.getItem(HIGH_SCORE_KEY), 10);
    highScore = Number.isFinite(stored) ? stored : 0;
    highScoreEl.textContent = highScore;
  }

  function saveHighScore() {
    if (score > highScore) {
      highScore = score;
      localStorage.setItem(HIGH_SCORE_KEY, String(highScore));
      highScoreEl.textContent = highScore;
    }
  }

  // ---------- 工具函数 ----------
  function randomInt(min, max) {
    return Math.floor(Math.random() * (max - min)) + min;
  }

  // 在空位生成食物；无空位时返回 null（胜利）
  function spawnFood() {
    const freeCells = [];
    for (let x = 0; x < GRID_SIZE; x++) {
      for (let y = 0; y < GRID_SIZE; y++) {
        const occupied = snake.some(function (seg) {
          return seg.x === x && seg.y === y;
        });
        if (!occupied) freeCells.push({ x: x, y: y });
      }
    }
    if (freeCells.length === 0) {
      return null;
    }
    return freeCells[randomInt(0, freeCells.length)];
  }

  // ---------- 绘制 ----------
  function drawGrid() {
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.lineWidth = 1;
    for (let i = 1; i < GRID_SIZE; i++) {
      ctx.beginPath();
      ctx.moveTo(i * CELL_SIZE, 0);
      ctx.lineTo(i * CELL_SIZE, CANVAS_SIZE);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, i * CELL_SIZE);
      ctx.lineTo(CANVAS_SIZE, i * CELL_SIZE);
      ctx.stroke();
    }
  }

  function drawSnake() {
    for (let i = 0; i < snake.length; i++) {
      const seg = snake[i];
      ctx.fillStyle = i === 0 ? "#8ef2cf" : "#3ecf8e";
      const pad = 1;
      ctx.fillRect(
        seg.x * CELL_SIZE + pad,
        seg.y * CELL_SIZE + pad,
        CELL_SIZE - pad * 2,
        CELL_SIZE - pad * 2
      );
      if (i === 0) {
        // 头部画眼睛指示方向
        ctx.fillStyle = "#0b1a20";
        const eyeSize = 3;
        const cx = seg.x * CELL_SIZE + CELL_SIZE / 2;
        const cy = seg.y * CELL_SIZE + CELL_SIZE / 2;
        if (direction.x === 1) {
          ctx.fillRect(cx + 3, cy - 5, eyeSize, eyeSize);
          ctx.fillRect(cx + 3, cy + 2, eyeSize, eyeSize);
        } else if (direction.x === -1) {
          ctx.fillRect(cx - 6, cy - 5, eyeSize, eyeSize);
          ctx.fillRect(cx - 6, cy + 2, eyeSize, eyeSize);
        } else if (direction.y === 1) {
          ctx.fillRect(cx - 5, cy + 3, eyeSize, eyeSize);
          ctx.fillRect(cx + 2, cy + 3, eyeSize, eyeSize);
        } else {
          ctx.fillRect(cx - 5, cy - 6, eyeSize, eyeSize);
          ctx.fillRect(cx + 2, cy - 6, eyeSize, eyeSize);
        }
      }
    }
  }

  function drawFood() {
    if (!food) return;
    ctx.fillStyle = "#ff7e67";
    ctx.beginPath();
    ctx.arc(
      food.x * CELL_SIZE + CELL_SIZE / 2,
      food.y * CELL_SIZE + CELL_SIZE / 2,
      CELL_SIZE / 2 - 2,
      0,
      Math.PI * 2
    );
    ctx.fill();
    // 食物高光
    ctx.fillStyle = "rgba(255,255,255,0.5)";
    ctx.beginPath();
    ctx.arc(
      food.x * CELL_SIZE + CELL_SIZE / 2 - 2,
      food.y * CELL_SIZE + CELL_SIZE / 2 - 2,
      2,
      0,
      Math.PI * 2
    );
    ctx.fill();
  }

  function render() {
    ctx.clearRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);
    drawGrid();
    drawFood();
    drawSnake();
  }

  // ---------- 游戏流程 ----------
  function reset() {
    if (timerId) {
      clearInterval(timerId);
      timerId = null;
    }

    // 初始蛇：位于中间，水平向右，长度 3
    snake = [
      { x: 7, y: 10 },
      { x: 6, y: 10 },
      { x: 5, y: 10 }
    ];
    direction = { x: 1, y: 0 };
    nextDirection = null;
    score = 0;
    speed = BASE_SPEED;
    running = false;
    gameOver = false;
    started = false;

    scoreEl.textContent = "0";
    overlayEl.classList.add("hidden");
    statusTipEl.textContent = "按任意方向键开始游戏";

    food = spawnFood();
    render();
  }

  function startGame() {
    started = true;
    running = true;
    statusTipEl.textContent = "游戏进行中";
    overlayEl.classList.add("hidden");
    if (timerId) clearInterval(timerId);
    timerId = setInterval(gameStep, speed);
  }

  function gameStep() {
    // 应用下一方向
    if (nextDirection) {
      direction = nextDirection;
      nextDirection = null;
    }

    // 计算新头部
    const head = snake[0];
    const newHead = {
      x: head.x + direction.x,
      y: head.y + direction.y
    };

    // 撞墙检测
    if (
      newHead.x < 0 ||
      newHead.x >= GRID_SIZE ||
      newHead.y < 0 ||
      newHead.y >= GRID_SIZE
    ) {
      endGame("撞墙了！");
      return;
    }

    // 撞自己检测
    const hitSelf = snake.some(function (seg) {
      return seg.x === newHead.x && seg.y === newHead.y;
    });
    if (hitSelf) {
      endGame("撞到自己了！");
      return;
    }

    // 蛇移动：头部插入
    snake.unshift(newHead);

    // 吃食物
    if (food && newHead.x === food.x && newHead.y === food.y) {
      score += 10;
      scoreEl.textContent = score;
      saveHighScore();

      // 加速
      speed = Math.max(MIN_SPEED, speed - SPEED_STEP);
      if (timerId) clearInterval(timerId);
      timerId = setInterval(gameStep, speed);

      // 新食物
      food = spawnFood();
      if (food === null) {
        endGame("你赢了！蛇占满整个场地");
        return;
      }
      // 吃到食物时尾部不删除（蛇变长）
    } else {
      // 未吃到食物，尾部移除（保持长度）
      snake.pop();
    }

    render();
  }

  function endGame(message) {
    if (gameOver) return;
    gameOver = true;
    running = false;
    if (timerId) {
      clearInterval(timerId);
      timerId = null;
    }
    saveHighScore();

    overlayTitleEl.textContent = "游戏结束";
    overlaySubtitleEl.textContent = message + "  得分：" + score;
    overlayEl.classList.remove("hidden");
    statusTipEl.textContent = "游戏结束，点击重新开始";
  }

  // ---------- 键盘控制 ----------
  function handleKey(event) {
    const key = event.key;

    // 只处理方向键（也允许 WASD）
    let newDir = null;
    switch (key) {
      case "ArrowUp":
      case "w":
      case "W":
        newDir = { x: 0, y: -1 };
        break;
      case "ArrowDown":
      case "s":
      case "S":
        newDir = { x: 0, y: 1 };
        break;
      case "ArrowLeft":
      case "a":
      case "A":
        newDir = { x: -1, y: 0 };
        break;
      case "ArrowRight":
      case "d":
      case "D":
        newDir = { x: 1, y: 0 };
        break;
      default:
        return;
    }

    // 方向键阻止页面滚动
    if (key.indexOf("Arrow") === 0) {
      event.preventDefault();
    }

    // 未开始：按方向键即开始
    if (!started) {
      startGame();
    }

    if (!running || gameOver) return;

    // 禁止 180 度反向
    const isReverse = newDir.x === -direction.x && newDir.y === -direction.y;
    if (isReverse) return;

    // 通过 nextDirection 允许连按快速换向
    nextDirection = newDir;
  }

  // ---------- 事件绑定 ----------
  restartBtn.addEventListener("click", function () {
    reset();
  });

  document.addEventListener("keydown", handleKey);

  // ---------- 启动 ----------
  loadHighScore();
  reset();
})();