/**
 * Chat module - handles chat functionality
 */

import { apiFetch, consumeSSE } from '../api.js';
import { appStore } from '../state.js';
import { bus, EVENTS } from '../event-bus.js';
import { icon } from '../icons.js';
import { escHtml } from '../common.js';
import { formatContent } from './message.js';

// ── Chat State ───────────────────────────────────────────────────────────────

let isProcessing = false;
let streamingMessage = null;

// ── DOM Elements ─────────────────────────────────────────────────────────────

let chatHistory = null;
let userInput = null;
let sendBtn = null;

// ── Initialization ───────────────────────────────────────────────────────────

/**
 * Initialize chat module
 */
export function init() {
    chatHistory = document.getElementById('chatHistory');
    userInput = document.getElementById('userInput');
    sendBtn = document.getElementById('sendBtn');

    setupEventListeners();
}

function setupEventListeners() {
    // Send button
    if (sendBtn) {
        sendBtn.addEventListener('click', sendMessage);
    }

    // Enter key
    if (userInput) {
        userInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    }

    // New chat button
    const newChatBtn = document.getElementById('newChatBtn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', startNewChat);
    }

    // History button
    const historyBtn = document.getElementById('historyBtn');
    if (historyBtn) {
        historyBtn.addEventListener('click', loadChatHistory);
    }

    // Hint cards
    document.querySelectorAll('.hint-card').forEach((card) => {
        card.addEventListener('click', () => {
            const hint = card.dataset.hint;
            if (hint) {
                userInput.value = hint;
                sendMessage();
            }
        });
    });
}

// ── Chat Actions ─────────────────────────────────────────────────────────────

/**
 * Send a message
 */
export async function sendMessage() {
    const message = userInput.value.trim();
    if (!message || isProcessing) return;

    isProcessing = true;
    userInput.value = '';
    sendBtn.disabled = true;

    // Add user message
    addMessage(message, 'user');

    // Show typing indicator
    showTypingIndicator();

    try {
        const sessionId = appStore.get('sessionId');
        const formData = new FormData();
        formData.append('message', message);
        if (sessionId) formData.append('session_id', sessionId);

        const resp = await apiFetch('/api/chat', {
            method: 'POST',
            body: formData,
        });

        let finalData = null;

        await consumeSSE(resp, {
            onProgress: (text) => {
                updateStreamingMessage(text);
            },
            onResult: (data) => {
                finalData = data;
            },
            onError: (error) => {
                removeStreamingMessage();
                addMessage(error, 'ai');
            },
        });

        // Handle result
        if (finalData) {
            removeStreamingMessage();
            addMessage(finalData.reply, 'ai');

            if (finalData.session_id) {
                appStore.set('sessionId', finalData.session_id);
            }

            // Handle view switching
            if (finalData.view) {
                handleViewResponse(finalData);
            }

            // Handle A2UI response
            if (finalData.a2ui) {
                bus.emit(EVENTS.VIEW_SWITCH, { a2ui: finalData.a2ui });
            }
        }
    } catch (err) {
        removeStreamingMessage();
        const errMsg = typeof err?.message === 'string' ? err.message : (typeof err === 'string' ? err : '未知错误');
        addMessage('发送失败：' + errMsg, 'ai');
    } finally {
        isProcessing = false;
        sendBtn.disabled = false;
        removeTypingIndicator();
    }
}

/**
 * Start a new chat
 */
export function startNewChat() {
    appStore.set('sessionId', null);
    if (chatHistory) {
        chatHistory.innerHTML = '';
    }
}

/**
 * Load chat history
 */
export async function loadChatHistory() {
    try {
        const resp = await apiFetch('/api/my-chat-history?limit=50');
        const data = await resp.json();

        if (data.messages && data.messages.length > 0) {
            appStore.set('sessionId', data.session_id);
            chatHistory.innerHTML = '';

            for (const msg of data.messages) {
                if (msg.role === 'user') {
                    addMessage(msg.content, 'user');
                } else if (msg.role === 'assistant') {
                    const meta = msg.metadata || {};
                    if (!meta.pending_action) {
                        addMessage(msg.content, 'ai');
                    }
                }
            }
        }
    } catch (err) {
        console.error('Failed to load chat history:', err);
    }
}

// ── Message Rendering ────────────────────────────────────────────────────────

/**
 * Add a message to chat history
 * @param {string} content - Message content
 * @param {string} type - Message type ('user' or 'ai')
 */
export function addMessage(content, type) {
    if (!chatHistory) return;

    // Ensure content is a string
    if (typeof content !== 'string') {
        content = String(content);
    }

    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${type}-message`;

    const avatarSvg = type === 'ai' ? icon('flame', 20) : icon('user', 20);

    msgDiv.innerHTML = `
        <div class="avatar">${avatarSvg}</div>
        <div class="content">${formatContent(content)}</div>
    `;

    chatHistory.appendChild(msgDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

/**
 * Show typing indicator
 */
function showTypingIndicator() {
    if (!chatHistory) return;

    const typingDiv = document.createElement('div');
    typingDiv.className = 'message ai-message typing-indicator';
    typingDiv.id = 'typingIndicator';
    typingDiv.innerHTML = `
        <div class="avatar">${icon('flame', 20)}</div>
        <div class="content">
            <div class="typing-dots">
                <span></span>
                <span></span>
                <span></span>
            </div>
        </div>
    `;

    chatHistory.appendChild(typingDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

/**
 * Remove typing indicator
 */
function removeTypingIndicator() {
    const typingDiv = document.getElementById('typingIndicator');
    if (typingDiv) {
        typingDiv.remove();
    }
}

/**
 * Update streaming message
 * @param {string} text - Streaming text
 */
function updateStreamingMessage(text) {
    if (!chatHistory) return;

    if (!streamingMessage) {
        streamingMessage = document.createElement('div');
        streamingMessage.className = 'message ai-message streaming-message';
        streamingMessage.innerHTML = `
            <div class="avatar">${icon('flame', 20)}</div>
            <div class="content">
                <span class="streaming-text"></span>
                <span class="streaming-cursor">|</span>
            </div>
        `;
        chatHistory.appendChild(streamingMessage);
    }

    const textEl = streamingMessage.querySelector('.streaming-text');
    if (textEl) {
        textEl.textContent = text;
    }

    chatHistory.scrollTop = chatHistory.scrollHeight;
}

/**
 * Remove streaming message
 */
function removeStreamingMessage() {
    if (streamingMessage) {
        streamingMessage.remove();
        streamingMessage = null;
    }
}

// ── View Response Handling ───────────────────────────────────────────────────

function handleViewResponse(data) {
    const { view, view_data, rules, rule_type, rule_mgmt, voucher } = data;

    switch (view) {
        case 'voucher':
            if (voucher) {
                bus.emit(EVENTS.VOUCHER_LOAD, { voucher });
            }
            break;

        case 'voucher_list':
            if (view_data) {
                bus.emit(EVENTS.VIEW_SWITCH, {
                    view: 'voucher_list',
                    data: view_data,
                });
            }
            break;

        case 'rules':
            bus.emit(EVENTS.VIEW_SWITCH, {
                view: 'rules',
                data: { rules, rule_type, rule_mgmt },
            });
            break;

        case 'user_list':
            if (view_data) {
                bus.emit(EVENTS.VIEW_SWITCH, {
                    view: 'user_list',
                    data: view_data,
                });
            }
            break;
    }
}

// ── Export State ─────────────────────────────────────────────────────────────

export function getIsProcessing() {
    return isProcessing;
}
