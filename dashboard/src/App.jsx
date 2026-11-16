import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import { createIamClient } from "./iamClient.js";
import "./App.css";

const BASE = "http://localhost:8100"; // client agent ka base URL
const AUTH = "http://localhost:9000";
const IAM_ENABLED = import.meta.env.VITE_IAM_ENABLED === "true";

// Decode the role claim locally for display and role-gated controls only.
function decodeRole(token) {
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(payload)).role || "user";
  } catch {
    return "user";
  }
}

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
  // Keep credentials and tokens only in component memory; reloads require login.
  const [session, setSession] = useState(null);
  const [loginName, setLoginName] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [auditOpen, setAuditOpen] = useState(false);
  const [auditRows, setAuditRows] = useState([]);
  const [auditFilters, setAuditFilters] = useState({ user: "", agent: "", decision: "", limit: "100" });
  const [prompt, setPrompt] = useState("");
  const [turns, setTurns] = useState([]); // har turn: { prompt, data, error }
  const [pending, setPending] = useState(""); // jo prompt abhi process ho raha hai
  const [roster, setRoster] = useState([]); // maujooda agents (Agent Cards se)
  const [rosterError, setRosterError] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 900);
  const endRef = useRef(null);
  const inputRef = useRef(null);
  const sessionRef = useRef(session);
  sessionRef.current = session;

  // Refresh the in-memory session one minute before the access token expires.
  async function refreshSession(current = session) {
    if (!current?.refresh_token) return false;
    try {
      const res = await fetch(`${AUTH}/auth/refresh`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: current.refresh_token }),
      });
      if (!res.ok) throw new Error("refresh failed");
      const tokens = await res.json();
      const updated = { ...tokens, username: current.username, role: decodeRole(tokens.access_token), expiresAt: Date.now() + tokens.expires_in * 1000 };
      setSession(updated);
      return updated;
    } catch {
      setSession(null);
      return false;
    }
  }

  // Schedule rotating refresh before access token expiry.
  useEffect(() => {
    if (!IAM_ENABLED || !session) return undefined;
    const timer = setTimeout(() => refreshSession(), Math.max(1000, session.expiresAt - Date.now() - 60_000));
    return () => clearTimeout(timer);
  }, [session]);

  // Authenticate a user and initialize a memory-only token session.
  async function login(e) {
    e.preventDefault();
    setLoginError("");
    try {
      const res = await fetch(`${AUTH}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: loginName, password: loginPassword }) });
      if (!res.ok) throw new Error(res.status === 429 ? "too many attempts" : "invalid credentials");
      const tokens = await res.json();
      setSession({ ...tokens, username: tokens.username || loginName, role: decodeRole(tokens.access_token), expiresAt: Date.now() + tokens.expires_in * 1000 });
      setLoginPassword("");
    } catch (error) {
      setLoginError(error.message || "invalid credentials");
    }
  }

  // Share one configured helper across roster, chat, and audit requests.
  const clientFetch = createIamClient({ enabled: IAM_ENABLED, baseUrl: BASE,
    getSession: () => sessionRef.current,
    refresh: () => refreshSession(sessionRef.current),
    onExpired: () => setSession(null),
  });


  // Send logout to IAM and clear all in-memory session state.
  async function logout() {
    if (session) {
      await fetch(`${AUTH}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${session.access_token}`, "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: session.refresh_token }) }).catch(() => {});
    }
    setSession(null);
    setTurns([]);
  }

  // Load administrator audit events using the active user and filter values.
  async function loadAudit() {
    const query = new URLSearchParams(Object.entries(auditFilters).filter(([, value]) => value));
    const response = await clientFetch(`/audit?${query}`);
    if (response.ok) setAuditRows((await response.json()).events || []);
  }

  // Agents ki list aur online/offline status backend se laata hai
  async function loadRoster() {
    try {
      const res = await clientFetch("/agents");
      if (!res.ok) throw new Error();
      setRoster(await res.json());
      setRosterError(false);
    } catch {
      setRosterError(true);
    }
  }

  // Page khulte hi agents ki list load hoti hai
  useEffect(() => {
    if (!IAM_ENABLED || session) loadRoster();
  }, [session]);

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
      const res = await clientFetch("/chat", {
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
  function fillSuggestion(text) {
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

  // Render the login form until the volatile IAM session exists.
  if (IAM_ENABLED && !session) return (
    <main className="iam-login-wrap">
      <form className="iam-login" onSubmit={login}>
        <div className="logo">A2</div>
        <h1>A2A Assistant</h1>
        <label>Username<input autoComplete="username" value={loginName} onChange={(e) => setLoginName(e.target.value)} required /></label>
        <label>Password<input type="password" autoComplete="current-password" value={loginPassword} onChange={(e) => setLoginPassword(e.target.value)} required /></label>
        {loginError && <div className="error-box" role="alert">{loginError}</div>}
        <button className="new-btn" type="submit">Log in</button>
      </form>
    </main>
  );

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
          {IAM_ENABLED && <><span className="iam-user">{session.username} <span className="badge">{session.role}</span></span>{session.role === "admin" && <button className="new-btn" onClick={() => { setAuditOpen((open) => !open); if (!auditOpen) loadAudit(); }}>Audit</button>}<button className="new-btn" onClick={logout}>Log out</button></>}
          <button className="new-btn" onClick={newChat} disabled={!!pending}>
            {Icon.plus} <span>New chat</span>
          </button>
        </header>

        <div className="scroll">
          {IAM_ENABLED && auditOpen && session.role === "admin" && <section className="audit-panel">
            <h2>Audit events</h2>
            <div className="audit-filters">
              {[["user", "User"], ["agent", "Agent"]].map(([key, label]) => <input key={key} aria-label={label} placeholder={label} value={auditFilters[key]} onChange={(e) => setAuditFilters((f) => ({ ...f, [key]: e.target.value }))} />)}
              <select aria-label="Decision" value={auditFilters.decision} onChange={(e) => setAuditFilters((f) => ({ ...f, decision: e.target.value }))}><option value="">All decisions</option><option>allow</option><option>deny</option><option>fail</option></select>
              <input aria-label="Limit" type="number" min="1" max="500" value={auditFilters.limit} onChange={(e) => setAuditFilters((f) => ({ ...f, limit: e.target.value }))} />
              <button className="new-btn" onClick={loadAudit}>Apply</button>
            </div>
            <div className="audit-table-wrap"><table><thead><tr>{["ts", "cid", "actor", "role", "event", "agent", "decision", "reason"].map((h) => <th key={h}>{h}</th>)}</tr></thead><tbody>{auditRows.map((row, index) => <tr key={`${row.ts}-${index}`}>{["ts", "cid", "actor", "role", "event", "agent", "decision", "reason"].map((key) => <td key={key}>{row[key] || ""}</td>)}</tr>)}</tbody></table></div>
          </section>}
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
                    <button key={s} className="suggest-card" onClick={() => fillSuggestion(s)}>
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
                          <ReactMarkdown>{IAM_ENABLED && t.data.agents?.some((a) => a.status === "failed" && (/access denied|403/i.test(a.text || ""))) ? t.data.answer.replace(/access denied[^.\n]*|403[^.\n]*/gi, "Not allowed for your role") : t.data.answer}</ReactMarkdown>
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
                          {t.data.correlation_id && <><h4>Correlation ID</h4><pre>{t.data.correlation_id}</pre></>}
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
