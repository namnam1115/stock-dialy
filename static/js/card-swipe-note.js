/**
 * card-swipe-note.js
 * ホームの diary カードヘッダーを左スワイプして
 * クイック継続記録（ボトムシート）を開く。
 *
 * 依存:
 *   - bottom-sheet.js : openBottomSheet / closeBottomSheet (home.html で読み込み済み)
 *   - toast.js        : showToast (home.html で読み込み済み)
 */

(function () {
  'use strict';

  const PANEL_WIDTH    = 80;   // アクションパネルの幅 (px)
  const SNAP_THRESHOLD = 60;   // この距離を超えたらパネルを固定 (px)
  const VERTICAL_LIMIT = 30;   // 縦移動がこれを超えたらスクロールと判定 (px)
  const HAPTIC_MS      = 15;

  let currentOpenHeader = null;      // 現在アクションパネルが開いているヘッダー
  let currentOpenDirection = null;   // 'left' | 'right'

  // ============================================================
  // スワイプハンドラのバインド
  // ============================================================
  function setupSwipeHandlers() {
    document.querySelectorAll('.diary-header').forEach(function (header) {
      if (header._swipeNoteSetup) return;
      header._swipeNoteSetup = true;

      var inner = header.querySelector('.diary-header-inner');
      if (!inner) return;

      var startX, startY, moveX = 0, isTracking = false, isScrolling = null;

      header.addEventListener('touchstart', function (e) {
        // アクションパネルへのタップは JS で処理するため除外
        if (e.target.closest('.note-action-panel')) return;
        if (e.target.closest('.exclude-action-panel')) return;
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        moveX = 0;
        isTracking = true;
        isScrolling = null;
        inner.style.transition = 'none';
      }, { passive: true });

      header.addEventListener('touchmove', function (e) {
        if (!isTracking) return;

        var diffX = startX - e.touches[0].clientX; // 左スワイプで正、右スワイプで負
        var diffY = Math.abs(e.touches[0].clientY - startY);

        // 方向未確定の間は transform を適用しない
        if (isScrolling === null) {
          var totalDist = Math.abs(diffX) + diffY;
          if (totalDist > 5) {
            isScrolling = diffY > Math.abs(diffX); // 縦成分が横成分を上回ればスクロール
          }
          return; // 方向確定まで何もしない
        }
        if (isScrolling) {
          isTracking = false;
          snapBack(inner);
          return;
        }

        e.preventDefault(); // 水平スクロールを防止

        if (diffX > 0) {
          // 左スワイプ: note パネルを露出（内側を左へ）
          moveX = Math.min(diffX, PANEL_WIDTH);
          inner.style.transform = 'translateX(-' + moveX + 'px)';
        } else {
          // 右スワイプ: exclude パネルを露出（内側を右へ）
          moveX = Math.max(diffX, -PANEL_WIDTH); // 負の値、最大 -PANEL_WIDTH
          inner.style.transform = 'translateX(' + (-moveX) + 'px)';
        }
      }, { passive: false });

      header.addEventListener('touchend', function () {
        if (!isTracking) return;
        isTracking = false;

        if (moveX >= SNAP_THRESHOLD) {
          // 左スワイプ確定: note パネルを開く
          openPanel(header, inner, 'left');
        } else if (moveX <= -SNAP_THRESHOLD) {
          // 右スワイプ確定: exclude パネルを開く
          openExcludePanel(header, inner);
        } else {
          snapBack(inner);
        }
      });

      header.addEventListener('touchcancel', function () {
        if (!isTracking) return;
        isTracking = false;
        snapBack(inner);
      });
    });
  }

  // ============================================================
  // アクションパネルの開閉
  // ============================================================
  function openPanel(header, inner) {
    // 他に開いているパネルを閉じる
    if (currentOpenHeader && currentOpenHeader !== header) {
      closePanel(currentOpenHeader);
    }

    inner.style.transition = 'transform 0.2s ease';
    inner.style.transform  = 'translateX(-' + PANEL_WIDTH + 'px)';
    currentOpenHeader = header;
    currentOpenDirection = 'left';
    if (navigator.vibrate) navigator.vibrate(HAPTIC_MS);

    // アクションパネルのタップ: 一度だけバインド
    var panel = header.querySelector('.note-action-panel');
    if (panel && !panel._tapBound) {
      panel._tapBound = true;
      panel.addEventListener('click', function () {
        snapBack(inner);
        currentOpenHeader = null;
        currentOpenDirection = null;
        openSheet(header);
      });
    }
  }

  function openExcludePanel(header, inner) {
    // 他に開いているパネルを閉じる
    if (currentOpenHeader && currentOpenHeader !== header) {
      closePanel(currentOpenHeader);
    }

    inner.style.transition = 'transform 0.2s ease';
    inner.style.transform  = 'translateX(' + PANEL_WIDTH + 'px)';
    currentOpenHeader = header;
    currentOpenDirection = 'right';
    if (navigator.vibrate) navigator.vibrate(HAPTIC_MS);

    // 除外パネルのタップ: 一度だけバインド
    var panel = header.querySelector('.exclude-action-panel');
    if (panel && !panel._excludeTapBound) {
      panel._excludeTapBound = true;
      panel.addEventListener('click', function () {
        snapBack(inner);
        currentOpenHeader = null;
        currentOpenDirection = null;
        toggleExclude(header);
      });
    }
  }

  function closePanel(header) {
    var inner = header.querySelector('.diary-header-inner');
    if (inner) snapBack(inner);
  }

  function snapBack(inner) {
    inner.style.transition = 'transform 0.2s ease';
    inner.style.transform  = 'translateX(0)';
  }

  // ============================================================
  // 除外フラグのトグル
  // ============================================================
  function toggleExclude(header) {
    var url = header.dataset.toggleExcludeUrl;
    if (!url) return;

    var csrfToken = getCsrfToken();
    var article = header.closest('.diary-article');

    fetch(url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': csrfToken,
        'X-Requested-With': 'XMLHttpRequest',
      },
    })
    .then(function (res) {
      if (!res.ok) throw new Error('toggle failed');
      // サーバーの状態を反映: data属性とパネル表示を更新
      var isNowExcluded = header.dataset.isExcluded !== 'true';
      header.dataset.isExcluded = isNowExcluded ? 'true' : 'false';

      // パネルのアイコン・ラベルを切り替え
      var panel = header.querySelector('.exclude-action-panel');
      if (panel) {
        panel.innerHTML = isNowExcluded
          ? '<i class="bi bi-arrow-counterclockwise"></i><span>戻す</span>'
          : '<i class="bi bi-archive"></i><span>アーカイブ</span>';
        panel.setAttribute('aria-label', isNowExcluded ? 'アーカイブから戻す' : 'アーカイブする');
        // _excludeTapBound はリセットしない: リスナーは再利用し二重バインドを防ぐ
      }

      // カードのグレーアウトを切り替え
      if (article) {
        article.classList.toggle('diary-article--excluded', isNowExcluded);
        // 除外済み一覧で解除した場合はカードを削除
        var statusInput = document.getElementById('statusFilter');
        if (!isNowExcluded && statusInput && statusInput.value === 'excluded') {
          article.style.transition = 'opacity 0.3s ease';
          article.style.opacity = '0';
          setTimeout(function () { article.remove(); }, 300);
        }
      }
    })
    .catch(function (err) {
      console.error('除外フラグの切り替えに失敗しました:', err);
    });
  }

  // ヘッダー外タップでパネルを閉じる
  document.addEventListener('touchstart', function (e) {
    if (!currentOpenHeader) return;
    if (!currentOpenHeader.contains(e.target)) {
      closePanel(currentOpenHeader);
      currentOpenHeader = null;
      currentOpenDirection = null;
    }
  }, { passive: true });

  // ============================================================
  // ボトムシートを開く
  // ============================================================
  function openSheet(header) {
    var stockName = header.dataset.stockName || '';
    var noteUrl   = header.dataset.quickNoteUrl || '';
    var noteTopic = header.dataset.noteTopics || '';

    var titleEl = document.getElementById('qnSheetTitle');
    if (titleEl) {
      titleEl.textContent = stockName ? stockName + '  継続記録を追加' : '継続記録を追加';
    }

    var urlInput = document.getElementById('qnNoteUrl');
    if (urlInput) urlInput.value = noteUrl;

    resetForm();

    // 日記ごとの topics を動的に topic-chips に設定
    var topicChipsContainer = document.querySelector('.bottom-sheet-body .topic-chips');
    if (topicChipsContainer) {
      topicChipsContainer.innerHTML = '';
      if (noteTopic) {
        var topics = noteTopic.split('|').filter(t => t.trim());
        topics.forEach(function(topic) {
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'topic-chip btn btn-sm btn-outline-secondary';
          btn.textContent = '# ' + topic;
          btn.onclick = function() {
            setQnTopic(this, topic);
          };
          topicChipsContainer.appendChild(btn);
        });
      }
    }

    if (typeof openBottomSheet === 'function') {
      openBottomSheet('quickNoteFromHomeSheet');
    }
  }

  function resetForm() {
    // テーマをリセット
    var topicEl = document.getElementById('qnTopic');
    if (topicEl) topicEl.value = '';

    // 本文をリセット
    var ta = document.getElementById('qnContent');
    if (ta) { ta.value = ''; updateQnCharCount(); }

    // topic-chips の active クラスをリセット
    document.querySelectorAll('.topic-chip').forEach(function (btn) {
      btn.classList.remove('active');
    });
  }

  // フォーム送信は home.html の window.qnSubmitNote で定義

  // ============================================================
  // 継続タブのバッジカウントを1増やす
  // ============================================================
  function incrementNoteBadge(url) {
    var match = url.match(/\/quick\/(\d+)\/note\//);
    if (!match) return;
    var diaryId = match[1];
    var badge = document.querySelector(
      '.diary-article[data-diary-id="' + diaryId + '"] ' +
      '[data-tab="notes-' + diaryId + '"] .tab-badge'
    );
    if (badge) {
      badge.textContent = parseInt(badge.textContent || '0', 10) + 1;
    }
  }

  // ============================================================
  // 文字数カウンター（グローバル公開）
  // ============================================================
  function updateQnCharCount() {
    var ta      = document.getElementById('qnContent');
    var counter = document.getElementById('qnCharCount');
    if (!ta || !counter) return;
    var len = ta.value.length;
    counter.textContent = len + ' / 3000';
    counter.classList.toggle('text-danger', len > 3000);
    counter.classList.toggle('text-warning', len > 2700 && len <= 3000);
  }

  // ============================================================
  // CSRF トークン取得
  // ============================================================
  function getCsrfToken() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    var meta = document.querySelector('meta[name=csrf-token]');
    if (meta) return meta.getAttribute('content');
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  // ============================================================
  // フッター「記録追加」ボタンのバインド
  // ============================================================
  function setupFooterButtons() {
    document.querySelectorAll('.note-add-footer-btn').forEach(function (btn) {
      if (btn._footerBound) return;
      btn._footerBound = true;
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var selector = btn.dataset.diaryHeader;
        if (!selector) return;
        var header = document.querySelector(selector);
        if (header) openSheet(header);
      });
    });
  }

  // ============================================================
  // 初回訪問時スワイプヒント（セッション1回だけ）
  // ============================================================
  function showSwipeHintOnce() {
    // タッチデバイスのみ
    if (!('ontouchstart' in window)) return;
    // 1セッション1回だけ
    if (sessionStorage.getItem('swipe-hint-shown')) return;
    sessionStorage.setItem('swipe-hint-shown', '1');

    // 最初のカードの header-inner で peek
    var firstInner = document.querySelector('.diary-header .diary-header-inner');
    if (!firstInner) return;

    setTimeout(function () {
      firstInner.classList.add('swipe-peek');
      firstInner.addEventListener('animationend', function () {
        firstInner.classList.remove('swipe-peek');
      }, { once: true });
    }, 1800); // カード表示アニメーション後
  }

  // ============================================================
  // グローバル公開
  // ============================================================
  // qnSubmitNote は home.html で定義（topic パラメータ対応版）
  window.updateQnCharCount   = updateQnCharCount;
  // フッターボタン等の外部から openSheet を呼べるよう公開
  window.openQuickNoteSheet  = openSheet;

  // ============================================================
  // 初期化
  // ============================================================
  document.addEventListener('DOMContentLoaded', function () {
    setupSwipeHandlers();
    setupFooterButtons();
    showSwipeHintOnce();
  });

  // HTMX の再レンダリング後にも再バインド
  document.addEventListener('htmx:afterSwap', function (e) {
    if (e.detail && e.detail.target && e.detail.target.id === 'diary-container') {
      setupSwipeHandlers();
      setupFooterButtons();
      showSwipeHintOnce();
    }
  });

}());
