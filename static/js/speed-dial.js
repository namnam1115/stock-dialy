/**
 * Enhanced SpeedDial class - Phase 1-3 Improvements
 * - リップルエフェクト
 * - 触覚フィードバック強化
 * - 長押しプレビュー
 * - キーボード自動調整
 * - 片手操作モード
 * - 使用頻度ベース並び替え
 * - コンテキスト認識
 * - プログレスインジケーター
 */
class SpeedDial {
  constructor(config = {}) {
    this.config = {
      triggerSelector: '.speed-dial-trigger',
      actionsSelector: '.speed-dial-actions',
      overlaySelector: '.speed-dial-overlay',
      containerSelector: '.speed-dial-container',
      useOverlay: true,
      autoCloseTime: 4000,
      longPressDelay: 500,
      enableHaptics: true,
      enableFrequencySort: true,
      enableContextAware: true,
      oneHandedMode: false,
      ...config
    };
    
    this.trigger = document.querySelector(this.config.triggerSelector);
    this.actions = document.querySelector(this.config.actionsSelector);
    this.overlay = document.querySelector(this.config.overlaySelector);
    this.container = document.querySelector(this.config.containerSelector);
    
    this.isOpen = false;
    this.singleAction = false;
    this.autoCloseTimer = null;
    
    // 長押し関連
    this.longPressTimer = null;
    this.longPressActive = false;
    
    // 使用頻度追跡
    this.actionStats = this.loadActionStats();
    
    // キーボード表示検出
    this.isKeyboardVisible = false;
    this.originalBottom = null;
    
    // タッチ操作のための変数
    this.touchStartY = 0;
    this.touchStartTime = 0;
    
    // アクションが1つだけかチェック
    if (this.actions && this.actions.querySelectorAll('.speed-dial-action').length === 1) {
      this.singleAction = true;
      this.setupSingleAction();
    } else if (this.trigger && this.actions) {
      this.init();
    } else {
      console.warn('SpeedDial: 必要な要素が見つかりません');
    }
  }
  
  // 触覚フィードバック（強化版）
  hapticFeedback(type = 'light') {
    if (!this.config.enableHaptics || !navigator.vibrate) return;
    
    // prefers-reduced-motionをチェック
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    
    const patterns = {
      light: [5],
      medium: [10],
      strong: [15],
      success: [10, 5, 10],
      error: [20, 10, 20, 10, 20],
      warning: [15, 10, 15],
      open: [5],
      close: [5],
      select: [10]
    };
    
    navigator.vibrate(patterns[type] || patterns.light);
  }
  
  // 使用頻度の読み込み
  loadActionStats() {
    try {
      return JSON.parse(localStorage.getItem('speedDialStats') || '{}');
    } catch (e) {
      return {};
    }
  }
  
  // 使用頻度の保存
  saveActionStats() {
    try {
      localStorage.setItem('speedDialStats', JSON.stringify(this.actionStats));
    } catch (e) {
      console.warn('Failed to save action stats:', e);
    }
  }
  
  // アクションの使用を記録
  recordActionUse(actionId) {
    if (!this.config.enableFrequencySort) return;
    
    this.actionStats[actionId] = (this.actionStats[actionId] || 0) + 1;
    this.saveActionStats();
    
    // 使用頻度が5回以上のアクションにインジケーターを追加
    if (this.actionStats[actionId] >= 5) {
      const action = this.actions.querySelector(`[data-action-id="${actionId}"]`);
      if (action) {
        action.classList.add('frequently-used');
      }
    }
  }
  
  // 使用頻度に基づいてアクションを並び替え
  sortActionsByFrequency() {
    if (!this.config.enableFrequencySort) return;
    
    const actions = Array.from(this.actions.querySelectorAll('.speed-dial-action'));
    
    actions.sort((a, b) => {
      const aId = a.dataset.actionId || a.querySelector('.speed-dial-btn')?.getAttribute('aria-label');
      const bId = b.dataset.actionId || b.querySelector('.speed-dial-btn')?.getAttribute('aria-label');
      const aCount = this.actionStats[aId] || 0;
      const bCount = this.actionStats[bId] || 0;
      return bCount - aCount;
    });
    
    actions.forEach(action => this.actions.appendChild(action));
  }
  
  // コンテキスト認識
  detectContext() {
    if (!this.config.enableContextAware) return;
    
    const scrollPosition = window.pageYOffset || document.documentElement.scrollTop;
    const windowHeight = window.innerHeight;
    const documentHeight = document.documentElement.scrollHeight;
    
    const isNearTop = scrollPosition < 100;
    const isNearBottom = (windowHeight + scrollPosition) >= documentHeight - 100;
    
    // コンテキストに応じてアクションを強調
    const actions = this.actions.querySelectorAll('.speed-dial-action');
    actions.forEach(action => {
      action.classList.remove('context-aware');
      
      const btn = action.querySelector('.speed-dial-btn');
      const actionType = Array.from(btn.classList).find(c => c.startsWith('action-'));
      
      if (isNearTop && actionType === 'action-add') {
        action.classList.add('context-aware');
      } else if (isNearBottom && actionType === 'action-back') {
        action.classList.add('context-aware');
      }
    });
  }
  
  // 単一アクションモードのセットアップ
  setupSingleAction() {
    if (this.trigger) {
      this.trigger.style.display = 'none';
    }
    
    if (this.actions) {
      this.actions.classList.add('active');
      
      const actionItem = this.actions.querySelector('.speed-dial-action');
      if (actionItem) {
        const label = actionItem.querySelector('.action-label');
        if (label) {
          label.style.display = 'none';
        }
        
        const btn = actionItem.querySelector('.speed-dial-btn');
        if (btn) {
          btn.style.width = '56px';
          btn.style.height = '56px';
        }
      }
    }
    
    if (this.container) {
      this.container.classList.add('single-action-mode');
    }
  }
  
  // キーボード表示の検出と調整
  setupKeyboardDetection() {
    if (!window.visualViewport) return;
    
    const adjustForKeyboard = () => {
      const keyboardHeight = window.innerHeight - window.visualViewport.height;
      
      if (keyboardHeight > 100) {
        // キーボードが表示されている
        if (!this.isKeyboardVisible) {
          this.isKeyboardVisible = true;
          this.originalBottom = this.container.style.bottom || '16px';
          this.container.classList.add('keyboard-visible');
          this.container.style.bottom = `${keyboardHeight + 16}px`;
          
          // 開いている場合は閉じる
          if (this.isOpen) {
            this.close();
          }
        }
      } else {
        // キーボードが非表示
        if (this.isKeyboardVisible) {
          this.isKeyboardVisible = false;
          this.container.classList.remove('keyboard-visible');
          this.container.style.bottom = this.originalBottom;
        }
      }
    };
    
    window.visualViewport.addEventListener('resize', adjustForKeyboard);
    window.visualViewport.addEventListener('scroll', adjustForKeyboard);
  }
  
  // 片手操作モードの設定
  setupOneHandedMode() {
    if (!this.config.oneHandedMode) return;
    
    // デバイスの画面サイズをチェック
    const screenWidth = window.innerWidth;
    
    if (screenWidth >= 414) { // 大画面スマホ
      this.container.classList.add('one-handed-mode');
      
      // 利き手に基づいて位置を調整（オプション）
      const preferredHand = localStorage.getItem('preferredHand') || 'right';
      if (preferredHand === 'left') {
        this.container.classList.add('one-handed-left');
      }
    }
  }
  
  // 長押しプレビューのセットアップ
  setupLongPress(btn, action) {
    let touchStartX, touchStartY;
    let hasMoved = false;
    
    const startLongPress = (e) => {
      hasMoved = false;
      
      if (e.type === 'touchstart') {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
      }
      
      this.longPressTimer = setTimeout(() => {
        if (!hasMoved) {
          this.showPreview(btn, action);
          this.longPressActive = true;
          btn.classList.add('long-press-active');
          this.hapticFeedback('medium');
        }
      }, this.config.longPressDelay);
    };
    
    const checkMove = (e) => {
      if (e.type === 'touchmove') {
        const deltaX = Math.abs(e.touches[0].clientX - touchStartX);
        const deltaY = Math.abs(e.touches[0].clientY - touchStartY);
        
        if (deltaX > 10 || deltaY > 10) {
          hasMoved = true;
          this.cancelLongPress(btn);
        }
      }
    };
    
    const endLongPress = (e) => {
      if (this.longPressActive) {
        e.preventDefault();
        this.hidePreview(btn);
        this.longPressActive = false;
      }
      this.cancelLongPress(btn);
    };
    
    btn.addEventListener('touchstart', startLongPress, { passive: true });
    btn.addEventListener('mousedown', startLongPress);
    btn.addEventListener('touchmove', checkMove, { passive: true });
    btn.addEventListener('touchend', endLongPress);
    btn.addEventListener('touchcancel', endLongPress);
    btn.addEventListener('mouseup', endLongPress);
    btn.addEventListener('mouseleave', endLongPress);
  }
  
  cancelLongPress(btn) {
    if (this.longPressTimer) {
      clearTimeout(this.longPressTimer);
      this.longPressTimer = null;
    }
    btn.classList.remove('long-press-active');
  }
  
  showPreview(btn, action) {
    const label = action.querySelector('.action-label');
    if (!label) return;
    
    const preview = document.createElement('div');
    preview.className = 'action-preview';
    preview.textContent = label.textContent;
    action.appendChild(preview);
    
    // アニメーション用に少し遅延
    setTimeout(() => preview.classList.add('active'), 10);
  }
  
  hidePreview(btn) {
    const preview = btn.closest('.speed-dial-action').querySelector('.action-preview');
    if (preview) {
      preview.classList.remove('active');
      setTimeout(() => preview.remove(), 300);
    }
  }
  
  // 音声アナウンス（アクセシビリティ）
  announceAction(actionLabel) {
    const announcement = document.createElement('div');
    announcement.setAttribute('role', 'status');
    announcement.setAttribute('aria-live', 'polite');
    announcement.className = 'sr-only';
    announcement.textContent = `${actionLabel}を選択しました`;
    document.body.appendChild(announcement);
    
    setTimeout(() => announcement.remove(), 1000);
  }
  
  init() {
    // 使用頻度に基づいて並び替え
    this.sortActionsByFrequency();
    
    // キーボード検出のセットアップ
    this.setupKeyboardDetection();
    
    // 片手操作モードのセットアップ
    this.setupOneHandedMode();
    
    // コンテキスト認識の初期化
    this.detectContext();
    
    // トリガーボタンのイベント
    this.trigger.addEventListener('click', () => {
      this.toggle();
      this.hapticFeedback('light');
    });
    
    this.trigger.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        this.toggle();
      }
    });
    
    // オーバーレイのクリックイベント
    if (this.overlay && this.config.useOverlay) {
      this.overlay.addEventListener('click', () => {
        this.close();
      });
    }
    
    // アクションボタンの設定
    const actionButtons = this.actions.querySelectorAll('.speed-dial-btn');
    actionButtons.forEach((btn, index) => {
      const action = btn.closest('.speed-dial-action');
      const actionId = action.dataset.actionId || btn.getAttribute('aria-label') || `action-${index}`;
      action.dataset.actionId = actionId;
      
      // 長押しプレビューのセットアップ
      this.setupLongPress(btn, action);
      
      // クリックイベント
      btn.addEventListener('click', (e) => {
        // 長押し状態の場合はクリックをキャンセル
        if (this.longPressActive) {
          e.preventDefault();
          return;
        }
        
        this.recordActionUse(actionId);
        this.hapticFeedback('select');
        this.announceAction(btn.getAttribute('aria-label'));
        
        // アクション実行後に閉じる
        setTimeout(() => this.close(), 100);
      });
      
      // キーボードアクセシビリティ
      btn.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          btn.click();
        }
      });
    });
    
    // ページ内の他の場所をクリックした時に閉じる
    document.addEventListener('click', (e) => {
      if (this.isOpen && !this.container.contains(e.target)) {
        this.close();
      }
    });
    
    // ESCキーで閉じる
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isOpen) {
        this.close();
      }
    });
    
    // タッチ操作のサポート（スワイプで閉じる）
    if (this.container) {
      this.container.addEventListener('touchstart', (e) => {
        this.touchStartY = e.touches[0].clientY;
        this.touchStartTime = Date.now();
      }, { passive: true });
      
      this.container.addEventListener('touchmove', (e) => {
        if (!this.isOpen) return;
        
        const currentY = e.touches[0].clientY;
        const diffY = currentY - this.touchStartY;
        
        // 下向きのスワイプで閉じる（より直感的）
        if (diffY > 50) {
          this.close();
        }
      }, { passive: true });
    }
    
    // スクロール時の処理
    let lastScrollTop = window.pageYOffset || document.documentElement.scrollTop;
    let scrollTimer = null;
    let scrollingStopTimer = null;

    window.addEventListener('scroll', () => {
      // ページ最下部付近ではFABを縮小してコンテンツとの重なりを軽減する
      this.updateNearBottomState();

      // 無限スクロール一覧はページ最下部にほぼ到達しないため、
      // スクロール中は常にFABを縮小し、止まったら元に戻す
      if (this.container) {
        this.container.classList.add('is-scrolling');
      }
      clearTimeout(scrollingStopTimer);
      scrollingStopTimer = setTimeout(() => {
        if (this.container) {
          this.container.classList.remove('is-scrolling');
        }
      }, 500);

      if (scrollTimer !== null) return;

      scrollTimer = setTimeout(() => {
        const currentScrollTop = window.pageYOffset || document.documentElement.scrollTop;

        // コンテキスト認識を更新
        this.detectContext();

        // 大きくスクロールした場合は閉じる
        if (Math.abs(lastScrollTop - currentScrollTop) > 50) {
          if (this.isOpen) {
            this.close();
          }
          lastScrollTop = currentScrollTop;
        }
        scrollTimer = null;
      }, 150);
    }, { passive: true });

    // 初期表示時点でも判定する
    this.updateNearBottomState();
  }

  // ページ最下部付近かどうかでFABの縮小状態を切り替える
  updateNearBottomState() {
    if (!this.container) return;

    const scrollPosition = window.pageYOffset || document.documentElement.scrollTop;
    const windowHeight = window.innerHeight;
    const documentHeight = document.documentElement.scrollHeight;
    const isNearBottom = (windowHeight + scrollPosition) >= documentHeight - 80;

    this.container.classList.toggle('near-bottom', isNearBottom);
  }
  
  toggle() {
    if (this.isOpen) {
      this.close();
    } else {
      this.open();
    }
  }
  
  open() {
    if (!this.isOpen) {
      this.hapticFeedback('open');
      
      this.trigger.classList.add('active');
      this.actions.classList.add('active');
      if (this.container) {
        this.container.classList.add('dial-open');
      }

      if (this.overlay && this.config.useOverlay) {
        this.overlay.classList.add('active');
      }
      
      // アニメーションのためにタイミングを少しずらす
      this.actions.querySelectorAll('.speed-dial-action').forEach((action, index) => {
        action.style.transitionDelay = `${0.05 * index}s`;
      });
      
      // アクセシビリティ: ARIAステートの更新
      this.trigger.setAttribute('aria-expanded', 'true');
      
      this.isOpen = true;
      
      // プログレスリングとタイマーを開始
      this.startAutoCloseTimer();
    }
  }
  
  close() {
    if (this.isOpen) {
      this.hapticFeedback('close');
      
      this.trigger.classList.remove('active');
      this.actions.classList.remove('active');
      if (this.container) {
        this.container.classList.remove('dial-open');
      }

      if (this.overlay && this.config.useOverlay) {
        this.overlay.classList.remove('active');
      }
      
      // トランジションディレイをリセット
      this.actions.querySelectorAll('.speed-dial-action').forEach(action => {
        action.style.transitionDelay = '';
      });
      
      // アクセシビリティ: ARIAステートの更新
      this.trigger.setAttribute('aria-expanded', 'false');
      
      this.isOpen = false;
      
      // タイマーをクリア
      this.clearAutoCloseTimer();
    }
  }
  
  // プログレスインジケーター付き自動閉じるタイマー
  startAutoCloseTimer() {
    this.clearAutoCloseTimer();
    
    if (this.config.autoCloseTime > 0) {
      this.addProgressRing();
      
      this.autoCloseTimer = setTimeout(() => {
        this.close();
      }, this.config.autoCloseTime);
    }
  }
  
  clearAutoCloseTimer() {
    if (this.autoCloseTimer) {
      clearTimeout(this.autoCloseTimer);
      this.autoCloseTimer = null;
      this.removeProgressRing();
    }
  }
  
  // プログレスリングを追加
  addProgressRing() {
    this.removeProgressRing();
    
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'progress-ring');
    svg.setAttribute('width', '60');
    svg.setAttribute('height', '60');
    
    const bgCircle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    bgCircle.setAttribute('cx', '30');
    bgCircle.setAttribute('cy', '30');
    bgCircle.setAttribute('r', '28');
    bgCircle.setAttribute('stroke', 'rgba(255,255,255,0.3)');
    bgCircle.setAttribute('stroke-width', '2');
    bgCircle.setAttribute('fill', 'none');
    
    const progressCircle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    progressCircle.setAttribute('cx', '30');
    progressCircle.setAttribute('cy', '30');
    progressCircle.setAttribute('r', '28');
    progressCircle.setAttribute('stroke', 'white');
    progressCircle.setAttribute('stroke-width', '2');
    progressCircle.setAttribute('fill', 'none');
    progressCircle.setAttribute('stroke-dasharray', '176');
    progressCircle.setAttribute('stroke-dashoffset', '176');
    progressCircle.setAttribute('class', 'progress-ring-circle');
    progressCircle.style.animation = `countdown ${this.config.autoCloseTime}ms linear forwards`;
    
    svg.appendChild(bgCircle);
    svg.appendChild(progressCircle);
    this.trigger.appendChild(svg);
  }
  
  removeProgressRing() {
    const ring = this.trigger.querySelector('.progress-ring');
    if (ring) {
      ring.remove();
    }
  }
}

/**
 * スピードダイアルを動的に生成する（拡張版）
 */
function createSpeedDial(options = {}) {
  const defaults = {
    container: document.body,
    actions: [],
    useOverlay: true,
    triggerIcon: 'bi-plus-lg',
    position: 'bottom-right',
    autoCloseTime: 4000,
    longPressDelay: 500,
    enableHaptics: true,
    enableFrequencySort: true,
    enableContextAware: true,
    oneHandedMode: false
  };
  
  const config = { ...defaults, ...options };
  
  // アクションをフィルタリング
  const filteredActions = config.actions.filter(action => action.condition !== false);
  const singleActionMode = filteredActions.length === 1;
  
  // コンテナを作成
  const container = document.createElement('div');
  container.className = 'speed-dial-container';
  if (singleActionMode) {
    container.classList.add('single-action-mode');
  }
  
  // アクションボタンコンテナを作成
  const actionsContainer = document.createElement('div');
  actionsContainer.className = 'speed-dial-actions';
  actionsContainer.setAttribute('role', 'menu');
  actionsContainer.setAttribute('aria-orientation', 'vertical');
  
  if (singleActionMode) {
    actionsContainer.classList.add('active');
  }
  
  // アクションボタンを生成
  for (const [index, action] of filteredActions.entries()) {
    const actionItem = document.createElement('div');
    actionItem.className = 'speed-dial-action';
    actionItem.setAttribute('role', 'menuitem');
    actionItem.dataset.actionId = action.id || `action-${index}`;
    
    // アクションラベル
    const label = document.createElement('span');
    label.className = 'action-label';
    label.textContent = action.label || '';
    
    // アクションボタン
    const btn = document.createElement('a');
    btn.href = action.url || '#';
    btn.className = `speed-dial-btn action-${action.type}`;
    btn.setAttribute('role', 'button');
    btn.setAttribute('aria-label', action.aria_label || action.label || '');
    btn.setAttribute('title', action.label || '');
    
    if (action.onclick) {
      btn.addEventListener('click', (e) => {
        if (!action.url || action.url === '#') {
          e.preventDefault();
        }
        
        try {
          if (typeof action.onclick === 'function') {
            action.onclick(e);
          }
        } catch (err) {
          console.warn('Action onclick error:', err);
        }
      });
    }
    
    const icon = document.createElement('i');
    icon.className = `bi ${action.icon}`;
    btn.appendChild(icon);
    
    actionItem.appendChild(label);
    actionItem.appendChild(btn);
    actionsContainer.appendChild(actionItem);
  }
  
  // トリガーボタンを作成
  const trigger = document.createElement('button');
  trigger.className = 'speed-dial-trigger';
  trigger.type = 'button';
  trigger.setAttribute('aria-haspopup', 'menu');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-label', 'アクションメニュー');
  
  const triggerIcon = document.createElement('i');
  triggerIcon.className = `bi ${config.triggerIcon}`;
  trigger.appendChild(triggerIcon);
  
  if (singleActionMode) {
    trigger.style.display = 'none';
  }
  
  // オーバーレイを作成
  let overlay = null;
  if (config.useOverlay && !singleActionMode) {
    overlay = document.createElement('div');
    overlay.className = 'speed-dial-overlay';
    config.container.appendChild(overlay);
  }
  
  // 要素を追加
  container.appendChild(actionsContainer);
  container.appendChild(trigger);
  config.container.appendChild(container);
  
  // スピードダイアルのインスタンスを初期化
  return new SpeedDial({
    triggerSelector: '.speed-dial-trigger',
    actionsSelector: '.speed-dial-actions',
    overlaySelector: '.speed-dial-overlay',
    containerSelector: '.speed-dial-container',
    useOverlay: config.useOverlay,
    autoCloseTime: config.autoCloseTime,
    longPressDelay: config.longPressDelay,
    enableHaptics: config.enableHaptics,
    enableFrequencySort: config.enableFrequencySort,
    enableContextAware: config.enableContextAware,
    oneHandedMode: config.oneHandedMode
  });
}

// グローバル初期化関数（Intersection Observer使用）
function initializeSpeedDial() {
  const speedDial = new SpeedDial();
  window.speedDialInstance = speedDial;

  // スピードダイヤルをDOM内で適切な位置に強制移動
  const speedDialContainer = document.querySelector('.speed-dial-container');
  if (speedDialContainer && speedDialContainer.parentElement !== document.body) {
    document.body.appendChild(speedDialContainer);
  }

  // ページクリック時にポジションを確認・修正
  document.addEventListener('click', function() {
    const speedDial = document.querySelector('.speed-dial-container');
    if (speedDial && speedDial.parentElement !== document.body) {
      document.body.appendChild(speedDial);
    }
  }, true);

  return speedDial;
}

// bfcache（ブラウザバック）からの復元時にスピードダイヤルをリセット
window.addEventListener('pageshow', function(event) {
  if (event.persisted && window.speedDialInstance) {
    window.speedDialInstance.close();
  }
});

// Intersection Observerによる遅延初期化
let speedDialInitialized = false;

if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting && !speedDialInitialized) {
        const existingTrigger = document.querySelector('.speed-dial-trigger');
        if (existingTrigger) {
          initializeSpeedDial();
          speedDialInitialized = true;
          observer.disconnect();
        }
      }
    });
  }, { threshold: 0.1 });
  
  // DOMContentLoadedでobserverを開始
  document.addEventListener('DOMContentLoaded', function() {
    observer.observe(document.body);
  });
} else {
  // Intersection Observerが使えない場合は従来通り
  document.addEventListener('DOMContentLoaded', function() {
    const existingTrigger = document.querySelector('.speed-dial-trigger');
    if (existingTrigger) {
      initializeSpeedDial();
    }
  });
}