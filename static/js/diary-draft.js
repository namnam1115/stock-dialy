/**
 * diary-draft.js — 長文入力の下書き自動保存（localStorage）
 *
 * 対象: `data-draft-key` 属性を持つ textarea（投資理由・継続記録の本文）。
 * セッション切れ・誤リロード・誤タブクローズで書きかけの長文が消える事故を防ぐ。
 *
 * 仕組み:
 * - 3秒間隔で本文を localStorage に保存（変化があったときのみ）
 * - ページ表示時に未送信の下書きがあれば「復元/破棄」バナーを textarea 直前に表示
 * - フォーム送信時に下書きを削除（送信後の再訪で古い下書きが出ないように）
 * - EasyMDE(CodeMirror) でラップされた textarea は、CodeMirror が自身の DOM 要素に
 *   公開するインスタンス（el.CodeMirror）経由で読み書きする（textarea は送信時まで
 *   同期されないため）
 */
(function () {
  'use strict';

  var PREFIX = 'kabulog:draft:';
  var SAVE_INTERVAL_MS = 3000;

  function storageKey(ta) {
    return PREFIX + ta.getAttribute('data-draft-key');
  }

  /** EasyMDE 化されていればその値、そうでなければ textarea の値 */
  function readValue(ta) {
    var scope = ta.parentElement || document;
    var cmEl = scope.querySelector('.CodeMirror');
    if (cmEl && cmEl.CodeMirror) return cmEl.CodeMirror.getValue();
    return ta.value;
  }

  function writeValue(ta, value) {
    var scope = ta.parentElement || document;
    var cmEl = scope.querySelector('.CodeMirror');
    if (cmEl && cmEl.CodeMirror) {
      cmEl.CodeMirror.setValue(value);
    }
    ta.value = value;
  }

  function loadDraft(key) {
    try {
      var raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function saveDraft(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify({ v: value, t: Date.now() }));
    } catch (e) { /* 容量超過等は握りつぶす（本体機能に影響させない） */ }
  }

  function clearDraft(key) {
    try { localStorage.removeItem(key); } catch (e) {}
  }

  function formatTime(ts) {
    var d = new Date(ts);
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  /** 「下書きがあります」バナーを textarea（または EasyMDE コンテナ）の直前に挿入 */
  function showRestoreBanner(ta, draft, key) {
    var banner = document.createElement('div');
    banner.className = 'alert alert-warning d-flex align-items-center gap-2 py-2 px-3 my-2 small';
    banner.setAttribute('role', 'alert');
    banner.innerHTML =
      '<i class="bi bi-file-earmark-text"></i>' +
      '<span class="flex-grow-1">' + formatTime(draft.t) + ' の未送信の下書きがあります</span>' +
      '<button type="button" class="btn btn-sm btn-warning draft-restore">復元する</button>' +
      '<button type="button" class="btn btn-sm btn-outline-secondary draft-discard">破棄</button>';

    var anchor = ta.parentElement || ta;
    anchor.insertBefore(banner, anchor.firstChild);

    banner.querySelector('.draft-restore').addEventListener('click', function () {
      writeValue(ta, draft.v);
      banner.remove();
    });
    banner.querySelector('.draft-discard').addEventListener('click', function () {
      clearDraft(key);
      banner.remove();
    });
  }

  function initField(ta) {
    var key = storageKey(ta);
    var draft = loadDraft(key);
    var initial = readValue(ta);

    // 未送信の下書きが「現在の内容と異なる」場合のみ復元を提案する
    // （編集ページでは保存済み本文が初期値に入っているため、同一なら出さない）
    if (draft && draft.v && draft.v.trim() && draft.v !== initial) {
      showRestoreBanner(ta, draft, key);
    }

    var lastSaved = draft ? draft.v : initial;
    setInterval(function () {
      var val = readValue(ta);
      // 空文字での上書き保存はしない（誤クリア直後にリロードで戻せるように）
      if (val !== lastSaved && val.trim()) {
        saveDraft(key, val);
        lastSaved = val;
      }
    }, SAVE_INTERVAL_MS);

    var form = ta.closest('form');
    if (form) {
      form.addEventListener('submit', function (e) {
        // 他のリスナーが preventDefault した（＝クライアント側バリデーションで
        // 送信中止になった）場合は下書きを残す。全リスナー実行後に判定するため defer。
        setTimeout(function () {
          if (!e.defaultPrevented) clearDraft(key);
        }, 0);
      });
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('textarea[data-draft-key]').forEach(initField);
  });
})();
