import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import "./App.css";

const BASE = "http://localhost:8100"; // client agent ka base URL

// Empty state ke suggestion cards (click par sirf prompt box bharta hai)
const SUGGESTIONS = [
  "Plan a 3-day trip to Hunza",
  "Schedule a catch-up with Ali at 7:00 pm",
  "Birthday party ideas for my 5-year-old",
  "Outline a thank-you message for my teacher",
];

// Chhote inline icons (koi extra library nahi)
const Icon = {
  menu: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  ),
  send: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 19V5M5 12l7-7 7 7" />
    </svg>
  ),
  refresh: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5" />
    </svg>
  ),
  plus: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
};

export default function App() {
  const [prompt, setPrompt] = useState("");
  const [turns, setTurns] = useState([]); // har turn: { prompt, data, error }
  const [pending, setPending] = useState(""); // jo prompt abhi process ho raha hai
  const [roster, setRoster] = useState([]); // maujooda agents (Agent Cards se)
  const [rosterError, setRosterError] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 900);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  // Agents ki list aur online/offline status backend se laata hai
  async function loadRoster() {
    try {
      const res = await fetch(`${BASE}/agents`);
      if (!res.ok) throw new Error();
      setRoster(await res.json());
      setRosterError(false);
    } catch {
      setRosterError(true);
    }
  }

  // Page khulte hi agents ki list load hoti hai
  useEffect(() => {
    loadRoster();
  }, []);

  // Naya message ya loading aane par chat ke neeche scroll karta hai
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, pending]);

  // Textarea ki height text ke hisaab se barhti hai (max tak)
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }, [prompt]);

  // Prompt backend ko bhejta hai aur result ko turns mein jodta hai
  async function send() {
    const text = prompt.trim();
    if (!text || pending) return;
    setPrompt("");
    setPending(text);
    const turn = { prompt: text, data: null, error: "" };
    try {
      const res = await fetch(`${BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text }),
      });
      if (!res.ok) throw new Error(`Backend error ${res.status}`); // 404/500 par saaf error
      const json = await res.json();
      if (json.error) throw new Error(json.error); // backend ka error message
      turn.data = json;
    } catch (e) {
      turn.error = e.message;
    } finally {
      setTurns((t) => [...t, turn]);
      setPending("");
      loadRoster(); // har run ke baad status dobara check
    }
  }

  // Enter se send, Shift+Enter se nayi line
  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  // Suggestion card: prompt box bharta hai aur focus deta hai
  function useSuggestion(text) {
    setPrompt(text);
    inputRef.current?.focus();
  }

  // Chat screen saaf karta hai (sirf UI reset)
  function newChat() {
    if (pending) return;
    setTurns([]);
    setPrompt("");
  }

  // Mobile par sidebar ke bahar tap karne par band
  function closeIfMobile() {
    if (window.innerWidth <= 900) setSidebarOpen(false);
  }

  // Aakhri successful turn se pata chalta hai ke sidebar mein kaun "used" hai
  const latest = [...turns].reverse().find((t) => t.data)?.data;
  const usedKeys = new Set((latest?.agents ?? []).map((a) => a.agent));
  const online = roster.filter((a) => a.online).length;
  const isEmpty = turns.length === 0 && !pending;

  return (
    <div className="app">
      {/* Sidebar: maujooda agents */}
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <div className="side-head">
          <div>
            <div className="side-title">Agents</div>
            <div className="muted small">
              {roster.length ? `${online}/${roster.length} online` : "Checking..."}
            </div>
          </div>
          <button className="icon-btn" onClick={loadRoster} aria-label="Refresh agents" title="Refresh">
            {Icon.refresh}
          </button>
        </div>

        {rosterError && <div className="note-error small">Client agent not reachable</div>}

        <div className="agent-list">
          {roster.map((a) => (
            <div key={a.key} className="agent">
              <div className="agent-top">
                <span className="agent-name">
                  <span className={`dot ${a.online ? "on" : "off"}`} />
                  {a.name}
                </span>
                {usedKeys.has(a.key) && <span className="badge">used</span>}
              </div>
              <div className="agent-desc">{a.online ? a.description : "Offline"}</div>
              <div className="tags">
                {a.tags.map((t) => (
                  <span key={t} className="tag">{t}</span>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="side-foot muted small">Agent2Agent demo</div>
      </aside>

      {/* Mobile overlay */}
      {sidebarOpen && <div className="overlay" onClick={closeIfMobile} />}

      {/* Main chat area */}
      <div className="main">
        <header className="topbar">
          <button className="icon-btn" onClick={() => setSidebarOpen((o) => !o)} aria-label="Toggle agents">
            {Icon.menu}
          </button>
          <div className="brand">A2A Assistant</div>
          <button className="new-btn" onClick={newChat} disabled={!!pending}>
            {Icon.plus} <span>New chat</span>
          </button>
        </header>

        <div className="scroll">
          <div className="thread">
            {isEmpty && (
              <div className="hero">
                <div className="logo">A2</div>
                <h1>How can the agents help today?</h1>
                <p className="muted">
                  Ask for a trip, a meeting, birthday ideas or a message outline.
                  The client agent picks the right agents for you.
                </p>
                <div className="suggest">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="suggest-card" onClick={() => useSuggestion(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {turns.map((t, i) => (
              <div key={i} className="turn">
                <div className="user-row">
                  <div className="user-bubble">{t.prompt}</div>
                </div>

                <div className="bot-row">
                  <div className="avatar">A2</div>
                  <div className="bot-body">
                    {t.error && <div className="error-box">{t.error}</div>}

                    {t.data && (
                      <>
                        <div className="md">
                          <ReactMarkdown>{t.data.answer}</ReactMarkdown>
                        </div>

                        {/* Footer: summary + agent chips + details */}
                        <div className="meta">
                          <span className="summary">
                            {t.data.agents.length
                              ? `${t.data.agents.filter((a) => a.status === "completed").length}/${t.data.agents.length} agents completed`
                              : "No agent matched"}
                          </span>
                          {t.data.agents.map((a) => (
                            <span key={a.agent} className={`chip ${a.status}`}>
                              {a.name}
                            </span>
                          ))}
                        </div>

                        <details className="details">
                          <summary>Details</summary>
                          <h4>Selection</h4>
                          <pre>{JSON.stringify(t.data.debug?.selection, null, 2)}</pre>
                          <h4>Agent outputs</h4>
                          <pre>{JSON.stringify(t.data.agents, null, 2)}</pre>
                        </details>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))}

            {pending && (
              <div className="turn">
                <div className="user-row">
                  <div className="user-bubble">{pending}</div>
                </div>
                <div className="bot-row">
                  <div className="avatar">A2</div>
                  <div className="bot-body">
                    <div className="typing" aria-label="Agents are working">
                      <span /><span /><span />
                    </div>
                    <div className="muted small">Agents are working...</div>
                  </div>
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>
        </div>

        {/* Composer */}
        <div className="composer-wrap">
          <div className="composer">
            <textarea
              ref={inputRef}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask anything: trips, meetings, birthdays, greetings..."
              aria-label="Prompt"
              rows={1}
            />
            <button className="send" onClick={send} disabled={!!pending || !prompt.trim()} aria-label="Send">
              {Icon.send}
            </button>
          </div>
          <div className="hint muted small">Enter to send, Shift+Enter for a new line</div>
        </div>
      </div>
    </div>
  );
}