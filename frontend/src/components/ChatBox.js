"use client";

import { useState, useRef, useEffect, useCallback } from 'react';

// Every request goes through the Next relay (src/app/api/[...path]).
// No backend URL and no API key ever reaches the browser.
const API_BASE = '/api';
const MAX_UPLOAD_MB = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_MB || 25);
const HISTORY_LENGTH = 6;
const GREETING_ID = 1;

function formatSource(source) {
  if (typeof source === 'string') {
    return source.split('/').pop();
  }

  const name = (source.source || 'Unknown document').split('/').pop();
  const page = source.page ? ` p.${source.page}` : '';
  const score = source.score ? ` — ${Math.round(source.score * 100)}%` : '';
  return `${name}${page}${score}`;
}

function formatMetrics(metrics) {
  if (!metrics) return null;
  const parts = [];
  if (metrics.used_chunks !== undefined) {
    parts.push(`${metrics.used_chunks} excerpt${metrics.used_chunks > 1 ? 's' : ''}`);
  }
  if (metrics.retrieval_ms !== undefined) parts.push(`retrieval ${metrics.retrieval_ms} ms`);
  if (metrics.generation_ms !== undefined) {
    parts.push(`generation ${(metrics.generation_ms / 1000).toFixed(1)} s`);
  }
  return parts.length ? parts.join(' · ') : null;
}

async function readErrorDetail(response, fallback) {
  const detail = await response.json().catch(() => null);
  return detail?.detail || fallback;
}

export default function ChatBox() {
  const [messages, setMessages] = useState([
    {
      id: GREETING_ID,
      role: 'bot',
      content:
        'Hello. I am your secure clinical assistant. How can I help you with your protocols or medical documents today?',
      sources: [],
    },
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // A request still in flight after unmount would keep holding a server-side
  // generation slot for a screen that no longer exists.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Update the assistant message while it streams, or create it on the first event.
  const upsertBotMessage = useCallback((botId, update) => {
    setMessages((prev) => {
      const existing = prev.find((message) => message.id === botId);
      if (existing) {
        return prev.map((message) => (message.id === botId ? update(message) : message));
      }
      return [...prev, update({ id: botId, role: 'bot', content: '', sources: [] })];
    });
  }, []);

  const buildHistory = () =>
    messages
      .filter((message) => message.id !== GREETING_ID && message.content)
      .slice(-HISTORY_LENGTH)
      .map((message) => ({
        role: message.role === 'user' ? 'user' : 'assistant',
        content: message.content,
      }));

  const consumeStream = async (response, botId) => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split('\n\n');
      // The last fragment may be an incomplete event: replay it on the next
      // pass. `pop()` returns undefined on an empty array.
      buffer = blocks.pop() ?? '';

      for (const rawBlock of blocks) {
        const line = rawBlock.trim();
        if (!line.startsWith('data:')) continue;

        let event;
        try {
          event = JSON.parse(line.slice(5));
        } catch {
          console.warn('Skipping unreadable SSE event.');
          continue;
        }

        if (event.type === 'sources') {
          upsertBotMessage(botId, (message) => ({ ...message, sources: event.sources || [] }));
        } else if (event.type === 'token') {
          upsertBotMessage(botId, (message) => ({
            ...message,
            content: message.content + event.content,
          }));
        } else if (event.type === 'done') {
          upsertBotMessage(botId, (message) => ({ ...message, metrics: event.metrics }));
        } else if (event.type === 'error') {
          throw new Error(event.detail);
        }
      }
    }
  };

  const handleStop = () => abortRef.current?.abort();

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!input.trim() || isLoading) return;

    const history = buildHistory();
    const userMessage = { id: Date.now(), role: 'user', content: input.trim() };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

    const botId = userMessage.id + 1;
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(`${API_BASE}/query/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: userMessage.content, history }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(
          await readErrorDetail(response, 'Could not reach the server.')
        );
      }

      await consumeStream(response, botId);
    } catch (error) {
      if (error.name === 'AbortError') {
        upsertBotMessage(botId, (message) => ({
          ...message,
          content: message.content
            ? `${message.content}\n\n[Answer stopped.]`
            : 'Answer stopped.',
        }));
      } else {
        console.error(error);
        upsertBotMessage(botId, (message) => ({
          ...message,
          content: message.content
            ? `${message.content}\n\n[Answer interrupted: ${error.message}]`
            : `Sorry, the answer could not be produced: ${error.message}`,
        }));
      }
    } finally {
      abortRef.current = null;
      setIsLoading(false);
    }
  };

  const handleFileUpload = async (event) => {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file) return;

    if (file.type !== 'application/pdf') {
      window.alert('Only PDF files are accepted at the moment.');
      return;
    }

    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      window.alert(`PDF too large. Limit: ${MAX_UPLOAD_MB} MB.`);
      return;
    }

    const formData = new FormData();
    formData.append('file', file);
    setIsLoading(true);

    try {
      const response = await fetch(`${API_BASE}/upload`, { method: 'POST', body: formData });

      if (!response.ok) {
        throw new Error(await readErrorDetail(response, 'Upload failed.'));
      }

      const data = await response.json();
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now() + 1,
          role: 'bot',
          content: `Document indexed successfully. ${data.chunks_added} chunks added. You can now ask questions about it.`,
          sources: [],
        },
      ]);
    } catch (error) {
      console.error(error);
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now() + 1,
          role: 'bot',
          content: `Failed to index the document: ${error.message}`,
          sources: [],
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const lastMessage = messages[messages.length - 1];
  const showTypingIndicator = isLoading && (!lastMessage || lastMessage.role === 'user');

  return (
    <div className="chat-container glass-panel">
      <div className="header">
        <h1 className="header-title">MediBot RAG</h1>
        <div className="badge-hds">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
          HDS &amp; GDPR compliant
        </div>
      </div>

      <div className="messages-area">
        {messages.map((message) => {
          const metrics = message.role === 'bot' ? formatMetrics(message.metrics) : null;
          return (
            <div key={message.id} className={`message-wrapper ${message.role}`}>
              <div className={`message ${message.role}`}>
                {message.content}

                {message.role === 'bot' && message.sources?.length > 0 && (
                  <div className="message-sources">
                    <strong>Sources:</strong>
                    <br />
                    {message.sources.map((source, index) => (
                      <span key={index} className="source-badge">
                        {formatSource(source)}
                      </span>
                    ))}
                  </div>
                )}

                {metrics && <div className="message-metrics">{metrics}</div>}
              </div>
            </div>
          );
        })}

        {showTypingIndicator && (
          <div className="message-wrapper bot">
            <div className="message bot typing-indicator">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="input-container">
        <form onSubmit={handleSubmit} className="input-form">
          <label className="upload-btn" title="Upload a PDF protocol">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <input
              type="file"
              accept=".pdf"
              style={{ display: 'none' }}
              onChange={handleFileUpload}
              disabled={isLoading}
            />
          </label>
          <input
            type="text"
            className="chat-input"
            placeholder="Ask a clinical question, or a question about a protocol..."
            value={input}
            onChange={(event) => setInput(event.target.value)}
            disabled={isLoading}
          />
          {isLoading ? (
            <button
              type="button"
              className="send-button stop-button"
              onClick={handleStop}
              title="Stop the answer"
              aria-label="Stop the answer"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                <rect x="5" y="5" width="14" height="14" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              type="submit"
              className="send-button"
              disabled={!input.trim()}
              aria-label="Send the question"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
