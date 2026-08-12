// Chat interface for the activity-intake assistant.
// Runs both as the embedded "AI Assistant" window (index.html) and as the standalone
// /chat page. In window mode it hands its result to the estimator in place; standalone
// it falls back to redirecting to "/".
//
// The conversation is plain free text — the assistant asks one question at a time and
// the analyst answers however they like. What it has understood shows up in the facts
// drawer on the left, redrawn from every response, so the panel needs no state of its
// own and can never drift from the server's.

let sessionId = null;
let isWaitingForResponse = false;
let chatReady = false;
// The drawer opens itself once, the first time a fact lands in it. A panel nobody has
// been shown is a panel nobody knows to open; after that, it is the analyst's to control.
let factsRevealed = false;
let lastFactKeys = [];

const chatMessages = document.getElementById('chat-messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const statusMessage = document.getElementById('status-message');

const factsRail = document.getElementById('facts-rail');
const factsHandle = document.getElementById('facts-handle');
const factsBody = document.getElementById('facts-body');
const factsStep = document.getElementById('facts-step');
const factsCount = document.getElementById('facts-count');
const assistantWindow = document.getElementById('assistant-window');

const BOT_AVATAR_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3Z"/></svg>';

// Initialize chat session (idempotent — safe to call when the window opens)
async function initChat() {
  if (chatReady || !chatMessages) return;
  chatReady = true;
  try {
    const response = await fetch('/chat/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    if (!response.ok) throw new Error('Failed to initialize chat');

    const data = await response.json();
    sessionId = data.session_id;

    chatMessages.innerHTML = '';
    addBotMessage(data.bot_message);
    renderFacts(data);

    userInput.disabled = false;
    sendBtn.disabled = false;
    userInput.focus();
  } catch (error) {
    console.error('Error initializing chat:', error);
    chatReady = false;
    showStatus('Failed to initialize chat. Please try again.', 'danger');
  }
}

// Expose for the shell so the window can lazily boot the assistant.
window.initChatAssistant = initChat;

/* ------------------------------ Facts drawer ------------------------------ */

function setFactsOpen(open) {
  if (!factsRail) return;
  factsRail.classList.toggle('open', open);
  if (factsHandle) factsHandle.setAttribute('aria-expanded', String(open));
  // The floating window widens to fit the drawer; the standalone page has no such frame.
  if (assistantWindow) assistantWindow.classList.toggle('facts-open', open);
}

function prettifyKey(key) {
  const words = String(key).replace(/[_-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// Redraw the panel from a /chat response. Facts, the step line and the count all come
// from the same payload, so nothing here can disagree with the server.
function renderFacts(data) {
  if (!factsBody) return;

  const facts = data.facts || {};
  const keys = Object.keys(facts);
  const pending = data.pending || [];

  if (factsStep) {
    const total = data.total_steps || 8;
    const step = data.current_step || 1;
    const name = data.step_name || '';
    factsStep.textContent = 'Step ' + step + ' of ' + total + (name ? ' · ' + name : '');
  }

  if (factsCount) {
    factsCount.textContent = String(keys.length);
    factsCount.hidden = keys.length === 0;
  }

  factsBody.innerHTML = '';

  if (!keys.length) {
    const empty = document.createElement('p');
    empty.className = 'facts-empty';
    empty.textContent = 'Nothing captured yet. Everything you confirm appears here.';
    factsBody.appendChild(empty);
    lastFactKeys = [];
    return;
  }

  const table = document.createElement('table');
  table.className = 'facts-table';
  const tbody = document.createElement('tbody');

  keys.forEach((key) => {
    const row = document.createElement('tr');
    if (!lastFactKeys.includes(key)) row.className = 'facts-row-new';

    const label = document.createElement('th');
    label.textContent = prettifyKey(key);

    const value = document.createElement('td');
    // textContent, not innerHTML: these strings are model output routed straight back
    // into the page.
    value.textContent = facts[key];

    row.appendChild(label);
    row.appendChild(value);
    tbody.appendChild(row);
  });

  table.appendChild(tbody);
  factsBody.appendChild(table);

  if (pending.length) {
    const block = document.createElement('div');
    block.className = 'facts-pending';

    const title = document.createElement('div');
    title.className = 'facts-pending-title';
    title.textContent = 'To be derived';
    block.appendChild(title);

    const list = document.createElement('ul');
    pending.forEach((item) => {
      const li = document.createElement('li');
      li.textContent = prettifyKey(item);
      list.appendChild(li);
    });
    block.appendChild(list);
    factsBody.appendChild(block);
  }

  lastFactKeys = keys;

  if (!factsRevealed) {
    factsRevealed = true;
    setFactsOpen(true);
  }
}

if (factsHandle) {
  factsHandle.addEventListener('click', () => {
    setFactsOpen(!factsRail.classList.contains('open'));
    // A manual click settles it either way: no auto-open should override the analyst.
    factsRevealed = true;
  });
}

/* ------------------------------ Messages ------------------------------ */

function addBotMessage(message, showTyping = false) {
  const messageDiv = document.createElement('div');
  messageDiv.className = 'message bot-message';

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.innerHTML = BOT_AVATAR_SVG;

  const content = document.createElement('div');
  content.className = 'message-content';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  content.appendChild(bubble);

  const paint = () => {
    bubble.innerHTML = formatMessage(message);
    // A message carrying a table needs more of the column than a sentence does.
    if (bubble.querySelector('table')) messageDiv.classList.add('has-table');
  };

  if (showTyping) {
    bubble.innerHTML = '<span class="typing-indicator"><span></span><span></span><span></span></span>';
    setTimeout(() => {
      paint();
      scrollToBottom();
    }, 500);
  } else {
    paint();
  }

  messageDiv.appendChild(avatar);
  messageDiv.appendChild(content);
  chatMessages.appendChild(messageDiv);
  scrollToBottom();
}

function addUserMessage(message) {
  const messageDiv = document.createElement('div');
  messageDiv.className = 'message user-message';

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = 'You';

  const content = document.createElement('div');
  content.className = 'message-content';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  bubble.textContent = message;

  content.appendChild(bubble);
  messageDiv.appendChild(avatar);
  messageDiv.appendChild(content);
  chatMessages.appendChild(messageDiv);
  scrollToBottom();
}

// The assistant answers in markdown-ish text and lays interim results out as tables;
// pipe tables are rendered rather than left as raw pipes.
function formatMessage(message) {
  const blocks = String(message).split(/\n{2,}/);
  return blocks.map((block) => {
    const table = renderPipeTable(block);
    return table !== null ? table : inlineFormat(block).replace(/\n/g, '<br>');
  }).join('<br><br>');
}

function inlineFormat(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^[-•] /gm, '&nbsp;&nbsp;• ');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// A markdown pipe table, or null when the block isn't one. The separator row (|---|---|)
// is what distinguishes a table from prose that happens to contain a pipe.
function renderPipeTable(block) {
  const lines = block.split('\n').map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return null;
  if (!lines.every((l) => l.startsWith('|') && l.endsWith('|'))) return null;
  if (!/^\|[\s:|-]+\|$/.test(lines[1])) return null;

  const cells = (line) => line.slice(1, -1).split('|').map((c) => c.trim());
  const head = cells(lines[0]);
  const rows = lines.slice(2).map(cells);

  const thead = '<tr>' + head.map((c) => '<th>' + inlineFormat(c) + '</th>').join('') + '</tr>';
  const tbody = rows.map(
    (row) => '<tr>' + row.map((c) => '<td>' + inlineFormat(c) + '</td>').join('') + '</tr>'
  ).join('');

  return '<div class="chat-table-wrap"><table class="chat-table">' + thead + tbody + '</table></div>';
}

/* ------------------------------ Sending ------------------------------ */

function sendMessage() {
  const message = userInput.value.trim();
  if (!message) return;
  userInput.value = '';
  userInput.style.height = 'auto';
  submitAnswer(message);
}

async function submitAnswer(message) {
  if (!message || isWaitingForResponse) return;

  addUserMessage(message);

  isWaitingForResponse = true;
  userInput.disabled = true;
  sendBtn.disabled = true;

  try {
    const response = await fetch('/chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: message })
    });
    if (!response.ok) throw new Error('Failed to send message');

    const data = await response.json();
    addBotMessage(data.bot_message, true);
    renderFacts(data);

    if (data.done && data.activity_description) {
      const facts = data.activity_facts || null;

      if (typeof window.loadDescriptionIntoEstimation === 'function') {
        // Floating-window mode — hand the draft to the estimator, then close.
        showStatus('Description ready — added to the estimator.', 'success');
        setTimeout(() => {
          window.loadDescriptionIntoEstimation(data.activity_description, facts);
          if (typeof window.closeAssistant === 'function') window.closeAssistant();
        }, 900);
      } else {
        // Standalone /chat page — fall back to redirect via localStorage.
        localStorage.setItem('activity_description', data.activity_description);
        if (facts) localStorage.setItem('activity_facts', JSON.stringify(facts));
        showStatus('Redirecting to cost estimation…', 'success');
        setTimeout(() => { window.location.href = '/'; }, 1500);
      }
    } else {
      isWaitingForResponse = false;
      userInput.disabled = false;
      sendBtn.disabled = false;
      userInput.focus();
    }
  } catch (error) {
    console.error('Error sending message:', error);
    addBotMessage('Sorry, something went wrong. Please try again.');
    isWaitingForResponse = false;
    userInput.disabled = false;
    sendBtn.disabled = false;
    userInput.focus();
  }
}

function showStatus(message, type = 'info') {
  if (!statusMessage) return;
  statusMessage.textContent = message;
  statusMessage.className = 'chat-status alert-' + type;
  statusMessage.style.display = 'block';
  if (type === 'success') {
    setTimeout(() => { statusMessage.style.display = 'none'; }, 3000);
  }
}

function scrollToBottom() {
  if (chatMessages) chatMessages.scrollTop = chatMessages.scrollHeight;
}

// Event listeners
if (sendBtn) sendBtn.addEventListener('click', sendMessage);
if (userInput) {
  userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  userInput.addEventListener('input', () => {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 140) + 'px';
  });
}

// Standalone /chat page: no estimation panel present, so boot immediately.
// In the main app the shell calls initChatAssistant() when the window is opened.
document.addEventListener('DOMContentLoaded', () => {
  if (chatMessages && !document.getElementById('estimation-panel')) {
    initChat();
  }
});
