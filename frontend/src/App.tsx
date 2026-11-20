import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowUp, Check, CircleHelp, Command, LoaderCircle, Plus, Sparkles } from "lucide-react";

type Metadata = {
  resolution_source: "LLM_CACHE" | "LIVE_INFERENCE";
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  key_id: string | null;
  fallback_attempts: number;
};

type Message = {
  id: string;
  role: "user" | "assistant" | "error";
  content: string;
  metadata?: Metadata;
};

type ChatResponse = {
  reply: string;
  metadata: Metadata;
};

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/+$/, "");

function createSessionId(): string {
  return `s-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}

function friendlyError(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (status === 503) return "The AI providers are unavailable right now. Your message was not saved—please try again shortly.";
  if (status === 422) return "Please check your message and try again.";
  return "We couldn't send that message. Please try again.";
}

export default function App() {
  const [sessionId] = useState(createSessionId);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [simulateFailover, setSimulateFailover] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [showTips, setShowTips] = useState(false);
  const transcriptRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isSending]);

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = input.trim();
    if (!text || isSending || text.length > 4000) return;

    setInput("");
    setIsSending(true);
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content: text }]);

    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (simulateFailover) headers["X-Simulate-Failure"] = "key_1=429";
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          session_id: sessionId,
          message: text,
          ...(systemPrompt.trim() ? { system_prompt: systemPrompt.trim() } : {}),
        }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
        throw new Error(friendlyError(response.status, detail));
      }
      const result = body as ChatResponse;
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "assistant", content: result.reply, metadata: result.metadata },
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Something went wrong. Please try again.";
      setMessages((current) => [...current, { id: crypto.randomUUID(), role: "error", content: message }]);
    } finally {
      setIsSending(false);
      setSimulateFailover(false);
    }
  }

  const hasMessages = messages.length > 0;

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Lyftr AI home">
          <span className="brand-mark"><Sparkles size={18} strokeWidth={2.2} /></span>
          <span>lyftr<span className="brand-light">.ai</span></span>
        </a>
        <button className="new-chat-button" onClick={() => window.location.reload()}>
          <Plus size={17} /> <span>New conversation</span> <kbd>⌘ K</kbd>
        </button>
        <div className="sidebar-divider" />
        <div className="sidebar-label">YOUR SESSION</div>
        <div className="session-card">
          <span className="session-dot" />
          <span className="session-copy">
            <strong>New conversation</strong>
            <small>{sessionId.slice(0, 14)}…</small>
          </span>
          <span className="live-indicator">LIVE</span>
        </div>
        <div className="sidebar-spacer" />
        <div className="sidebar-note">
          <span className="note-icon"><CircleHelp size={15} /></span>
          <span><strong>Built to keep going.</strong><br />Automatic key failover keeps your chat available.</span>
        </div>
        <div className="sidebar-footer">
          <div className="avatar">V</div>
          <div className="profile-copy"><strong>Guest session</strong><small>No account required</small></div>
          <button className="icon-button footer-help" aria-label="Show shortcuts" onClick={() => setShowTips(!showTips)}>
            <Command size={16} />
          </button>
        </div>
      </aside>

      <section className="chat-panel">
        <header className="topbar">
          <div className="mobile-brand"><span className="brand-mark"><Sparkles size={16} /></span> lyftr<span className="brand-light">.ai</span></div>
          <div className="model-pill"><span className="model-pulse" /> Gemini 2.0 Flash <span className="pill-divider" /> <span className="ready-text">Ready</span></div>
          <button className="icon-button tips-button" aria-label="About this chat" onClick={() => setShowTips(!showTips)}>
            <CircleHelp size={18} />
          </button>
        </header>

        <div className={`transcript ${hasMessages ? "has-messages" : ""}`} ref={transcriptRef}>
          {!hasMessages ? (
            <div className="welcome">
              <div className="welcome-eyebrow"><span className="eyebrow-line" /> YOUR THOUGHT PARTNER <span className="eyebrow-line" /></div>
              <div className="welcome-icon"><Sparkles size={25} strokeWidth={1.7} /></div>
              <h1>A little more <em>thoughtful.</em></h1>
              <p>Ask anything. Your conversation stays in context, while smart caching and automatic failover keep things quick and reliable.</p>
              <div className="suggestions">
                <button onClick={() => setInput("Help me plan a focused, productive week.")}>
                  <span className="suggestion-symbol lavender">✳</span><span><strong>Plan my week</strong><small>Make space for what matters</small></span><ArrowUp size={15} />
                </button>
                <button onClick={() => setInput("Explain a complex idea in simple terms.")}>
                  <span className="suggestion-symbol mint">↗</span><span><strong>Make it make sense</strong><small>Untangle a tricky topic</small></span><ArrowUp size={15} />
                </button>
                <button onClick={() => setInput("Help me brainstorm a fresh idea.")}>
                  <span className="suggestion-symbol peach">✦</span><span><strong>Find a fresh angle</strong><small>Start with a blank page</small></span><ArrowUp size={15} />
                </button>
              </div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => (
                <article key={message.id} className={`message-row ${message.role}`}>
                  {message.role !== "user" && <div className="assistant-avatar"><Sparkles size={15} /></div>}
                  <div className="message-content" data-testid={message.role === "assistant" ? "assistant-message" : undefined}>
                    <div className="message-author">{message.role === "user" ? "You" : message.role === "error" ? "Couldn't send" : "Lyftr"}</div>
                    <div className="message-text">{message.content}</div>
                    {message.metadata && (
                      <div className="metadata-strip">
                        <span className={`source-badge ${message.metadata.resolution_source === "LLM_CACHE" ? "cached" : ""}`} data-testid="meta-source">
                          <span />{message.metadata.resolution_source}
                        </span>
                        <span className="meta-item" data-testid="meta-model">{message.metadata.model}</span>
                        <span className="meta-item" data-testid="meta-latency">{message.metadata.latency_ms} ms</span>
                        <span className="meta-item token-meta"><span>IN</span><b data-testid="meta-input-tokens">{message.metadata.input_tokens}</b></span>
                        <span className="meta-item token-meta"><span>OUT</span><b data-testid="meta-output-tokens">{message.metadata.output_tokens}</b></span>
                        {message.metadata.fallback_attempts > 0 && <span className="fallback-note">↳ {message.metadata.fallback_attempts} fallback</span>}
                      </div>
                    )}
                  </div>
                </article>
              ))}
              {isSending && (
                <div className="message-row assistant pending-row">
                  <div className="assistant-avatar"><Sparkles size={15} /></div>
                  <div className="message-content"><div className="message-author">Lyftr</div><div className="typing"><LoaderCircle size={15} /> Thinking through it…</div></div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="composer-wrap">
          {showPrompt && (
            <label className="prompt-field">
              <span>SESSION INSTRUCTIONS <button className="text-button" onClick={() => { setSystemPrompt(""); setShowPrompt(false); }} type="button">Clear</button></span>
              <input
                data-testid="system-prompt-input"
                maxLength={4000}
                onChange={(event) => setSystemPrompt(event.target.value)}
                placeholder="e.g. Keep answers concise and practical"
                value={systemPrompt}
              />
            </label>
          )}
          {showTips && (
            <div className="tips-popover">
              <button className="close-tip" onClick={() => setShowTips(false)} aria-label="Close">×</button>
              <strong>Quick guide</strong>
              <p>Each page load starts a fresh session. Messages and reply metadata are saved for this session. Toggle failover below to test the backup key.</p>
            </div>
          )}
          <form className="composer" onSubmit={sendMessage}>
            <textarea
              aria-label="Message"
              data-testid="chat-input"
              maxLength={4000}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder="Ask anything…"
              rows={2}
              value={input}
            />
            <div className="composer-bottom">
              <div className="composer-tools">
                <button className={`tool-button ${showPrompt ? "active" : ""}`} onClick={() => setShowPrompt(!showPrompt)} type="button">
                  <Plus size={15} /> <span>Instructions</span>
                </button>
                <span className="tool-separator" />
                <label className="simulation-toggle" title="Requires failure simulation to be enabled on the backend">
                  <input checked={simulateFailover} onChange={(event) => setSimulateFailover(event.target.checked)} type="checkbox" />
                  <span className="toggle-track"><span /></span>
                  <span>Test failover</span>
                </label>
              </div>
              <div className="send-controls">
                <span className={`char-count ${input.length > 3800 ? "near-limit" : ""}`}>{input.length}/4000</span>
                <button className="send-button" data-testid="send-button" disabled={!input.trim() || isSending || input.trim().length > 4000} type="submit" aria-label="Send message">
                  {isSending ? <LoaderCircle size={17} className="spin" /> : <ArrowUp size={18} strokeWidth={2.3} />}
                </button>
              </div>
            </div>
          </form>
          <div className="composer-caption"><span><Check size={12} /> Your chats are saved to this session</span><span>AI can make mistakes. Check important info.</span></div>
        </div>
      </section>
    </main>
  );
}
