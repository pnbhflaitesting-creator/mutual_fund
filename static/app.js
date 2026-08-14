/* Mutual Fund Voice Bot — front-end.
 * Uses the Web Speech API for speech-to-text (recognition) and
 * text-to-speech (synthesis). Falls back gracefully to typed input when
 * recognition isn't supported (e.g. Firefox).
 */

const micBtn = document.getElementById("micBtn");
const statusEl = document.getElementById("status");
const heardEl = document.getElementById("heard");
const textInput = document.getElementById("textInput");
const askBtn = document.getElementById("askBtn");
const answerEl = document.getElementById("answer");
const answerTitle = document.getElementById("answerTitle");
const answerMeta = document.getElementById("answerMeta");
const fundList = document.getElementById("fundList");
const replayBtn = document.getElementById("replayBtn");

let lastSpoken = "";

// ---------------------------------------------------------------- speech out
function speak(text) {
  if (!("speechSynthesis" in window) || !text) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.02;
  u.pitch = 1.0;
  // Prefer an English (India) voice when present.
  const voices = window.speechSynthesis.getVoices();
  const preferred = voices.find(v => /en[-_]IN/i.test(v.lang)) ||
                    voices.find(v => /^en/i.test(v.lang));
  if (preferred) u.voice = preferred;
  window.speechSynthesis.speak(u);
}

// ------------------------------------------------------------------ ask API
async function ask(query) {
  if (!query || !query.trim()) return;
  heardEl.textContent = `You asked: “${query}”`;
  statusEl.textContent = "Thinking…";
  try {
    const res = await fetch(`/api/query?q=${encodeURIComponent(query)}`);
    const data = await res.json();
    render(data);
    lastSpoken = data.spoken;
    speak(data.spoken);
    statusEl.textContent = "Tap the mic and ask a question";
  } catch (err) {
    statusEl.textContent = "Something went wrong. Please try again.";
    console.error(err);
  }
}

function render(data) {
  const p = data.parsed || {};
  const periodLabel = { "1y": "1-year", "3y": "3-year", "5y": "5-year" }[p.period] || p.period;
  const catLabel = (data.results[0] && p.category) ? data.results[0].category_label : "all";
  answerTitle.textContent = p.category
    ? `Top ${data.results.length} ${catLabel} funds — ${periodLabel} return`
    : `Top ${data.results.length} funds — ${periodLabel} return`;

  const sourceText = { live: "live data", mixed: "live + snapshot", snapshot: "snapshot data" }[data.source] || data.source;
  answerMeta.textContent = `Ranked by ${periodLabel} annualised return · ${sourceText}`;

  fundList.innerHTML = "";
  if (!data.results.length) {
    const li = document.createElement("li");
    li.textContent = "No matching funds found. Try another category.";
    fundList.appendChild(li);
  }
  for (const r of data.results) {
    const li = document.createElement("li");
    const badge = r.live
      ? `<span class="badge live">live</span>`
      : `<span class="badge">snapshot</span>`;
    li.innerHTML = `
      <div class="fund-info">
        <div class="fund-name">${escapeHtml(r.name)} ${badge}</div>
        <div class="fund-sub">${escapeHtml(r.amc)} · ${escapeHtml(r.category_label)}</div>
      </div>
      <div class="fund-return">
        <div class="pct">${r.return_pct}%</div>
        <div class="lbl">${periodLabel}</div>
      </div>`;
    fundList.appendChild(li);
  }
  answerEl.hidden = false;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// -------------------------------------------------------------- speech in
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;

if (SR) {
  recognition = new SR();
  recognition.lang = "en-IN";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    listening = true;
    micBtn.classList.add("listening");
    statusEl.textContent = "Listening… ask your question";
  };
  recognition.onresult = (e) => {
    const transcript = e.results[0][0].transcript;
    ask(transcript);
  };
  recognition.onerror = (e) => {
    statusEl.textContent = e.error === "not-allowed"
      ? "Microphone access denied. You can type instead."
      : "Didn't catch that. Try again or type your question.";
  };
  recognition.onend = () => {
    listening = false;
    micBtn.classList.remove("listening");
  };

  micBtn.addEventListener("click", () => {
    if (listening) { recognition.stop(); return; }
    window.speechSynthesis && window.speechSynthesis.cancel();
    try { recognition.start(); } catch (_) {}
  });
} else {
  statusEl.textContent = "Voice input isn't supported in this browser — type your question below.";
  micBtn.addEventListener("click", () => textInput.focus());
}

// ----------------------------------------------------------------- wiring
askBtn.addEventListener("click", () => ask(textInput.value));
textInput.addEventListener("keydown", (e) => { if (e.key === "Enter") ask(textInput.value); });
replayBtn.addEventListener("click", () => speak(lastSpoken));
document.querySelectorAll(".chip").forEach(chip => {
  chip.addEventListener("click", () => ask(chip.dataset.q));
});

// Warm up voice list (some browsers load voices asynchronously).
if ("speechSynthesis" in window) {
  window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
}
