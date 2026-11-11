import { useState } from "react";
import ReactMarkdown from "react-markdown";
import "./App.css";

const API_URL = "http://localhost:8100/chat";

export default function App() {
  const [prompt, setPrompt] = useState("");
  const [data, setData] = useState(null); // client agent ka poora response
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Prompt client agent ko bhejta hai aur response state mein rakhta hai
  async function send() {
    if (!prompt.trim()) return;
    setLoading(true);
    setError("");
    setData(null);
    try {
      const res = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      if (!res.ok) throw new Error(`Backend error ${res.status}`); // 404/500 par saaf error
      const json = await res.json();
      if (json.error) throw new Error(json.error); // backend ka error message
      setData(json);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app">
      <h1>A2A Assistant</h1>

      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="Ask anything: trips, meetings, birthdays, greetings..."
        rows={4}
      />
      <button onClick={send} disabled={loading}>
        {loading ? "Working..." : "Send"}
      </button>

      {error && <div className="error">{error}</div>}

      {data?.agents && (
        <>
          {/* Main answer: normal assistant style */}
          <div className="answer">
            <ReactMarkdown>{data.answer}</ReactMarkdown>
          </div>

          {/* Execution summary + per-agent status chips */}
          <div className="summary">
            <strong>{data.summary}</strong>
            <div className="chips">
              {data.agents.map((a) => (
                <span key={a.agent} className={`chip ${a.status}`}>
                  {a.name}: {a.status}
                </span>
              ))}
            </div>
          </div>

          {/* Secondary details: selection + raw agent outputs */}
          <details>
            <summary>Details</summary>
            <h4>Selection</h4>
            <pre>{JSON.stringify(data.debug?.selection, null, 2)}</pre>
            <h4>Agent outputs</h4>
            <pre>{JSON.stringify(data.agents, null, 2)}</pre>
          </details>
        </>
      )}
    </div>
  );
}