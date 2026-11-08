// ParkShark Parking Concierge - Vercel serverless proxy to Google Gemini (gemini-3.8-flash).
//
// The browser runs the agent loop and executes the tools (they need the live map state);
// this function only adds the API key, a fixed model, system prompt and tool list, and
// forwards the conversation. The key lives in the GEMINI_API_KEY env var (Vercel project
// settings / local .env) and never reaches the browser.
//
// Key types: a key starting with "AQ." is a Vertex AI express-mode key (aiplatform.googleapis.com);
// any other key is a Gemini API key (generativelanguage.googleapis.com).
//
// Wire format with the browser is unchanged: messages are {role: "user"|"assistant", content} with
// content blocks {type:"text"}, {type:"tool_use", id, name, input} and {type:"tool_result", tool_use_id,
// content}; the response is {content, stop_reason}. We translate to and from Gemini contents here.

// Model order: GEMINI_MODEL env override, then current stable Flash models. If Google answers 404
// (model retired or not enabled for this key - gemini-2.5-flash was retired on Vertex on 2026-10-20),
// the next model is tried and remembered for this serverless instance.
const MODELS = [...new Set([process.env.GEMINI_MODEL, "gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash"].filter(Boolean))];
let modelIndex = 0;
const MAX_TOKENS = 8192;           // cost cap for a public demo endpoint; thinking tokens count toward it
const MAX_MESSAGES = 60;
const MAX_BODY_BYTES = 200_000;
const RATE = { windowMs: 60_000, max: 20 };   // per IP, best effort (per serverless instance)
const TIMEOUT_MS = 45_000;

const SYSTEM = `You are the ParkShark Parking Concierge for Duke University's West/Science campus. The driver is going to the Wilkinson Building (534 Research Dr, Durham NC) unless the context says another destination.

Your job: understand the driver's needs (arrival time, how long they stay, vehicle size, wheelchair access, EV charging, budget, whether they are a visitor / Duke student-staff without a permit / Duke permit holder) and recommend ONE place to park, with one or two alternatives.

How to work:
- Always use the tools. They return only sourced facts: Duke's official facility data, OpenStreetMap, City of Durham signs, and the Tier 3 occupancy model. Never invent prices, lot names, space counts or rules. If a tool says a value is null or not published, say "not published".
- If an essential detail is missing, assume a sensible default and say so in one short line instead of asking: visitor, arriving now, 2 hours, standard car, no special needs, no budget limit.
- Typical flow: rank_parking_options -> get_lot_details for your pick -> show_on_map for your pick. Use get_price if the driver asks about a specific place.
- Free-space numbers are estimates from the Tier 3 model unless occupancy_tier is "measured"; say "about" and never promise a space. There is no per-stall availability.
- Respect the budget: if nothing fits, say so and give the cheapest option.
- Mention permit zones, posted signs and warnings the tools report when they matter.

Answer format: plain text, short (under 120 words). Start with the recommendation (name, walk time, price), then why in a few bullet points, then alternatives in one line.`;

const PROFILE_PROPS = {
  role: { type: "string", enum: ["visitor", "affiliate", "permit"], description: "visitor = public; affiliate = Duke student/staff without a permit (can buy daily passes); permit = has a Duke parking permit" },
  hours: { type: "number", description: "Planned stay in hours (e.g. 2)" },
  vehicle: { type: "string", enum: ["compact", "standard", "large"], description: "large = full-size SUV / pickup truck" },
  wheelchair: { type: "boolean", description: "Needs an ADA-accessible stall" },
  senior: { type: "boolean", description: "Prefers the shortest, flattest walk" },
  ev: { type: "boolean", description: "Needs EV charging" },
};
const PROFILE_REQ = ["role", "hours", "vehicle", "wheelchair", "senior", "ev"];

const TOOLS = [
  {
    name: "rank_parking_options",
    description: "Rank parking options (lots, decks, on-street segments, sensor-pilot groups) near the destination for a driver profile and arrival time. Returns for each: id, name, kind, walk minutes to the destination, price for the stay (null when not published), capacity with its confidence, estimated free spaces with range, permits, ADA/EV. Call this first.",
    input_schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        arrival: { type: "string", description: "Arrival as 'now', 'HH:MM' (24h, today) or 'YYYY-MM-DD HH:MM'" },
        ...PROFILE_PROPS,
        max_budget_usd: { type: "number", description: "Maximum the driver wants to pay for the whole stay in USD; 0 means no limit" },
        sort: { type: "string", enum: ["value", "time", "cost"], description: "value = time weighted with price (default), time = fastest, cost = cheapest" },
        limit: { type: "integer", description: "How many options to return (1-8)" },
      },
      required: ["arrival", ...PROFILE_REQ, "max_budget_usd", "sort", "limit"],
    },
  },
  {
    name: "get_price",
    description: "Price and access rules for one place for a driver profile: published visitor hourly rate, permit zones and annual permit rates, hours.",
    input_schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        option_id: { type: "string", description: "Lot or curb id returned by rank_parking_options" },
        arrival: { type: "string", description: "'now', 'HH:MM' or 'YYYY-MM-DD HH:MM'" },
        ...PROFILE_PROPS,
      },
      required: ["option_id", "arrival", ...PROFILE_REQ],
    },
  },
  {
    name: "get_lot_details",
    description: "Everything we know about one lot, deck, street or sensor-pilot group: capacity and its basis, estimated free spaces, permit zones, visitor and permit rates, hours, ADA/EV, levels, stall layout summary, entrances, rules, posted signs, warnings and source links.",
    input_schema: {
      type: "object",
      additionalProperties: false,
      properties: { option_id: { type: "string", description: "Id returned by rank_parking_options" } },
      required: ["option_id"],
    },
  },
  {
    name: "show_on_map",
    description: "Open a lot, deck or street on the driver's map with its detail panel and stall layout.",
    input_schema: {
      type: "object",
      additionalProperties: false,
      properties: { option_id: { type: "string" } },
      required: ["option_id"],
    },
  },
];

const hits = new Map();
function rateLimited(ip) {
  const now = Date.now();
  const arr = (hits.get(ip) || []).filter((t) => now - t < RATE.windowMs);
  arr.push(now);
  hits.set(ip, arr);
  return arr.length > RATE.max;
}

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}

function validMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0 || messages.length > MAX_MESSAGES) return false;
  if (messages[0].role !== "user") return false;
  return messages.every((m) => m && (m.role === "user" || m.role === "assistant") &&
    (typeof m.content === "string" || Array.isArray(m.content)));
}

// Gemini function declarations: same tools, schema without the JSON-Schema-only keywords.
function toGeminiSchema(schema) {
  if (Array.isArray(schema)) return schema.map(toGeminiSchema);
  if (!schema || typeof schema !== "object") return schema;
  const out = {};
  for (const [k, v] of Object.entries(schema)) {
    if (k === "additionalProperties") continue;
    out[k] = k === "properties" ? Object.fromEntries(Object.entries(v).map(([n, p]) => [n, toGeminiSchema(p)])) : toGeminiSchema(v);
  }
  return out;
}
const FUNCTION_DECLARATIONS = TOOLS.map((t) => ({ name: t.name, description: t.description, parameters: toGeminiSchema(t.input_schema) }));

// Browser messages -> Gemini contents.
function toContents(messages) {
  const names = new Map();   // tool_use id -> function name (functionResponse needs the name)
  return messages.map((m) => {
    const blocks = typeof m.content === "string" ? [{ type: "text", text: m.content }] : m.content;
    const parts = [];
    for (const b of blocks) {
      if (!b || typeof b !== "object") continue;
      if (b.type === "text" && typeof b.text === "string" && b.text) parts.push({ text: b.text });
      else if (b.type === "tool_use" && typeof b.name === "string") {
        names.set(b.id, b.name);
        const part = { functionCall: { name: b.name, args: b.input && typeof b.input === "object" ? b.input : {} } };
        if (typeof b.signature === "string") part.thoughtSignature = b.signature;   // must be echoed back
        parts.push(part);
      } else if (b.type === "tool_result") {
        const name = names.get(b.tool_use_id);
        if (!name) continue;
        let result = b.content;
        if (typeof result === "string") { try { result = JSON.parse(result); } catch { /* keep text */ } }
        parts.push({ functionResponse: { name, response: b.is_error ? { error: result } : { result } } });
      }
    }
    if (!parts.length) parts.push({ text: "(empty)" });
    return { role: m.role === "assistant" ? "model" : "user", parts };
  });
}

// Gemini response -> browser content blocks + stop_reason.
function fromCandidate(data) {
  if (data.promptFeedback?.blockReason) return { content: [], stop_reason: "refusal" };
  const cand = data.candidates?.[0];
  if (!cand) return { content: [], stop_reason: "refusal" };
  const content = [];
  let calls = 0;
  for (const p of cand.content?.parts || []) {
    if (p.thought) continue;                          // internal reasoning summary, not shown
    if (p.functionCall) {
      calls++;
      content.push({ type: "tool_use", id: p.functionCall.id || `call_${Date.now().toString(36)}_${calls}`,
        name: p.functionCall.name, input: p.functionCall.args || {}, ...(p.thoughtSignature ? { signature: p.thoughtSignature } : {}) });
    } else if (typeof p.text === "string" && p.text) content.push({ type: "text", text: p.text });
  }
  const fr = cand.finishReason;
  let stop_reason = "end_turn";
  if (calls) stop_reason = "tool_use";
  else if (fr === "MAX_TOKENS") stop_reason = "max_tokens";
  else if (["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"].includes(fr)) stop_reason = "refusal";
  return { content, stop_reason, finish_reason: fr ?? null };
}

function endpoint(key, model = MODELS[modelIndex]) {
  if (key.startsWith("AQ.")) {   // Vertex AI express mode
    return { provider: "vertex", model, url: `https://aiplatform.googleapis.com/v1/publishers/google/models/${model}:generateContent?key=${encodeURIComponent(key)}`, headers: {} };
  }
  return { provider: "gemini", model, url: `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, headers: { "x-goog-api-key": key } };
}

// Google's error text never contains the key, but strip anything key-like anyway before showing it.
const cleanMsg = (m) => String(m || "").replace(/(AIza|AQ\.)[A-Za-z0-9_.-]+/g, "[key]").slice(0, 300);

export async function POST(request) {
  const key = (process.env.GEMINI_API_KEY || "").trim();
  if (!key) return json(503, { error: "Concierge is not configured: set GEMINI_API_KEY on the server." });
  const ip = (request.headers.get("x-forwarded-for") || "local").split(",")[0].trim();
  if (rateLimited(ip)) return json(429, { error: "Too many requests - wait a minute and try again." });

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return json(413, { error: "Conversation too long - start a new chat." });
  let body;
  try { body = JSON.parse(raw); } catch { return json(400, { error: "Invalid JSON" }); }
  if (!validMessages(body.messages)) return json(400, { error: "Invalid messages" });

  const payload = {
    systemInstruction: { parts: [{ text: SYSTEM }] },
    contents: toContents(body.messages),
    tools: [{ functionDeclarations: FUNCTION_DECLARATIONS }],
    toolConfig: { functionCallingConfig: { mode: "AUTO" } },
    generationConfig: { maxOutputTokens: MAX_TOKENS },   // Gemini 3: keep default temperature and thinking
  };
  let res, data, ep;
  for (let i = modelIndex; i < MODELS.length; i++) {
    ep = endpoint(key, MODELS[i]);
    try {
      res = await fetch(ep.url, { method: "POST", headers: { "content-type": "application/json", ...ep.headers },
        body: JSON.stringify(payload), signal: AbortSignal.timeout(TIMEOUT_MS) });
    } catch (error) {
      return json(502, { error: error?.name === "TimeoutError" ? "The AI service timed out - try again." : "AI service unreachable" });
    }
    data = await res.json().catch(() => ({}));
    if (res.status !== 404) { modelIndex = i; break; }
    console.warn(`[concierge] ${ep.provider} model ${ep.model} -> 404: ${cleanMsg(data?.error?.message)}`);
  }
  if (!res.ok) {
    const msg = cleanMsg(data?.error?.message);
    const status = String(data?.error?.status || "");
    console.warn(`[concierge] ${ep.provider} ${ep.model} -> ${res.status} ${status}: ${msg}`);
    if (res.status === 401 || res.status === 403 || /API key|API_KEY/i.test(msg)) return json(500, { error: "Server API key is invalid or not allowed to use Gemini." });
    if (res.status === 429 || status === "RESOURCE_EXHAUSTED") return json(429, { error: "The AI service is busy - try again in a moment." });
    if (res.status === 400) return json(400, { error: "Request rejected: " + msg });
    return json(502, { error: `AI service error (${res.status}): ${msg || "no details"}` });
  }
  return json(200, { ...fromCandidate(data), model: data.modelVersion || ep.model, provider: ep.provider });
}

export function GET() {
  const key = (process.env.GEMINI_API_KEY || "").trim();
  return json(200, { ok: true, configured: Boolean(key), model: MODELS[modelIndex], fallbacks: MODELS.slice(modelIndex + 1), provider: key ? endpoint(key).provider : null });
}

// Exported for tests only.
export const _internal = { toContents, fromCandidate, endpoint, FUNCTION_DECLARATIONS };
