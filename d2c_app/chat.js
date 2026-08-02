// Chat interface for activity description extraction.
// Runs both as the embedded "AI Assistant" tab (index.html) and as the
// standalone /chat page. In tab mode it hands its result to the estimator
// in place; standalone it falls back to redirecting to "/".

let sessionId = null;
let isWaitingForResponse = false;
let chatReady = false;

const chatMessages = document.getElementById('chat-messages');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const statusMessage = document.getElementById('status-message');

const BOT_AVATAR_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3Z"/></svg>';

// Initialize chat session (idempotent — safe to call when a tab opens)
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

    userInput.disabled = false;
    sendBtn.disabled = false;
    userInput.focus();
  } catch (error) {
    console.error('Error initializing chat:', error);
    chatReady = false;
    showStatus('Failed to initialize chat. Please try again.', 'danger');
  }
}

// Expose for the shell so the tab can lazily boot the assistant.
window.initChatAssistant = initChat;

// Add bot message to chat, optionally with clickable answers beneath it.
function addBotMessage(message, showTyping = false, options = null, multiSelect = false) {
  const messageDiv = document.createElement('div');
  messageDiv.className = 'message bot-message';

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.innerHTML = BOT_AVATAR_SVG;

  const content = document.createElement('div');
  content.className = 'message-content';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';

  // Bubble first, so the options always land after it rather than depending on which
  // branch below ran.
  content.appendChild(bubble);
  const attachOptions = () => renderOptions(content, options, multiSelect);

  if (showTyping) {
    bubble.innerHTML = '<span class="typing-indicator"><span></span><span></span><span></span></span>';
    setTimeout(() => {
      bubble.innerHTML = formatMessage(message);
      attachOptions();
      scrollToBottom();
    }, 800);
  } else {
    bubble.innerHTML = formatMessage(message);
    attachOptions();
  }

  messageDiv.appendChild(avatar);
  messageDiv.appendChild(content);
  chatMessages.appendChild(messageDiv);
  scrollToBottom();
}

// Render the clickable answers for a question.
// Single-select behaves like a radio: one click answers and sends. Multi-select ticks
// like checkboxes and waits for the confirm button. Typing instead is always allowed —
// the chips are a shortcut, and answering by hand retires them (see lockOptionGroups).
function renderOptions(content, options, multiSelect) {
  if (!options || !options.length) return;

  const group = document.createElement('div');
  group.className = 'chat-options' + (multiSelect ? ' chat-options-multi' : '');

  if (!multiSelect) {
    options.forEach((label) => {
      const chip = makeChip(label);
      chip.addEventListener('click', () => {
        if (group.classList.contains('locked')) return;
        chip.classList.add('selected');
        submitAnswer(label, [label]);
      });
      group.appendChild(chip);
    });
    content.appendChild(group);
    return;
  }

  const chosen = [];
  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.className = 'chat-options-confirm';
  confirm.textContent = 'Send selected';
  confirm.disabled = true;

  options.forEach((label) => {
    const chip = makeChip(label);
    chip.addEventListener('click', () => {
      if (group.classList.contains('locked')) return;
      const at = chosen.indexOf(label);
      if (at === -1) {
        chosen.push(label);
        chip.classList.add('selected');
      } else {
        chosen.splice(at, 1);
        chip.classList.remove('selected');
      }
      confirm.disabled = chosen.length === 0;
    });
    group.appendChild(chip);
  });

  confirm.addEventListener('click', () => {
    if (!chosen.length || group.classList.contains('locked')) return;
    submitAnswer(chosen.join('; '), chosen.slice());
  });

  group.appendChild(confirm);
  content.appendChild(group);
}

function makeChip(label) {
  const chip = document.createElement('button');
  chip.type = 'button';
  chip.className = 'chat-option';
  chip.textContent = label;
  return chip;
}

// Retire every option group on screen. Called before each send so a question that has
// been answered — by chip or by typing — can't be answered a second time.
// `has-selection` lets the stylesheet drop the options that weren't taken; a group
// answered by typing keeps all of them visible, since none of them was the answer.
function lockOptionGroups() {
  chatMessages.querySelectorAll('.chat-options').forEach((group) => {
    group.classList.add('locked');
    if (group.querySelector('.chat-option.selected')) group.classList.add('has-selection');
    group.querySelectorAll('button').forEach((btn) => { btn.disabled = true; });
  });
}

// The chips are a shortcut, never the only route — say so in the box itself, so an
// analyst whose answer isn't on the list doesn't read the list as exhaustive.
function setInputAffordance(hasOptions) {
  if (!userInput) return;
  userInput.placeholder = hasOptions
    ? 'Pick an option above, or type your own answer…'
    : 'Type your message here...';
}

// Add user message to chat
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

// Format message (convert markdown-like syntax)
function formatMessage(message) {
  message = message.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  message = message.replace(/\*(.+?)\*/g, '<em>$1</em>');
  message = message.replace(/^• /gm, '&nbsp;&nbsp;• ');
  message = message.replace(/\n/g, '<br>');
  return message;
}

// Send whatever the user typed.
function sendMessage() {
  const message = userInput.value.trim();
  if (!message) return;
  userInput.value = '';
  userInput.style.height = 'auto';
  submitAnswer(message, []);
}

// Send one turn to the backend, from the text box or from the option chips.
// `selected` carries the clicked labels so the agent knows the answer was a choice
// rather than free prose; `message` is the same content as plain text.
async function submitAnswer(message, selected) {
  if (!message || isWaitingForResponse) return;

  lockOptionGroups();
  addUserMessage(message);

  isWaitingForResponse = true;
  userInput.disabled = true;
  sendBtn.disabled = true;

  try {
    const response = await fetch('/chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        message: message,
        selected_options: selected || []
      })
    });
    if (!response.ok) throw new Error('Failed to send message');

    const data = await response.json();
    addBotMessage(data.bot_message, true, data.options, data.multi_select);
    setInputAffordance(data.options && data.options.length);

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

// Show status message
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
// In the main app the shell calls initChatAssistant() when the tab is opened.
document.addEventListener('DOMContentLoaded', () => {
  if (chatMessages && !document.getElementById('estimation-panel')) {
    initChat();
  }
});
