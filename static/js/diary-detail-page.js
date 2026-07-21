/**
 * diary-detail-page.js
 * detail.html にインラインで書かれていたページ固有スクリプトの外部化。
 * ページ固有の設定・データは detail.html 内の window.DIARY_DETAIL_CONFIG から受け取る
 * （テンプレート変数を JS 本体に埋め込まないための受け渡し口）。
 * 実行順序はテンプレート内での元の記述順を維持している。
 */

  // 記録タブの『仮説』ビューへ移動する共通ヘルパー。
  // 仮説追加はボトムシート(#addThesisSheet)に一本化したため、ここではビュー遷移のみを担う。
  window.goToThesisView = function () {
    var tabBtn = document.getElementById('notes-tab');
    if (tabBtn && !tabBtn.classList.contains('active')) { tabBtn.click(); }
    if (window.switchNotesView) { window.switchNotesView('thesis'); }
  };
  // 仮説シート保存後（?view=thesis）に仮説ビューへ着地させる。
  // switchNotesView は DOMContentLoaded 内で定義されるため、定義後に確実に走る load で実行する。
  (function () {
    if (new URLSearchParams(window.location.search).get('view') !== 'thesis') { return; }
    window.addEventListener('load', function () {
      window.goToThesisView();
      var block = document.getElementById('karte-block');
      if (block) { block.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    });
  })();


// 想起カードの「答え合わせをする」(?verify=<thesis_id>) で着地したとき、
// 記録タブの『仮説』ビューを開いてカルテへスクロールする。検証フォーム自体は
// サーバー側が ?verify= を見て最初から描画済み（HTMXボタンの起動連鎖には依存しない）。
(function() {
  var vid = new URLSearchParams(window.location.search).get('verify');
  if (!vid) return;
  function openVerify() {
    var scrollToKarte = function() {
      var block = document.getElementById('karte-block');
      if (block) block.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };
    // タブ切替（Bootstrap）は非同期。切替完了(shown.bs.tab)を待ってからスクロールする
    var tabBtn = document.getElementById('notes-tab');
    var needsSwitch = tabBtn && !tabBtn.classList.contains('active');
    if (needsSwitch) { tabBtn.addEventListener('shown.bs.tab', scrollToKarte, { once: true }); }
    if (window.goToThesisView) { window.goToThesisView(); }
    if (!needsSwitch) { scrollToKarte(); }
  }
  // switchNotesView は DOMContentLoaded 内で定義されるため、定義後に確実に走る load で実行する。
  window.addEventListener('load', openVerify);
})();


// ============================================
// 継続記録フォーム: Markdown エディタ & 文字数カウンター
// ============================================
document.addEventListener('DOMContentLoaded', function() {
(function() {
  const MAX_NOTE_LENGTH = 5000;
  let noteEasyMDE = null;

  // EasyMDE ダークモード対応スタイル
  const dmStyle = document.createElement('style');
  dmStyle.textContent = `
    .dark-mode .EasyMDEContainer .CodeMirror,
    [data-theme="dark"] .EasyMDEContainer .CodeMirror {
      background: rgba(71, 85, 105, 0.3) !important;
      color: var(--dm-text-100) !important;
      border-color: var(--dm-border) !important;
    }
    .dark-mode .EasyMDEContainer .editor-toolbar,
    [data-theme="dark"] .EasyMDEContainer .editor-toolbar {
      background-color: var(--dm-card) !important;
      border-color: var(--dm-border) !important;
    }
    .dark-mode .EasyMDEContainer .editor-toolbar button,
    [data-theme="dark"] .EasyMDEContainer .editor-toolbar button {
      color: var(--dm-text-200) !important;
    }
    .dark-mode .EasyMDEContainer .editor-toolbar button:hover,
    .dark-mode .EasyMDEContainer .editor-toolbar button.active,
    [data-theme="dark"] .EasyMDEContainer .editor-toolbar button:hover,
    [data-theme="dark"] .EasyMDEContainer .editor-toolbar button.active {
      background: rgba(89, 142, 243, 0.15) !important;
      border-color: var(--dm-primary-200) !important;
      color: var(--dm-primary-200) !important;
    }
    .dark-mode .EasyMDEContainer .editor-toolbar i.separator,
    [data-theme="dark"] .EasyMDEContainer .editor-toolbar i.separator {
      border-left-color: var(--dm-border) !important;
    }
    .dark-mode .EasyMDEContainer .editor-preview,
    [data-theme="dark"] .EasyMDEContainer .editor-preview {
      background: var(--dm-card) !important;
      color: var(--dm-text-100) !important;
    }
    /* 文字数カウンター */
    .character-count { font-size: 0.875rem; color: var(--text-muted); }
    .character-count.warning { color: var(--warning-color); }
    .character-count.danger { color: var(--danger-color); }
    .dark-mode .character-count,
    [data-theme="dark"] .character-count { color: var(--dm-text-200) !important; }
    /* 変更3: EasyMDE モバイル最適化 */
    @media (max-width: 768px) {
      .EasyMDEContainer .editor-toolbar {
        flex-wrap: wrap;
        overflow-x: visible;
        padding: 4px;
      }
      .EasyMDEContainer .editor-toolbar button {
        min-width: 44px;
        min-height: 44px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
      }
    }
  `;
  document.head.appendChild(dmStyle);

  function updateNoteCharCount(text) {
    const count = (text || '').length;
    const el = document.getElementById('noteContentCharCount');
    if (!el) return;
    el.textContent = count + ' / ' + MAX_NOTE_LENGTH + '文字';
    el.classList.remove('warning', 'danger');
    if (count > MAX_NOTE_LENGTH * 0.9) {
      el.classList.add('danger');
    } else if (count > MAX_NOTE_LENGTH * 0.7) {
      el.classList.add('warning');
    }
  }

  function initNoteEasyMDE() {
    if (noteEasyMDE) return; // 既に初期化済み
    const textarea = document.getElementById('content');
    if (!textarea) return;

    noteEasyMDE = new EasyMDE({
      element: textarea,
      toolbar: easymdeBiToolbar([
        'bold', 'italic', '|',
        'unordered-list', 'ordered-list', '|',
        'quote', 'code', '|',
        'preview', 'side-by-side', 'fullscreen', '|',
        'guide'
      ]),
      placeholder: '状況の変化や新たな情報を記録しましょう\n\n**太字**、*斜体*、箇条書きなどMarkdown記法が使えます\n\n@ でタグ、[[ で他の日記を参照できます',
      autosave: { enabled: false },
      spellChecker: false,
      status: false,
      minHeight: '120px',
      maxHeight: '400px',
      renderingConfig: { singleLineBreaks: true },
    });

    // 文字数カウンター: EasyMDE のテキスト変更イベントにフック
    noteEasyMDE.codemirror.on('change', function() {
      updateNoteCharCount(noteEasyMDE.value());
    });

    // 初期値を反映
    updateNoteCharCount(noteEasyMDE.value());

    // @ハッシュタグ／[[日記メンション オートコンプリート（継続ノートフィールド）
    new HashtagMentionAutocomplete(
      noteEasyMDE.codemirror,
      window.DIARY_DETAIL_CONFIG.urls.hashtags
    );
    new DiaryMentionAutocomplete(
      noteEasyMDE.codemirror,
      window.DIARY_DETAIL_CONFIG.urls.searchMyDiaries
    );
  }

  function resetNoteEasyMDE() {
    if (noteEasyMDE) {
      noteEasyMDE.value('');
      updateNoteCharCount('');
    }
  }

  // ボトムシートのonOpenでEasyMDE初期化
  window.bottomSheets = window.bottomSheets || {};
  window.bottomSheets['addNoteSheet'] = new BottomSheet('addNoteSheet', {
    onOpen: function() {
      initNoteEasyMDE();
      setTimeout(function() {
        if (noteEasyMDE) {
          noteEasyMDE.codemirror.focus();
        }
      }, 100);
    },
    onClose: function() {
      resetNoteEasyMDE();
    }
  });

  // フォーム送信前: textarea に値を明示的に同期してからバリデーション
  const quickNoteForm = document.getElementById('quickNoteForm');
  if (quickNoteForm) {
    quickNoteForm.addEventListener('submit', function(e) {
      // テーマ入力・画像添付（圧縮待ち）を挟むとシートを開いてから送信までが長くなり、
      // フォーム内に埋め込まれたCSRFトークンが古くなって送信が弾かれることがある。
      // 送信直前に最新のcsrftoken Cookieへ差し替えて備える。
      const csrfInput = quickNoteForm.querySelector('input[name="csrfmiddlewaretoken"]');
      const freshToken = typeof getCookie === 'function' ? getCookie('csrftoken') : null;
      if (csrfInput && freshToken) {
        csrfInput.value = freshToken;
      }

      const textarea = document.getElementById('content');
      if (noteEasyMDE && textarea) {
        // EasyMDE の内容を textarea に確実に同期する
        const text = noteEasyMDE.value();
        textarea.value = text;

        if (!text.trim()) {
          e.preventDefault();
          alert('記録内容を入力してください。');
          noteEasyMDE.codemirror.focus();
          return false;
        }
        if (text.length > MAX_NOTE_LENGTH) {
          e.preventDefault();
          alert('記録内容は' + MAX_NOTE_LENGTH + '文字以内で入力してください。\n現在: ' + text.length + '文字');
          noteEasyMDE.codemirror.focus();
          return false;
        }
      } else if (textarea && !textarea.value.trim()) {
        // EasyMDE が未初期化の場合も空チェック
        e.preventDefault();
        alert('記録内容を入力してください。');
        textarea.focus();
        return false;
      }
    });
  }

  // ============================================
  // 継続記録: 新規/編集モード切替・テーマUI
  // ============================================
  window.setNoteFormAddMode = function() {
    if (!quickNoteForm) return;
    quickNoteForm.action = quickNoteForm.dataset.addAction;
    var title = document.getElementById('addNoteSheetTitle');
    if (title) title.innerHTML = '<i class="bi bi-journal-plus me-2"></i>記録を追加';
  };

  window.resetNoteFormToAdd = function() {
    window.setNoteFormAddMode();
    var dateEl = document.getElementById('date');
    if (dateEl) dateEl.value = new Date().toISOString().slice(0, 10);
    var typeEl = document.getElementById('note_type');
    if (typeEl) { typeEl.value = 'analysis'; }
    var topicEl = document.getElementById('note_topic');
    if (topicEl) topicEl.value = '';
    document.querySelectorAll('.topic-chip.active').forEach(function(c) { c.classList.remove('active'); });
    var priceEl = document.getElementById('note_current_price');
    if (priceEl) priceEl.value = '';
    var srcEl = document.getElementById('note_source_doc_id');
    if (srcEl) srcEl.value = '';
    if (typeof resetNoteEasyMDE === 'function') resetNoteEasyMDE();
    var ta = document.getElementById('content');
    if (ta) { ta.value = ''; ta.placeholder = '状況の変化や新たな情報を記録しましょう'; }
    var fileEl = document.getElementById('note_image');
    if (fileEl) fileEl.value = '';
    var preview = document.getElementById('note-image-preview-container');
    if (preview) preview.style.display = 'none';
  };

  // 振り返り記入: 継続記録シートを retrospective プリセット+取引サマリーのプリフィルで開く
  window.startRetrospective = function() {
    window.resetNoteFormToAdd();
    var typeEl = document.getElementById('note_type');
    if (typeEl) typeEl.value = 'retrospective';
    var title = document.getElementById('addNoteSheetTitle');
    if (title) title.innerHTML = '<i class="bi bi-arrow-counterclockwise me-2"></i>この投資の振り返り';
    // 固定テーマでスレッド集約（サーバー側 DiaryNote.clean がフォールバック）
    var topicEl = document.getElementById('note_topic');
    if (topicEl && !topicEl.value) topicEl.value = '振り返り';
    // 取引サマリーを本文へプリフィル（保有中の日記では json_script 自体が出力されない）
    var prefill = '';
    var prefillEl = document.getElementById('retro-prefill');
    if (prefillEl) {
      try { prefill = JSON.parse(prefillEl.textContent); } catch (e) { prefill = ''; }
    }
    openBottomSheet('addNoteSheet');
    if (typeof initNoteEasyMDE === 'function') initNoteEasyMDE();
    if (noteEasyMDE) {
      noteEasyMDE.value(prefill);
    } else {
      var ta = document.getElementById('content');
      if (ta && prefill) ta.value = prefill;
    }
  };

  window.openEditNoteSheet = function(noteId) {
    // テーマ別モーダルが開いていれば先に閉じる（z-index 衝突防止）
    closeNoteDetailModal();
    var card = document.querySelector('.note-card[data-note-id="' + noteId + '"]');
    if (!card || !quickNoteForm) return;
    window.resetNoteFormToAdd();

    // 編集モードへ
    quickNoteForm.action = quickNoteForm.dataset.editBase + noteId + '/edit/';
    var title = document.getElementById('addNoteSheetTitle');
    if (title) title.innerHTML = '<i class="bi bi-pencil-square me-2"></i>記録を編集';

    var dateEl = document.getElementById('date');
    if (dateEl) dateEl.value = card.dataset.noteDate || '';
    var typeEl = document.getElementById('note_type');
    if (typeEl) { typeEl.value = card.dataset.noteType; }
    var topicEl = document.getElementById('note_topic');
    if (topicEl) topicEl.value = card.dataset.noteTopic || '';
    var priceEl = document.getElementById('note_current_price');
    if (priceEl) priceEl.value = card.dataset.notePrice || '';

    var raw = document.querySelector('.note-raw-content[data-for-note="' + noteId + '"]');
    var content = raw ? raw.value : '';

    openBottomSheet('addNoteSheet');
    if (typeof initNoteEasyMDE === 'function') initNoteEasyMDE();
    if (noteEasyMDE) {
      noteEasyMDE.value(content);
      updateNoteCharCount(content);
    } else {
      var ta = document.getElementById('content');
      if (ta) ta.value = content;
    }
  };

  window.setNoteTopic = function(el, value) {
    var topicEl = document.getElementById('note_topic');
    if (topicEl) topicEl.value = value;
    document.querySelectorAll('.topic-chip').forEach(function(c) { c.classList.remove('active'); });
    if (el) el.classList.add('active');
  };

  window.switchNotesView = function(view) {
    var timeline = document.getElementById('notes-view-timeline');
    var topic = document.getElementById('notes-view-topic');
    var thesis = document.getElementById('notes-view-thesis');
    var activity = document.getElementById('notes-view-activity');
    if (timeline) timeline.hidden = (view !== 'timeline');
    if (topic) topic.hidden = (view !== 'topic');
    if (thesis) thesis.hidden = (view !== 'thesis');
    if (activity) activity.hidden = (view !== 'activity');
    document.querySelectorAll('.notes-view-btn').forEach(function(b) {
      b.classList.toggle('active', b.dataset.view === view);
    });
    if (view === 'timeline') {
      window.rebuildTimelineStepper();
    } else {
      var tStep = document.getElementById('timelineStepper');
      _teardownStepper(tStep);
      if (tStep) tStep.style.display = 'none';
    }
  };

  // テーマ別モーダル
  var _noteTopics = window.DIARY_DETAIL_CONFIG.noteTopics || [];
  var _currentTopicIdx = 0;

  window.openNoteDetailModal = function(topic) {
    var idx = _noteTopics.findIndex(function(t) { return t.topic === topic; });
    _currentTopicIdx = idx >= 0 ? idx : 0;
    _renderNoteDetailModal();
    var overlay = document.getElementById('noteDetailOverlay');
    if (overlay) { overlay.removeAttribute('hidden'); }
    document.body.style.overflow = 'hidden';
  };

  window.closeNoteDetailModal = function() {
    var overlay = document.getElementById('noteDetailOverlay');
    if (overlay) { overlay.setAttribute('hidden', ''); }
    document.body.style.overflow = '';
    _teardownStepper(document.getElementById('noteDetailStepper'));
  };

  window.navigateTopic = function(dir) {
    _currentTopicIdx = Math.max(0, Math.min(_noteTopics.length - 1, _currentTopicIdx + dir));
    _renderNoteDetailModal();
  };

  function _renderNoteDetailModal() {
    if (!_noteTopics.length) return;
    var data = _noteTopics[_currentTopicIdx];
    var titleEl = document.getElementById('noteDetailTitle');
    var countEl = document.getElementById('noteDetailCount');
    var prevBtn = document.getElementById('noteDetailPrev');
    var nextBtn = document.getElementById('noteDetailNext');
    var body    = document.getElementById('noteDetailBody');
    if (titleEl) titleEl.textContent = data.label;
    if (countEl) countEl.textContent = (_currentTopicIdx + 1) + ' / ' + _noteTopics.length;
    if (prevBtn) prevBtn.disabled = (_currentTopicIdx === 0);
    if (nextBtn) nextBtn.disabled = (_currentTopicIdx === _noteTopics.length - 1);
    if (body) {
      body.innerHTML = '';
      data.noteIds.forEach(function(noteId) {
        var card = document.querySelector('#note-card-store .note-card[data-note-id="' + noteId + '"]');
        // フィルター後、実際に表示中のビューに同じ ID のカードが存在し、かつ display が '' (表示中) の場合のみ追加
        if (card) {
          var displayCard = document.querySelector('.notes-list .note-card[data-note-id="' + noteId + '"], #notes-view-topic .note-card[data-note-id="' + noteId + '"]');
          if (displayCard && displayCard.style.display !== 'none') {
            body.appendChild(card.cloneNode(true));
          }
        }
      });
      body.scrollTop = 0;
      // カード送りステッパーを再生成（表示中カードのみ）
      var cards = Array.from(body.querySelectorAll('.note-card[data-note-id]'));
      _buildStepper(document.getElementById('noteDetailStepper'), body, cards);
    }
  }

  // ===== カード送りステッパー（前/次ボタンで1件ずつ移動）=====
  var _prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var _STEP_OFFSET = 84; // 時系列(window)スクロール時のヘッダーオフセット

  function _teardownStepper(el) {
    if (!el || !el._state) return;
    var s = el._state;
    if (s.obs) s.obs.disconnect();
    if (s.cleanup) s.cleanup();
    el._state = null;
  }

  // el: ステッパー要素 / scrollMode: 'window' または スクロールコンテナDOM / cardEls: 表示中カード(降順)
  function _buildStepper(el, scrollMode, cardEls) {
    if (!el) return;
    _teardownStepper(el);
    // 1件以下は不要
    if (!cardEls || cardEls.length <= 1) { el.style.display = 'none'; return; }
    el.style.display = 'flex';

    var up   = el.querySelector('.note-stepper-up');
    var down = el.querySelector('.note-stepper-down');
    var N = cardEls.length;
    var state = { current: 0, obs: null, cleanup: null };
    el._state = state;
    var behavior = _prefersReducedMotion ? 'auto' : 'smooth';

    function scrollToIndex(i) {
      i = Math.max(0, Math.min(N - 1, i));
      var card = cardEls[i];
      if (!card) return;
      state.current = i;
      if (scrollMode === 'window') {
        var y = card.getBoundingClientRect().top + window.pageYOffset - _STEP_OFFSET;
        window.scrollTo({ top: y, behavior: behavior });
      } else {
        card.scrollIntoView({ block: 'start', behavior: behavior });
      }
      updateDisabled();
    }
    function updateDisabled() {
      if (up)   up.disabled   = (state.current <= 0);
      if (down) down.disabled = (state.current >= N - 1);
    }
    function onUp()   { scrollToIndex(state.current - 1); }
    function onDown() { scrollToIndex(state.current + 1); }
    if (up)   up.addEventListener('click', onUp);
    if (down) down.addEventListener('click', onDown);
    state.cleanup = function () {
      if (up)   up.removeEventListener('click', onUp);
      if (down) down.removeEventListener('click', onDown);
    };

    // 現在地（最上位の表示中カード）を追従して前/次の活性状態を更新
    if ('IntersectionObserver' in window) {
      var visible = new Map();
      var root = (scrollMode === 'window') ? null : scrollMode;
      var rootMargin = (scrollMode === 'window')
        ? ('-' + _STEP_OFFSET + 'px 0px -55% 0px')
        : '0px 0px -60% 0px';
      state.obs = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          var idx = cardEls.indexOf(en.target);
          if (idx < 0) return;
          if (en.isIntersecting) visible.set(idx, en.intersectionRatio);
          else visible.delete(idx);
        });
        if (!visible.size) return;
        var minIdx = Infinity;
        visible.forEach(function (_, idx) { if (idx < minIdx) minIdx = idx; });
        if (minIdx !== Infinity) { state.current = minIdx; updateDisabled(); }
      }, { root: root, rootMargin: rootMargin, threshold: [0, 1] });
      cardEls.forEach(function (c) { state.obs.observe(c); });
    }

    updateDisabled();
  }

  function _visibleTimelineCards() {
    return Array.from(document.querySelectorAll('#timelineCards .note-card[data-note-id]'))
      .filter(function (c) { return c.style.display !== 'none'; });
  }

  // 時系列ビュー用ステッパーの（再）生成。継続記録タブ＋時系列ビュー表示中のみ出す
  window.rebuildTimelineStepper = function () {
    var el = document.getElementById('timelineStepper');
    var wrap = document.getElementById('notes-view-timeline');
    var pane = document.getElementById('notes-content');
    var notesActive = pane && pane.classList.contains('active');
    if (!el || !wrap || wrap.hidden || !notesActive) {
      _teardownStepper(el);
      if (el) el.style.display = 'none';
      return;
    }
    _buildStepper(el, 'window', _visibleTimelineCards());
  };

  // タブ切替時にステッパーの表示可否を再判定（他タブへ移動したら隠す）
  document.querySelectorAll('#diaryDetailTabs .improved-tab-link').forEach(function (t) {
    t.addEventListener('shown.bs.tab', function () { window.rebuildTimelineStepper(); });
  });

  // URLハッシュで直接フォームを開く場合はボトムシートを開く
  if (window.location.hash === '#add-note') {
    setTimeout(function() { resetNoteFormToAdd(); openBottomSheet('addNoteSheet'); }, 300);
  }
})();
}); // DOMContentLoaded


document.addEventListener('DOMContentLoaded', function() {
    // loadNotifications()をtry-catchで囲む
    try {
        loadNotifications();
    } catch (error) {
        console.error('通知読み込みエラー:', error);
    }

    // ============================================
    // 投資理由 全画面モーダル
    // ============================================
    try {
        const reasonExpandBtn = document.getElementById('reasonExpandBtn');
        const reasonOverlay = document.getElementById('reasonFullscreenOverlay');
        const reasonClose = document.getElementById('reasonFullscreenClose');

        // デバッグログ（開発環境のみ）
        if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
            console.log('投資理由モーダル要素:', {
                button: reasonExpandBtn,
                overlay: reasonOverlay,
                close: reasonClose
            });
        }

        if (reasonExpandBtn && reasonOverlay) {
            reasonExpandBtn.addEventListener('click', function() {
                console.log('全画面ボタンがクリックされました');
                reasonOverlay.classList.add('active');
                document.body.style.overflow = 'hidden';
            });

            function closeReasonFullscreen() {
                reasonOverlay.classList.remove('active');
                document.body.style.overflow = '';
            }

            if (reasonClose) {
                reasonClose.addEventListener('click', closeReasonFullscreen);
            }

            reasonOverlay.addEventListener('click', function(e) {
                if (e.target === reasonOverlay) {
                    closeReasonFullscreen();
                }
            });

            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape' && reasonOverlay.classList.contains('active')) {
                    closeReasonFullscreen();
                }
            });
        } else {
            console.warn('投資理由モーダルの要素が見つかりません:', {
                button: !!reasonExpandBtn,
                overlay: !!reasonOverlay
            });
        }
    } catch (error) {
        console.error('投資理由モーダルの初期化エラー:', error);
    }
    
    // プッシュ通知ステータス更新時のUI更新
    document.addEventListener('pushStatusUpdated', function(e) {
        const status = e.detail;
        const statusDiv = document.getElementById('push-status');
        const icon = statusDiv.querySelector('.push-status-icon');
        const valueDiv = statusDiv.querySelector('.push-status-value');
        
        if (status.enabled) {
            icon.innerHTML = '<i class="bi bi-check-circle-fill"></i>';
            icon.className = 'push-status-icon push-status-enabled';
            valueDiv.textContent = '有効';
        } else {
            icon.innerHTML = '<i class="bi bi-x-circle"></i>';
            icon.className = 'push-status-icon push-status-disabled';
            valueDiv.textContent = '無効';
        }
    });

    // 画像プレビュー表示
    const noteImageInput = document.getElementById('note_image');
    const noteImagePreview = document.getElementById('note-image-preview');
    const noteImagePreviewContainer = document.getElementById('note-image-preview-container');
    const noteRemoveImageBtn = document.getElementById('note-remove-image');

    if (noteImageInput && noteImagePreview && noteImagePreviewContainer && noteRemoveImageBtn) {
        noteImageInput.addEventListener('change', function(event) {
            const file = event.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    noteImagePreview.src = e.target.result;
                    noteImagePreviewContainer.style.display = 'block';
                }
                reader.readAsDataURL(file);
            }
        });

        noteRemoveImageBtn.addEventListener('click', function() {
            noteImageInput.value = '';
            noteImagePreview.src = '#';
            noteImagePreviewContainer.style.display = 'none';
        });
    }
});

async function loadNotifications() {
    const container = document.getElementById('notification-list-container');
    
    try {
        const response = await fetch(window.DIARY_DETAIL_CONFIG.urls.notifications);
        const data = await response.json();
        
        if (data.notifications && data.notifications.length > 0) {
            container.innerHTML = renderNotifications(data.notifications);
        } else {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">
                        <i class="bi bi-bell-slash"></i>
                    </div>
                    <h4>通知設定はありません</h4>
                    <p>「新規作成」ボタンから通知を設定できます</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('通知一覧の読み込みエラー:', error);
        container.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-circle me-2"></i>
                通知一覧の読み込みに失敗しました
            </div>
        `;
    }
}

function renderNotifications(notifications) {
  return notifications.map(notification => {
    const remindAt = new Date(notification.remind_at);
    const detailsHtml = `
      <div class="notification-details">
        <i class="bi bi-calendar-event"></i>
        <span>${remindAt.toLocaleString('ja-JP', {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit'
        })}</span>
      </div>
    `;
    
    return `
      <div class="notification-item" data-notification-id="${notification.id}">
        <div class="notification-item-header">
          <div class="notification-header-left">
            <div class="notification-type-icon reminder">
              <i class="bi-alarm"></i>
            </div>
            <span class="badge badge-info">
              <i class="bi-alarm me-1"></i>
              リマインダー
            </span>
          </div>
          <div class="notification-actions">
            <button class="btn-icon btn-danger-icon"
                    onclick="deleteDiaryNotification('${notification.id}')"
                    title="削除"
                    aria-label="通知を削除">
              <i class="bi bi-trash"></i>
            </button>
          </div>
        </div>
        
        <div class="notification-details-section">
          ${detailsHtml}
          ${notification.last_sent ? 
            `<div class="notification-last-sent">
              <i class="bi bi-check-circle"></i>
              最終送信: ${new Date(notification.last_sent).toLocaleDateString('ja-JP', {
                year: 'numeric',
                month: 'short',
                day: 'numeric'
              })}
            </div>` : 
            '<div class="notification-last-sent"><i class="bi bi-clock"></i>未送信</div>'
          }
        </div>
        
        ${notification.message ? 
          `<div class="notification-message">
            <i class="bi bi-chat-quote"></i>
            ${notification.message}
          </div>` : 
          ''
        }
      </div>
    `;
  }).join('');
}

async function deleteDiaryNotification(notificationId) {
    if (!confirm('この通知設定を削除しますか？')) {
        return;
    }
    
    try {
        const csrfToken = window.getCSRFToken();
        
        if (!csrfToken) {
            console.error('❌ CSRFトークンが取得できません');
            showToast('セキュリティトークンの取得に失敗しました。ページを再読み込みしてください。', 'danger');
            return;
        }
        
        const response = await fetch(window.DIARY_DETAIL_CONFIG.urls.notificationDeleteTemplate.replace('00000000-0000-0000-0000-000000000000', notificationId), {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken,
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            credentials: 'same-origin'
        });
        
        const contentType = response.headers.get('content-type');
        if (!contentType || !contentType.includes('application/json')) {
            const responseText = await response.text();
            console.error('❌ JSONではないレスポンス:', responseText.substring(0, 200));
            
            if (response.status === 403) {
                showToast('セッションが無効です。ページを再読み込みしてください。', 'danger');
            } else if (response.status === 404) {
                showToast('通知が見つかりませんでした。すでに削除された可能性があります。', 'warning');
                loadNotifications();
            } else {
                showToast('サーバーエラーが発生しました。', 'danger');
            }
            return;
        }
        
        const data = await response.json();
        
        if (response.ok && data.success) {
            showToast('通知設定を削除しました', 'success');
            loadNotifications();
        } else {
            showToast('削除に失敗しました: ' + (data.error || '不明なエラー'), 'danger');
        }
    } catch (error) {
        console.error('削除エラー:', error);
        showToast('削除中にエラーが発生しました', 'danger');
    }
}

window.deleteDiaryNotification = deleteDiaryNotification;
window.loadNotifications = loadNotifications;

// ==========================================
// 関連日記
// ==========================================
(function() {
  const DIARY_ID = window.DIARY_DETAIL_CONFIG.diaryId;
  const BASE_URL = window.DIARY_DETAIL_CONFIG.urls.relatedSearch;
  const ADD_URL  = window.DIARY_DETAIL_CONFIG.urls.relatedAdd;

  let searchTimer;

  // CSRF_COOKIE_HTTPONLY=True 対応：hidden input → cookie の順でトークン取得
  function csrf() {
    return getCSRFToken();
  }

  function esc(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  window.toggleRelatedSearch = function() {
    const area = document.getElementById('related-search-area');
    const icon = document.getElementById('related-toggle-icon');
    const open = area.style.display === 'none';
    area.style.display = open ? 'block' : 'none';
    icon.className = open ? 'bi bi-dash' : 'bi bi-plus';
    if (open) {
      document.getElementById('related-search-input').focus();
    } else {
      document.getElementById('related-search-results').innerHTML = '';
      document.getElementById('related-search-input').value = '';
    }
  };

  window.searchRelatedDiaries = function(q) {
    clearTimeout(searchTimer);
    const results = document.getElementById('related-search-results');
    if (!q.trim()) { results.innerHTML = ''; return; }
    searchTimer = setTimeout(async () => {
      try {
        const res = await fetch(BASE_URL + '?q=' + encodeURIComponent(q));
        const data = await res.json();
        if (!data.diaries || !data.diaries.length) {
          results.innerHTML = '<div class="related-result-item" style="cursor:default; color:var(--text-secondary);">該当なし</div>';
          return;
        }
        results.innerHTML = data.diaries.map(d => `
          <button type="button" class="related-result-item"
                  data-id="${d.id}"
                  data-name="${esc(d.stock_name)}"
                  data-symbol="${esc(d.stock_symbol||'')}"
                  data-excerpt="${esc(d.excerpt||'')}">
            <div class="d-flex align-items-center gap-1">
              ${d.stock_symbol ? `<span class="badge bg-secondary flex-shrink-0" style="font-size:0.75rem;">${esc(d.stock_symbol)}</span>` : ''}
              <span class="related-result-name">${esc(d.stock_name)}</span>
              ${d.first_purchase_date ? `<span class="related-result-meta">${esc(d.first_purchase_date)}</span>` : ''}
            </div>
            ${d.excerpt ? `<div class="related-result-excerpt">${esc(d.excerpt)}</div>` : ''}
          </button>
        `).join('');

        // クリックイベントをデリゲートで付与
        results.querySelectorAll('.related-result-item[data-id]').forEach(btn => {
          btn.addEventListener('click', () => {
            addRelatedDiary(
              parseInt(btn.dataset.id),
              btn.dataset.name,
              btn.dataset.symbol,
              btn.dataset.excerpt
            );
          });
        });
      } catch (e) {
        console.error('関連日記検索エラー:', e);
      }
    }, 280);
  };

  window.addRelatedDiary = async function(relatedId, name, symbol, excerpt) {
    try {
      const res = await fetch(ADD_URL, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ related_id: relatedId })
      });
      if (res.status === 403) {
        alert('認証エラー: ページを再読み込みしてください');
        return;
      }
      const data = await res.json();
      if (!data.success) { alert(data.error || '追加に失敗しました'); return; }

      // リストに追加（自動リンクで既存の場合は上書き）
      const list = document.getElementById('related-diaries-list');
      const noMsg = document.getElementById('no-related-msg');
      if (noMsg) noMsg.remove();

      const existing = document.getElementById(`related-item-${relatedId}`);
      const item = existing || document.createElement('div');
      item.className = 'related-unified-item';
      item.id = `related-item-${relatedId}`;
      item.innerHTML = `
        <a href="${window.DIARY_DETAIL_CONFIG.urls.detailUrlTemplate.replace('/0/', '/' + relatedId + '/')}" class="related-unified-main">
          <div class="related-unified-head">
            ${symbol ? `<span class="badge bg-secondary related-unified-code">${esc(symbol)}</span>` : ''}
            <span class="related-unified-name">${esc(name)}</span>
          </div>
          <div class="related-unified-vias">
            <span class="rs-via rs-via-manual">手動リンク</span>
          </div>
          ${excerpt ? `<div class="related-unified-excerpt">${esc(excerpt)}</div>` : ''}
        </a>
        <button type="button" class="btn btn-sm btn-link text-danger p-0 related-unified-remove"
                onclick="removeRelatedDiary(${relatedId})" title="手動リンクを解除">
          <i class="bi bi-x-circle"></i>
        </button>
      `;
      if (!existing) list.prepend(item);

      // 検索エリアをリセット
      document.getElementById('related-search-input').value = '';
      document.getElementById('related-search-results').innerHTML = '';
    } catch (e) {
      console.error('関連日記追加エラー:', e);
      alert('追加中にエラーが発生しました');
    }
  };

  window.removeRelatedDiary = async function(relatedId) {
    if (!confirm('関連付けを解除しますか？')) return;
    try {
      const removeUrl = window.DIARY_DETAIL_CONFIG.urls.relatedRemoveTemplate.replace('/0/', '/' + relatedId + '/');
      const res = await fetch(removeUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf() }
      });
      const data = await res.json();
      if (data.success) {
        const item = document.getElementById(`related-item-${relatedId}`);
        if (item) item.remove();
        const list = document.getElementById('related-diaries-list');
        if (!list.querySelector('[id^="related-item-"]')) {
          list.innerHTML = '<p class="text-muted mb-0 small" id="no-related-msg">関連日記はありません</p>';
        }
      }
    } catch (e) {
      console.error('関連日記削除エラー:', e);
    }
  };

})();

// ============================================
// EDINET連携: 開示書類メモ化
// ============================================
window.edinetPrefillNote = function(diaryId, docId) {
  fetch(window.DIARY_DETAIL_CONFIG.urls.edinetNotePrefill + '?doc_id=' + encodeURIComponent(docId), {
    headers: {'X-Requested-With': 'XMLHttpRequest'}
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.error) {
      console.warn('[EDINET] prefill error:', data.error);
      return;
    }
    // 継続記録フォームのEasyMDEに内容を流し込む
    var noteMDE = window.noteMDE;
    if (noteMDE) {
      noteMDE.value(data.content || '');
    } else {
      var ta = document.getElementById('id_content');
      if (ta) ta.value = data.content || '';
    }
    // note_type を earnings にセット（ピルUIも更新）
    var noteTypeEl = document.querySelector('[name="note_type"]');
    if (noteTypeEl && data.note_type) {
      noteTypeEl.value = data.note_type;
      document.querySelectorAll('[data-target="note_type"]').forEach(function(p) {
        p.classList.toggle('active', p.dataset.value === data.note_type);
      });
    }

    // 参照書類IDをセット
    var srcDocEl = document.getElementById('note_source_doc_id');
    if (srcDocEl) srcDocEl.value = data.doc_id || '';

    // 継続記録ボトムシートを開く（新規モードを保証。内容は維持）
    if (typeof window.setNoteFormAddMode === 'function') {
      window.setNoteFormAddMode();
    }
    if (typeof openBottomSheet === 'function') {
      openBottomSheet('addNoteSheet');
    }

    // トースト表示
    var toast = document.getElementById('edinet-prefill-toast');
    if (toast) {
      toast.style.display = '';
      setTimeout(function() { toast.style.display = 'none'; }, 4000);
    }
  })
  .catch(function(e) { console.error('[EDINET] fetch error:', e); });
};

// EDINET連携: XBRL 財務分析トリガー（AI 不使用）
// ============================================
window.edinetXBRLAnalyze = function(diaryId, docId, btn) {
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span>';
  }
  var csrf = document.querySelector('[name=csrfmiddlewaretoken]');
  var fd = new FormData();
  fd.append('doc_id', docId);
  if (csrf) fd.append('csrfmiddlewaretoken', csrf.value);

  fetch(window.DIARY_DETAIL_CONFIG.urls.edinetXbrlAnalyze, {
    method: 'POST',
    headers: {'X-Requested-With': 'XMLHttpRequest'},
    body: fd,
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.error) {
      alert('XBRL 分析エラー: ' + data.error);
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-bar-chart-steps"></i>'; }
      return;
    }
    // パネルを再ロードして財務指標を表示
    // htmx.ajax を直接呼ぶ（hx-trigger="once" の制限を回避）
    var panelUrl = window.DIARY_DETAIL_CONFIG.urls.edinetPanel;
    if (window.htmx) {
      htmx.ajax('GET', panelUrl, {target: '#edinet-panel-body', swap: 'innerHTML'});
    } else {
      fetch(panelUrl, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
        .then(function(r) { return r.text(); })
        .then(function(html) {
          var p = document.getElementById('edinet-panel-body');
          if (p) p.innerHTML = html;
        });
    }
  })
  .catch(function(e) {
    console.error('[EDINET] XBRL analyze error:', e);
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-bar-chart-steps"></i>'; }
  });
};

// 感情分析モーダル表示
function _escH(s) {
  return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : '';
}
window.showSentimentModal = function(el) {
  try {
    var data = JSON.parse(el.dataset.sent);
    var body = document.getElementById('sentimentModalBody');
    if (!body) return;
    var sl = data.sentiment_label || '';
    var scoreClass = sl === 'positive' ? 'text-success' : sl === 'negative' ? 'text-danger' : 'text-secondary';
    var badgeClass = sl === 'positive' ? 'bg-success' : sl === 'negative' ? 'bg-danger' : 'bg-secondary';
    // 語彙辞書ベースのスコア（-1〜+1）を-100〜+100に変換して表示
    var scoreNum = data.overall_score ? (data.overall_score * 100).toFixed(0) : '0';
    var html = '';

    // スコアサマリー
    html += '<div class="d-flex align-items-center gap-3 mb-3 pb-3 border-bottom">';
    html += '<div class="text-center"><div class="display-6 fw-bold ' + scoreClass + '">' + scoreNum + '</div>';
    html += '<div class="text-secondary" style="font-size:0.7rem;">スコア(-100〜+100)</div></div>';
    html += '<div><span class="badge fs-5 ' + badgeClass + '">' + _escH(data.label_display || '—') + '</span>';
    // 前回の有報・半報とのトーン変化（単発スコアより重要な情報として併記）
    var tt = data.tone_trend;
    if (tt && tt.label) {
      var ttClass = tt.label === '改善' ? 'text-success' : tt.label === '悪化' ? 'text-danger' : 'text-secondary';
      html += '<div class="small mt-1 ' + ttClass + '">前回比 ' + _escH(tt.label)
            + '（' + (tt.delta >= 0 ? '+' : '') + (tt.delta * 100).toFixed(0) + '）</div>';
    }
    html += '</div></div>';

    // ポジティブキーワード
    var kpos = data.keyword_pos || [];
    if (kpos.length > 0) {
      html += '<p class="mb-1 small fw-semibold">ポジティブキーワード</p>';
      html += '<div class="d-flex flex-wrap gap-1 mb-3">';
      kpos.forEach(function(k) { html += '<span class="badge bg-success-subtle text-success border border-success-subtle">' + _escH(k) + '</span>'; });
      html += '</div>';
    }

    // ネガティブキーワード
    var kneg = data.keyword_neg || [];
    if (kneg.length > 0) {
      html += '<p class="mb-1 small fw-semibold">ネガティブキーワード</p>';
      html += '<div class="d-flex flex-wrap gap-1 mb-3">';
      kneg.forEach(function(k) { html += '<span class="badge bg-danger-subtle text-danger border border-danger-subtle">' + _escH(k) + '</span>'; });
      html += '</div>';
    }

    // キーセンテンス
    var sp = (data.sample_sentences && data.sample_sentences.positive) || [];
    var sn = (data.sample_sentences && data.sample_sentences.negative) || [];
    if (sp.length > 0 || sn.length > 0) {
      html += '<p class="mb-2 small fw-semibold">キーセンテンス</p>';
      sp.slice(0,2).forEach(function(s) { html += '<p class="small text-success mb-1"><i class="bi bi-plus-circle-fill me-1"></i>' + _escH(s) + '</p>'; });
      sn.slice(0,2).forEach(function(s) { html += '<p class="small text-danger mb-1"><i class="bi bi-dash-circle-fill me-1"></i>' + _escH(s) + '</p>'; });
    }

    // 統計
    var st = data.stats || {};
    if (st.sentences_analyzed) {
      html += '<div class="text-secondary border-top pt-2 mt-2" style="font-size:0.7rem;">分析文数: ' + st.sentences_analyzed;
      if (st.positive_words_count !== undefined) html += ' | ポジ語: ' + st.positive_words_count + ' | ネガ語: ' + st.negative_words_count;
      html += '</div>';
    }

    body.innerHTML = html || '<p class="text-secondary text-center py-3">詳細データがありません</p>';
    bootstrap.Modal.getOrCreateInstance(document.getElementById('sentimentModal')).show();
  } catch(e) { console.error('[EDINET] showSentimentModal:', e); }
};

// 財務分析レポートモーダル表示
window.showReportModal = function(el) {
  try {
    var raw = el.dataset.report;
    if (!raw) return;
    var d = JSON.parse(raw);
    var symbol = el.dataset.symbol || '';
    var body = document.getElementById('reportModalBody');
    if (!body) return;

    function fmtCF(v) {
      if (v == null) return '—';
      var abs = Math.abs(v);
      var s = abs >= 1e8 ? (abs/1e8).toFixed(1) + '億円' : (abs/1e4).toFixed(0) + '万円';
      return (v >= 0 ? '+' : '−') + s;
    }
    function fmtPct(v, digits) {
      return v != null ? v.toFixed(digits != null ? digits : 1) + '%' : '—';
    }
    function indicatorRow(label, val, valStyle) {
      return '<div style="display:flex;justify-content:space-between;align-items:center;padding:11px 0;border-bottom:1px solid #f0f0f0;">'
        + '<span style="color:#666;font-size:14px;">' + label + '</span>'
        + '<span style="font-weight:600;font-size:15px;' + (valStyle||'') + '">' + val + '</span></div>';
    }

    var cf = d.cf || {};
    var isIdeal = d.operating_cf > 0 && d.investing_cf < 0 && d.financing_cf < 0;

    var html = '';

    // ---- カードヘッダー ----
    html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:18px 22px 14px;border-bottom:1px solid #e8e8e8;">';
    html += '<div style="font-size:14px;font-weight:600;display:flex;align-items:center;gap:7px;color:#444;">';
    html += '<i class="bi bi-bar-chart-fill" style="color:#6aa84f;"></i>財務分析レポート</div>';
    html += '<button type="button" data-bs-dismiss="modal" style="background:none;border:none;font-size:20px;color:#aaa;cursor:pointer;line-height:1;padding:0;">✕</button>';
    html += '</div>';

    // ---- 本文 ----
    html += '<div style="padding:22px 22px 24px;">';

    // 企業名・書類種別
    html += '<div style="font-size:18px;font-weight:700;margin:0 0 6px;letter-spacing:0.3px;">' + _escH(d.company_name || '') + '</div>';
    html += '<div style="font-size:13px;color:#666;margin-bottom:22px;">' + _escH(d.doc_type || '') + ' | ' + _escH(d.file_date || '') + '</div>';

    // キャッシュフロー
    html += '<div style="font-size:13px;font-weight:600;color:#555;display:flex;align-items:center;gap:5px;margin-bottom:14px;">';
    html += '<i class="bi bi-cash-stack"></i>キャッシュフロー</div>';
    html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;text-align:center;margin-bottom:14px;">';
    html += '<div><div style="font-size:12px;color:#888;margin-bottom:3px;">営業CF</div>'
          + '<div style="font-size:15px;font-weight:700;color:' + (d.operating_cf >= 0 ? '#2a8b57' : '#e05c5c') + ';">' + fmtCF(d.operating_cf) + '</div></div>';
    html += '<div><div style="font-size:12px;color:#888;margin-bottom:3px;">投資CF</div>'
          + '<div style="font-size:15px;font-weight:700;color:#777;">' + fmtCF(d.investing_cf) + '</div></div>';
    html += '<div><div style="font-size:12px;color:#888;margin-bottom:3px;">財務CF</div>'
          + '<div style="font-size:15px;font-weight:700;color:#777;">' + fmtCF(d.financing_cf) + '</div></div>';
    html += '</div>';

    // CF分類タグ（教科書的なパターン分類。リスク判定・強み/懸念は出さない）
    if (cf.name) {
      html += '<div style="display:flex;justify-content:center;gap:10px;margin-bottom:10px;">';
      html += '<span style="padding:5px 16px;border-radius:20px;font-size:12px;font-weight:600;'
            + (isIdeal ? 'background:#e6f4ea;color:#2a8b57;border:1px solid #cce8d6;' : 'background:#f1f3f4;color:#555;border:1px solid #e0e0e0;')
            + '">' + _escH(cf.name) + '</span>';
      html += '</div>';
    }

    // パターンの定義（機械的分類の説明）
    if (cf.description) {
      html += '<p style="font-size:12px;line-height:1.6;color:#888;text-align:center;margin-bottom:14px;">' + _escH(cf.description) + '</p>';
    }

    // 自己資本比率（財務安全性）
    if (d.equity_ratio != null) {
      var eqStyle = d.equity_ratio >= 50 ? 'color:#2a8b57;' : d.equity_ratio < 20 ? 'color:#e05c5c;' : '';
      html += '<div style="height:1px;background:#e8e8e8;margin:16px 0;"></div>';
      html += '<div style="font-size:13px;font-weight:600;color:#555;display:flex;align-items:center;gap:5px;margin-bottom:12px;">';
      html += '<i class="bi bi-shield-check"></i>財務安全性</div>';
      html += indicatorRow('自己資本比率', fmtPct(d.equity_ratio), eqStyle);
    }

    // ---- リアルタイム株価（非同期ロード） ----
    var liveId = 'reportLiveMetrics';
    if (symbol) {
      html += '<div style="height:1px;background:#e8e8e8;margin:16px 0;"></div>';
      html += '<div style="font-size:13px;font-weight:600;color:#555;display:flex;align-items:center;gap:5px;margin-bottom:12px;">';
      html += '<i class="bi bi-graph-up"></i>リアルタイム株価指標 <span style="font-size:11px;color:#999;font-weight:normal;">（取得日時点）</span></div>';
      html += '<div id="' + liveId + '" style="color:#888;font-size:13px;"><span class="spinner-border spinner-border-sm me-1"></span>取得中…</div>';
    }

    html += '</div>'; // end card-body

    body.innerHTML = html;
    bootstrap.Modal.getOrCreateInstance(document.getElementById('reportModal')).show();

    // リアルタイム株価を非同期取得
    if (symbol) {
      fetch(window.DIARY_DETAIL_CONFIG.urls.stockMetricsTemplate.replace('/0/', '/' + encodeURIComponent(symbol) + '/'))
        .then(function(r) { return r.json(); })
        .then(function(m) {
          var el2 = document.getElementById(liveId);
          if (!el2) return;
          if (!m.success) { el2.textContent = '取得失敗'; return; }
          var rows = '';
          if (m.price != null) rows += indicatorRow('株価', m.price.toLocaleString() + '円', '');
          if (m.per != null) rows += indicatorRow('PER（実績）', m.per.toFixed(1) + '倍', '');
          if (m.pbr != null) rows += indicatorRow('PBR', m.pbr.toFixed(2) + '倍', '');
          if (m.dividend_yield != null) rows += indicatorRow('配当利回り', m.dividend_yield.toFixed(2) + '%', 'color:#2a8b57;');
          if (m.market_cap_oku != null) rows += indicatorRow('時価総額', m.market_cap_oku.toLocaleString() + '億円', '');
          if (rows) {
            rows += '<div style="font-size:11px;color:#aaa;margin-top:10px;">取得: ' + _escH(m.fetched_at || '') + '（Yahoo Finance）</div>';
          }
          el2.innerHTML = rows || '取得できる指標がありませんでした';
        })
        .catch(function() {
          var el2 = document.getElementById(liveId);
          if (el2) el2.textContent = '株価情報の取得に失敗しました';
        });
    }
  } catch(e) { console.error('[EDINET] showReportModal:', e); }
};


// 仮説シートのプレビュー・タグ候補サジェストは _thesis_form_fields.html 側の
// 埋め込みスクリプトに一本化済み（#sheetThesisFields を自身のスコープとして初期化する）。


/* ============================================
   詳細ページ リッチアニメーション
   ============================================ */
(function () {
  'use strict';

  /* ① アクションバー スクロールシャドウ */
  function initActionBarScroll() {
    var actionBar = document.querySelector('.action-bar');
    if (!actionBar) return;
    var ticking = false;
    function onScroll() {
      if (!ticking) {
        requestAnimationFrame(function () {
          actionBar.classList.toggle('scrolled', window.scrollY > 8);
          ticking = false;
        });
        ticking = true;
      }
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  /* ② 損益ヒーローカード 数値カウントアップ */
  function animateCountUp(el, target, duration, prefix, suffix) {
    var start = performance.now();
    var from = 0;
    function step(now) {
      var progress = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      var current = Math.round(from + (target - from) * eased);
      el.textContent = prefix + current.toLocaleString('ja-JP') + suffix;
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function initProfitHeroCounter() {
    var profitEl = document.querySelector('.profit-hero-card .profit-value');
    if (!profitEl || profitEl.dataset.animated) return;
    profitEl.dataset.animated = '1';

    /* テキストから数値を抽出 */
    var raw = profitEl.textContent.replace(/[^\d\-+,]/g, '').replace(/,/g, '');
    var num = parseInt(raw, 10);
    if (isNaN(num)) return;

    var prefix = num >= 0 ? '+' : '';
    var currencyUnit = window.DIARY_DETAIL_CONFIG.currencyUnit;
    profitEl.textContent = prefix + '0' + currencyUnit;

    /* アニメーション開始を少し遅らせる */
    setTimeout(function () {
      animateCountUp(profitEl, num, 900, prefix, currencyUnit);
    }, 300);
  }

  /* ③ タブ切替時にコンテンツをスライドアニメーション */
  function initTabContentAnimation() {
    var tabLinks = document.querySelectorAll('.improved-tab-link');
    tabLinks.forEach(function (link) {
      link.addEventListener('click', function () {
        var targetId = this.getAttribute('href') || this.dataset.bsTarget;
        if (!targetId) return;
        var panel = document.querySelector(targetId);
        if (!panel) return;
        /* アニメーションをリセットして再実行 */
        panel.style.animation = 'none';
        void panel.offsetHeight;
        panel.style.animation = '';
      });
    });
  }

  /* ⑤ 価格詳細カード ホバー時パーティクル（ライト版） */
  function initPriceCardShine() {
    if (window.matchMedia('(hover: none)').matches) return;
    document.querySelectorAll('.price-detail-card').forEach(function (card) {
      card.addEventListener('mousemove', function (e) {
        var rect = card.getBoundingClientRect();
        var x = ((e.clientX - rect.left) / rect.width * 100).toFixed(1);
        var y = ((e.clientY - rect.top) / rect.height * 100).toFixed(1);
        card.style.background =
          'radial-gradient(circle at ' + x + '% ' + y + '%, ' +
          'rgba(113,196,239,0.12) 0%, var(--bg-200) 60%)';
      });
      card.addEventListener('mouseleave', function () {
        card.style.background = '';
      });
    });
  }

  /* ⑥ 通知アイテム 削除アニメーション（既存のdelete処理にフック） */
  function initNotifDeleteAnimation() {
    document.querySelectorAll('[data-delete-notification]').forEach(function (btn) {
      if (btn.dataset.richHooked) return;
      btn.dataset.richHooked = '1';
      btn.addEventListener('click', function () {
        var item = this.closest('.notification-item');
        if (!item) return;
        item.style.transition = 'transform 0.3s ease, opacity 0.3s ease, max-height 0.35s ease';
        item.style.transform = 'translateX(40px)';
        item.style.opacity = '0';
        item.style.maxHeight = item.offsetHeight + 'px';
        setTimeout(function () {
          item.style.maxHeight = '0';
          item.style.overflow = 'hidden';
          item.style.marginBottom = '0';
          item.style.paddingTop = '0';
          item.style.paddingBottom = '0';
        }, 280);
      });
    });
  }

  /* 初期化 */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  /* ⑥ サイドバー目次（概要タブ専用）: タブ切替で表示制御、クリックでスムーズスクロール、scrollspy */
  function initSidebarToc() {
    var toc = document.getElementById('overview-toc');
    if (!toc) return;

    /* タブ切替: 概要タブのときだけ表示 */
    function applyTocVisibility(targetId) {
      toc.style.display = (targetId === '#basic-content') ? 'block' : 'none';
    }
    var initialActive = document.querySelector('#diaryDetailTabs .improved-tab-link.active');
    applyTocVisibility(initialActive ? (initialActive.dataset.bsTarget || initialActive.getAttribute('href')) : '');
    document.querySelectorAll('#diaryDetailTabs .improved-tab-link').forEach(function (link) {
      link.addEventListener('shown.bs.tab', function (e) {
        var t = e.target.dataset.bsTarget || e.target.getAttribute('href');
        applyTocVisibility(t);
      });
    });

    /* クリック: スムーズスクロール（headerオフセット考慮） */
    var ACTION_BAR_OFFSET = 72;
    toc.querySelectorAll('.toc-link').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        var id = a.dataset.target;
        var target = id && document.getElementById(id);
        if (!target) return;
        var y = target.getBoundingClientRect().top + window.pageYOffset - ACTION_BAR_OFFSET;
        window.scrollTo({ top: y, behavior: 'smooth' });
      });
    });

    /* scrollspy: 表示中セクションを active 化 */
    var links = Array.from(toc.querySelectorAll('.toc-link'));
    var sections = links
      .map(function (a) { return document.getElementById(a.dataset.target); })
      .filter(Boolean);
    if (!sections.length || !('IntersectionObserver' in window)) return;

    var visible = new Map();
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) visible.set(en.target.id, en.intersectionRatio);
        else visible.delete(en.target.id);
      });
      if (!visible.size) return;
      var topId = null, topRatio = -1;
      visible.forEach(function (ratio, id) {
        if (ratio > topRatio) { topRatio = ratio; topId = id; }
      });
      links.forEach(function (a) {
        a.classList.toggle('active', a.dataset.target === topId);
      });
    }, { rootMargin: '-80px 0px -55% 0px', threshold: [0, 0.1, 0.5, 1] });
    sections.forEach(function (s) { observer.observe(s); });
  }

  function init() {
    initActionBarScroll();
    initProfitHeroCounter();
    initTabContentAnimation();
    initPriceCardShine();
    initNotifDeleteAnimation();
    initSidebarToc();
  }
})();


(function () {
  var _webArticleModal = null;

  function getWebArticleModal() {
    if (!_webArticleModal) {
      _webArticleModal = new bootstrap.Modal(document.getElementById('webArticleModal'));
    }
    return _webArticleModal;
  }

  function renderPreviewCard(data) {
    var html = '';

    // OG画像
    if (data.image) {
      html += '<div style="margin:-1rem -1rem 0.75rem;border-radius:0;overflow:hidden;max-height:180px;">'
            + '<img src="' + _esc(data.image) + '" alt="" loading="lazy"'
            + ' style="width:100%;height:180px;object-fit:cover;"'
            + ' onerror="this.parentElement.style.display=\'none\'">'
            + '</div>';
    }

    // タイトル
    if (data.title) {
      html += '<div class="fw-bold mb-1" style="font-size:0.95rem;line-height:1.4;">'
            + _esc(data.title)
            + '</div>';
    }

    // 説明文
    if (data.description) {
      html += '<div class="text-muted mb-2" style="font-size:0.8rem;line-height:1.5;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;">'
            + _esc(data.description)
            + '</div>';
    }

    // URL表示
    html += '<div class="text-muted text-truncate mb-3" style="font-size:0.75rem;">'
          + _esc(data.url)
          + '</div>';

    // ボタン
    html += '<a href="' + _esc(data.url) + '" target="_blank" rel="noopener noreferrer"'
          + ' class="btn btn-primary btn-sm w-100">'
          + '<i class="bi bi-box-arrow-up-right me-1"></i>記事を開く'
          + '</a>';

    return html;
  }

  function _esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function showWebArticleModal(url) {
    var body     = document.getElementById('webArticleModalBody');
    var siteName = document.getElementById('webArticleModalSiteName');

    siteName.textContent = '';
    body.innerHTML =
      '<div class="text-center py-4">'
      + '<div class="spinner-border spinner-border-sm text-primary" role="status"></div>'
      + '<p class="mt-2 text-muted small mb-0">記事情報を取得しています...</p>'
      + '</div>';

    getWebArticleModal().show();

    fetch(window.DIARY_DETAIL_CONFIG.urls.linkPreview + '?url=' + encodeURIComponent(url), {
      credentials: 'same-origin'
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        siteName.textContent = data.site_name || '';
        body.innerHTML = renderPreviewCard(data);
      })
      .catch(function () {
        // フォールバック：プレビュー取得失敗でも記事へのリンクは提供
        siteName.textContent = '';
        body.innerHTML =
          '<div class="text-center py-3 text-muted mb-3">'
          + '<i class="bi bi-link-45deg" style="font-size:1.5rem;"></i>'
          + '<p class="mt-1 small mb-0">プレビューを取得できませんでした</p>'
          + '</div>'
          + '<div class="text-muted text-truncate mb-3" style="font-size:0.75rem;">' + _esc(url) + '</div>'
          + '<a href="' + _esc(url) + '" target="_blank" rel="noopener noreferrer"'
          + ' class="btn btn-primary btn-sm w-100">'
          + '<i class="bi bi-box-arrow-up-right me-1"></i>記事を開く'
          + '</a>';
      });
  }

  function initWebArticleLinks() {
    document.addEventListener('click', function (e) {
      var link = e.target.closest('a');
      if (!link) return;

      // .markdown-content 内のリンクのみ対象
      if (!link.closest('.markdown-content')) return;

      var href = link.getAttribute('href');
      if (!href) return;

      // 外部URL（http / https）のみインターセプト
      if (!/^https?:\/\//i.test(href)) return;

      e.preventDefault();
      showWebArticleModal(href);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWebArticleLinks);
  } else {
    initWebArticleLinks();
  }
})();

// ============================================
// 継続記録フィルタ機能（テキスト検索）
// ============================================
let currentTextFilter = '';

window.filterNotesByText = function(text) {
  currentTextFilter = text.toLowerCase();
  applyNoteFilters();
  localStorage.setItem('noteTextFilter', text);
};

function applyNoteFilters() {
  // note-card-store を除外して、現在表示中のビュー内の note cards のみを対象
  const cardStore = document.getElementById('note-card-store');
  const noteCards = Array.from(document.querySelectorAll('.notes-list [data-note-type], #notes-view-topic [data-note-type]'))
    .filter(card => !cardStore || !cardStore.contains(card));

  let visibleCount = 0;
  const topicMatches = {};

  noteCards.forEach(card => {
    const content = (card.textContent || '').toLowerCase();
    const topic = card.dataset.noteTopic || '';

    const shouldShow = !currentTextFilter || content.includes(currentTextFilter);
    card.style.display = shouldShow ? '' : 'none';

    if (shouldShow) {
      visibleCount++;
      topicMatches[topic] = (topicMatches[topic] || 0) + 1;
    }
  });

  // テーマ別のマッチ数を更新
  document.querySelectorAll('.topic-index-row').forEach(row => {
    const topic = row.dataset.topic;
    const countSpan = row.querySelector('.topic-index-count');
    const totalCount = parseInt(countSpan.dataset.totalCount) || 0;
    const matchCount = topicMatches[topic] || 0;

    countSpan.dataset.matchCount = matchCount;

    // テキスト検索が適用されている場合のみマッチ数を表示
    const hasFilter = !!currentTextFilter;
    countSpan.textContent = hasFilter ? `${matchCount}/${totalCount}件` : `${totalCount}件`;

    // ヒット数が0の場合は薄くするとともに、クリック不可にする
    row.style.opacity = matchCount === 0 && hasFilter ? '0.5' : '1';
    row.style.pointerEvents = matchCount === 0 && hasFilter ? 'none' : 'auto';
    row.style.cursor = matchCount === 0 && hasFilter ? 'not-allowed' : 'pointer';
  });

  // フィルタ結果が空の場合のメッセージ表示（オプション）
  const emptyMsg = document.querySelector('.notes-empty-message');
  if (emptyMsg) {
    emptyMsg.style.display = visibleCount === 0 ? 'block' : 'none';
  }

  // 時系列ビュー表示中ならカード送りステッパーをカードに同期
  if (typeof window.rebuildTimelineStepper === 'function') {
    window.rebuildTimelineStepper();
  }
}

// ページ読込時にフィルタ状態を復元
(function restoreNoteFilters() {
  const savedTextFilter = localStorage.getItem('noteTextFilter') || '';
  currentTextFilter = savedTextFilter;

  const searchInput = document.getElementById('noteSearchInput');
  if (searchInput) {
    searchInput.value = savedTextFilter;
  }

  applyNoteFilters();
})();


function copyDiaryJson(btn) {
  var el = document.getElementById('diary-export-json');
  if (!el) return;
  // json_script は \uXXXX エスケープで出力するため、パース→再シリアライズで日本語を復元する
  var text = JSON.stringify(JSON.parse(el.textContent), null, 2);
  var originalHtml = btn.innerHTML;
  navigator.clipboard.writeText(text).then(function() {
    btn.innerHTML = '<i class="bi bi-check2 me-2"></i>コピーしました';
    setTimeout(function() { btn.innerHTML = originalHtml; }, 2000);
  }).catch(function() {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    btn.innerHTML = '<i class="bi bi-check2 me-2"></i>コピーしました';
    setTimeout(function() { btn.innerHTML = originalHtml; }, 2000);
  });
}

function downloadDiaryJson() {
  var el = document.getElementById('diary-export-json');
  if (!el) return;
  var data = JSON.parse(el.textContent);
  var text = JSON.stringify(data, null, 2);
  var blob = new Blob([text], { type: 'application/json' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  var namePart = (data.symbol || data.name || 'diary').toString().replace(/[\\/:*?"<>|]/g, '_');
  a.href = url;
  a.download = namePart + '_' + (data.exported_at || '') + '.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
