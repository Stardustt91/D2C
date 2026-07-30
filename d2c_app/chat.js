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

// Add bot message to chat
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

  if (showTyping) {
    bubble.innerHTML = '<span class="typing-indicator"><span></span><span></span><span></span></span>';
    setTimeout(() => { bubble.innerHTML = formatMessage(message); }, 800);
  } else {
    bubble.innerHTML = formatMessage(message);
  }

  content.appendChild(bubble);
  messageDiv.appendChild(avatar);
  messageDiv.appendChild(content);
  chatMessages.appendChild(messageDiv);
  scrollToBottom();
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

// Send message to backend
async function sendMessage() {
  const message = userInput.value.trim();
  if (!message || isWaitingForResponse) return;

  addUserMessage(message);
  userInput.value = '';
  userInput.style.height = 'auto';

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
