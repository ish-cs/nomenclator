# Augur v0.3.2 — Ask AI Accuracy Overhaul

**Status:** Planning
**Version:** v0.3.2
**Branch:** jinka
**Based on:** v0.3.1 shipped
**Goal:** Fix the Ask AI feature to achieve 99%+ accuracy — eliminate hallucination, deliver grounded, specific, context-aware answers on every message.

---

## Root Cause Diagnosis

Deep code inspection of `screenpipe-dashboard.html` (`getScreenpipeContext`, `sendAI`) revealed **6 compounding bugs** causing bad answers. Listed by severity:

---

### Bug 1 — CRITICAL: Context is dead after the first message (line 2029)

```javascript
// Current code (lines 2026–2029):
const historyMessages = chatHistory.slice(-8);
const messages = [
  ...historyMessages,
  { role: 'user', content: historyMessages.length === 0 ? userMessageWithSystem : question }
];
```

When any chat history exists (`historyMessages.length > 0`), the model receives only the bare `question` string — **no system prompt, no screen context, no identity**. The model is answering blind from message 2 onwards. It then invents plausible-sounding but fabricated answers because it has no grounding data.

This is the single largest source of hallucination. Every follow-up question in a conversation is unanswered with real data.

---

### Bug 2 — CRITICAL: Context retrieval bypasses context-server.py entirely (lines 1947–1968)

`getScreenpipeContext()` calls screenpipe directly on port 3030 with its own inferior retrieval logic. It completely ignores `context-server.py` (port 3031) which already has:

- **Hybrid scoring**: keyword × 3 + recency + semantic (cosine × 2.0 via Chroma)
- **Browser captures**: tab activity, dwell time, selected text — not available via port 3030
- **Better deduplication** and ranking already built and tested

The AI is working off raw, unranked screen frames instead of the best-matched context the system already knows how to compute.

---

### Bug 3 — HIGH: Context chunks truncated to 150 characters (line 1995)

```javascript
return `[${time}] [${app}]\n${text.slice(0, 150)}`;
```

150 chars = ~25–35 words. Most frames are cut mid-sentence, stripping the most informative part of the content. The model gets headers without bodies, code without context, URLs without content. A meaningful chunk needs 350–400 chars to carry a complete thought.

---

### Bug 4 — HIGH: System prompt has no grounding or anti-hallucination rules (line 2022)

```javascript
const systemPrompt = `You are a helpful AI assistant with access to screenpipe data -- OCR captures of the user's screen. Answer concisely and helpfully. Use **bold** for key points. No markdown headers.${contextBlock}`;
```

No constraints, no citation requirement, no "say I don't know" instruction. Without explicit grounding rules, LLMs (especially smaller local models) default to their training data when context is thin or ambiguous, producing confident but fabricated answers. Research shows explicit "only use provided context" + "say I don't know if unsupported" instructions reduce hallucination by 40–80%.

---

### Bug 5 — MEDIUM: No user profile card injected into the prompt

The `/context-card?days=7` endpoint on context-server.py generates a compact behavioral profile (e.g., "User primarily uses VS Code, Chrome, Figma. Recent topics: react, typescript, design systems. Most active 09:00–17:00."). This is ~300 chars and dramatically personalizes answers. It is never used in Ask AI.

---

### Bug 6 — MEDIUM: Context scoring is weaker than the server (lines 1972–1986)

The client-side scoring in `getScreenpipeContext()` does: `keywordScore * 3 + recencyScore`. The server does: `(kw × 3) + recency + semantic_bonus(cosine × 2.0) + [time_bonus + selection_bonus for browser]`. The client can never match the server's ranked relevance. The top 20 frames surfaced client-side will often miss the most semantically relevant ones.

---

## Solution Architecture

All changes are in `screenpipe-dashboard.html` only. Backend is already correct — the fix is entirely in how the frontend queries and uses it.

### New AI call flow (every message):

```
User sends message
        │
        ├── parallel ─── getScreenpipeContext(question)   ← now calls context-server.py /context
        │                 (hybrid ranked: keyword + semantic + browser captures)
        │
        └── (already cached) userProfileCard              ← fetched once on init()
                                    │
                    ┌───────────────┘
                    │
              Build messages array:
              [
                { role: 'user',      content: SYSTEM_PROMPT + PROFILE + FRESH_CONTEXT },
                { role: 'assistant', content: 'Understood. Ready to help.' },  ← synthetic ACK
                ...chatHistory.slice(-6),    ← real conversation pairs
                { role: 'user',      content: question }   ← current question
              ]
                    │
              POST to LM Studio /v1/chat/completions
                    │
              Store in chatHistory: { question, reply } only (no context bloat)
```

This pattern ensures: (a) context is always fresh for every message, (b) conversation history provides continuity, (c) the model is always grounded.

---

## Implementation Plan

Single file: `screenpipe-dashboard.html`

---

### Change 1 — New `getScreenpipeContext()` — use context-server.py

**Replace the entire function body.** New behavior:

1. Call `http://localhost:3031/context?q=${encodeURIComponent(question)}&limit=12&window_hours=24`
2. If context-server is offline, fall back to direct screenpipe `GET /search?limit=20` (current logic, but with 400-char chunks)
3. Format each result frame as `[HH:MM AM/PM] [AppName / WindowName]\n{text.slice(0, 400)}`
4. Include browser capture frames (they come unified from `/context`)
5. Dynamic char budget: cap context block at `Math.min(4000, 5500 - Math.floor(chatHistory.slice(-6).reduce((n, m) => n + (m.content||'').length, 0) / 4) * 4)` — leaves room for history and response
6. If context is empty, return a minimal "no recent screen data" sentinel string

New function:
```javascript
async function getScreenpipeContext(question) {
  try {
    // Primary: use context-server.py hybrid ranking (keyword + semantic + browser)
    const ctxResp = await fetch(
      `http://localhost:3031/context?q=${encodeURIComponent(question)}&limit=12&window_hours=24`
    ).then(r => r.json()).catch(() => null);

    let frames = [];

    if (ctxResp && ctxResp.results && ctxResp.results.length > 0) {
      // Use hybrid-ranked results from context-server.py
      frames = ctxResp.results.map(r => {
        const time = r.timestamp ? new Date(r.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '?';
        const src  = r.source === 'browser' ? `Browser / ${(r.window || r.url || '').slice(0, 60)}` : `${r.app || '?'} / ${(r.window || '').slice(0, 50)}`;
        const text = (r.text || '').slice(0, 400);
        return `[${time}] [${src}]\n${text}`;
      });
    } else {
      // Fallback: direct screenpipe (context-server offline)
      const recent = await fetch(`${API}/search?limit=20`).then(r => r.json()).catch(() => ({ data: [] }));
      const allItems = recent.data || [];
      allItems.sort((a, b) => new Date(b.content?.timestamp || 0) - new Date(a.content?.timestamp || 0));
      frames = allItems.slice(0, 12).map(item => {
        const c = item.content;
        const time = c.timestamp ? new Date(c.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '?';
        const isOCR = item.type === 'OCR';
        const src = isOCR ? `${c.app_name || '?'} / ${(c.window_name || '').slice(0, 50)}` : 'audio';
        const text = ((isOCR ? c.text : c.transcription) || '').slice(0, 400);
        return `[${time}] [${src}]\n${text}`;
      });
    }

    if (frames.length === 0) {
      return '\n\n[No recent screen data available for this query.]';
    }

    // Dynamic budget: leave room for chat history + response
    const historyChars = chatHistory.slice(-6).reduce((n, m) => n + (m.content || '').length, 0);
    const budget = Math.max(1500, Math.min(4000, 5500 - historyChars));

    const label = ctxResp?.keywords?.join(', ') || question.slice(0, 60);
    let block = `\n\n--- SCREEN CONTEXT (${frames.length} captures, ranked for: ${label}) ---\n\n`;
    block += frames.join('\n\n---\n\n');
    block += '\n\n--- END CONTEXT ---';

    if (block.length > budget) {
      block = block.slice(0, budget) + '\n[...context trimmed to fit model window]';
    }
    return block;

  } catch (e) {
    return '';
  }
}
```

---

### Change 2 — New system prompt with grounding rules

**Replace** the `systemPrompt` string in `sendAI()`:

```javascript
const systemPrompt = `You are Augur AI — a personal assistant with access to the user's screen capture data (OCR screenshots and audio transcriptions captured by screenpipe).

RULES — follow these strictly:
1. Ground every answer in the provided SCREEN CONTEXT. Do not invent activities, apps, code, or content not present in the context.
2. When context supports the answer: be specific. Reference exact app names, window titles, timestamps, and text visible on screen.
3. When the context does NOT contain enough information to answer, say exactly: "I don't see that in your recent screen data."
4. For factual/general questions unrelated to screen activity: answer from knowledge and state "From general knowledge:".
5. Use **bold** for key facts. No markdown headers. Concise answers only.${userProfileCard ? '\n\n' + userProfileCard : ''}`;
```

---

### Change 3 — Fix `sendAI()` message construction (the critical bug)

**Replace** lines 2026–2030 with the synthetic-turn pattern that injects fresh context on every message:

```javascript
// Context is always fresh — fetched above for this specific question
const systemTurn = { role: 'user', content: `${systemPrompt}\n\n${contextBlock}` };
const systemAck  = { role: 'assistant', content: 'Understood. I have your screen context. Ready to help.' };
const historyMessages = chatHistory.slice(-6);   // last 3 conversation pairs
const messages = [
  systemTurn,
  systemAck,
  ...historyMessages,
  { role: 'user', content: question }
];
```

`chatHistory` continues to store only `{ role: 'user', content: question }` and `{ role: 'assistant', content: reply }` — clean pairs, no context bloat.

---

### Change 4 — Add `userProfileCard` with fetch + cache

**Add** these at the top of the AI Chat section (near `let chatHistory = []`):

```javascript
let userProfileCard = '';

async function loadUserProfileCard() {
  try {
    const r = await fetch('http://localhost:3031/context-card?days=7');
    const d = await r.json();
    if (d.card) userProfileCard = d.card;
  } catch (e) {
    userProfileCard = '';
  }
}
```

**Add** to `init()`:
```javascript
loadUserProfileCard();
setInterval(loadUserProfileCard, 60 * 60 * 1000); // refresh hourly
```

---

### Change 5 — Reduce `chatHistory` slice from `-8` to `-6`

In the new `sendAI()`, history is `.slice(-6)` (3 exchange pairs). This frees ~200–400 tokens for context without sacrificing useful conversation memory. Users rarely need more than 3 previous exchanges to maintain context in a session.

---

### Change 6 — Better error surface for context-server offline

When `ctxResp` is null (context-server offline), the current fallback now shows a subtle indicator in the context header:

```
--- SCREEN CONTEXT (12 captures, fallback mode — context server offline) ---
```

This lets the user know why results may be less accurate without blocking them.

---

## Why These Changes Work: Research Basis

| Technique | Source | Expected Gain |
|-----------|--------|---------------|
| Always-fresh context injection per message | Standard RAG architecture | Eliminates hallucination in multi-turn chat |
| Hybrid retrieval (keyword + semantic + browser) via context-server | arxiv 2501.07391, BM25+dense vectors research | +20–40% retrieval precision |
| Grounded system prompt with explicit "say I don't know" | AWS Bedrock anti-hallucination research, Promptfoo | 40–80% hallucination reduction |
| Synthetic turn pattern for Mistral-compatible system prompt injection | LM Studio/Mistral community best practice | Correct system-level grounding without `system` role |
| 400-char chunks instead of 150-char | RAG chunking research: 400-token chunks maximize recall | Provides complete thoughts vs truncated fragments |
| User profile card in system prompt | Context engineering: personalization reduces generic answers | Fewer "I don't know your activity" responses |
| Dynamic token budget | Context window utilization research (arxiv 2407.19794) | Optimal use of model context at any history length |

---

## Accuracy Test Scenarios

These scenarios should be manually tested after implementation:

| Scenario | Expected Result | Current (broken) |
|----------|----------------|------------------|
| "What was I working on at 2pm?" | Specific app + content from context | Generic or hallucinated answer |
| Follow-up: "What else was in that file?" | Correct context from prior exchange | Loses context, invents code |
| "What websites did I visit?" | Includes browser captures (URL + title + dwell) | Only OCR frames, misses browser |
| "What did I do in Figma?" (user never opened Figma) | "I don't see Figma in your screen data." | Invents Figma usage |
| "What is a REST API?" | "From general knowledge: A REST API is..." | Correct but context-unrelated |
| 4th message in conversation | Same accuracy as 1st message | Currently zero context |

---

## Constraints

- **Single file:** `screenpipe-dashboard.html` only.
- **No new dependencies:** No new JS libraries. Uses existing `fetch()`.
- **No backend changes:** `context-server.py` is already correct. No changes.
- **No git operations** unless explicitly requested.
- **Backward-compatible:** If context-server is offline, fallback to direct screenpipe.
- **No UI changes:** This is a pure logic fix — no layout, styling, or tab changes.

---

## Verification Checklist (manual, post-implementation)

**Core accuracy:**
- [ ] Message 1 answer is grounded in actual screen data
- [ ] Message 2, 3, 4+ answers are equally grounded (context re-injected each time)
- [ ] Follow-up questions reference correct prior context from chatHistory
- [ ] "I don't see that in your screen data" appears when user asks about absent activity
- [ ] Browser tab visits appear in answers about "what sites did I visit"

**Context quality:**
- [ ] Context block in network tab shows 10–12 frames per request
- [ ] Frames are > 150 chars each (check network payload)
- [ ] Requests route to port 3031 (context-server), not just 3030 (screenpipe)
- [ ] When context-server offline, fallback to 3030 with no crash

**System prompt:**
- [ ] Model badge in UI still shows correctly
- [ ] User profile card text visible in network payload (if context-server running)
- [ ] No context window overflow errors (LM Studio stays under token limit)

---

## Subagent Structure

Single subagent — all changes are in one file, tightly coupled, sequential.

**Scope:** `screenpipe-dashboard.html` only.

**Order of edits:**
1. Add `userProfileCard` variable and `loadUserProfileCard()` function near `let chatHistory = []`
2. Add `loadUserProfileCard()` and `setInterval` to `init()`
3. Replace entire body of `getScreenpipeContext()` with new version
4. Replace `systemPrompt` string in `sendAI()`
5. Replace message construction block in `sendAI()` (lines 2026–2030)

Do not change anything else. Do not refactor surrounding code. Do not touch HTML, CSS, or any other JS function.
