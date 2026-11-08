// ParkShark Parking Concierge - Vercel serverless proxy to the Claude API.
//
// The browser runs the agent loop and executes the tools (they need the live map state);
// this function only adds the API key, a fixed model, system prompt and tool list, and
// forwards the conversation. The key lives in the ANTHROPIC_API_KEY env var (Vercel project
// settings / local .env) and never reaches the browser.
import Anthropic from "@anthropic-ai/sdk";

const MODEL = "claude-opus-5-5";
const MAX_TOKENS = 4096;           // cost cap for a public demo endpoint; answers are short
const MAX_MESSAGES = 60;
const MAX_BODY_BYTES = 200_000;
const RATE = { windowMs: 60_000, max: 20 };   // per IP, best effort (per serverless instance)

const SYSTEM = `You are the ParkShark Parking Concierge for Duke University's West/Science campus. The driver is going to the Wilkinson Building (534 Research Dr, Durham NC) unless they say otherwise.

Your job: understand the driver's needs (arrival time, how long they stay, vehicle size, wheelchair access, EV charging, budget, whether they are a visitor / Duke student-staff without a permit / Duke permit holder) and recommend ONE parking option and a specific spot, with one or two alternatives.

How to work:
- Always use the tools for availability, travel time, prices and spot quality. Never invent prices, lot names or numbers.
- If an essential detail is missing, assume a sensible default and say so in one short line instead of asking: visitor, arriving now, 2 hours, standard car, no special needs, no budget limit. Ask a question only if the request is genuinely ambiguous (e.g. a different destination).
- Typical flow: rank_parking_options with the driver's profile and arrival time -> score_spots for the best lot -> show_on_map for your pick. Use get_price if the driver asks about a specific lot.
- Garages have no mapped stalls (any open space works). If your top pick is a garage, also score the best surface lot from the ranking so the driver gets a concrete stall as the alternative.
- Respect the budget: if no option fits, say so and give the cheapest option.
- Be honest about uncertainty: stall positions are often estimated, curb spaces may be drop-off or permit zones, and occupancy for a future arrival is a forecast. Spot availability returned by score_spots is for right now.
- On Duke game days, mention the event rules the tools report.

Answer format: plain text, short (under 120 words). Start with the recommendation (lot name, spot id, total time, price), then why in a few bullet points, then alternatives in one line.`;

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
    description: "Rank parking options (lots, garages, CV-verified curb segments) near the destination for a driver profile and arrival time. Returns for each option: id, name, kind, total minutes (drive + search + walk), price for the stay, predicted free spaces and occupancy at arrival, plus any game-day event in effect. Call this first.",
    strict: true,
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
    description: "Price and access rules for one lot/garage/curb segment for a driver profile and arrival time (visitor hourly rates, daily passes, permit-only, game-day rules).",
    strict: true,
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
    name: "score_spots",
    description: "Score the currently open stalls of one lot (0-100) for this driver: neighbours (large vehicles), middle vs end stall, walk and slope, ADA/EV fit, sun/rain, price. Returns the best stalls with reasons. Availability is as of right now.",
    strict: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        option_id: { type: "string", description: "Lot or curb id" },
        ...PROFILE_PROPS,
        limit: { type: "integer", description: "How many stalls to return (1-5)" },
      },
      required: ["option_id", ...PROFILE_REQ, "limit"],
    },
  },
  {
    name: "show_on_map",
    description: "Zoom the driver's map to a lot and highlight a recommended stall so they can see it.",
    strict: true,
    input_schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        option_id: { type: "string" },
        spot_id: { type: "string", description: "Stall id from score_spots, or an empty string to just show the lot" },
      },
      required: ["option_id", "spot_id"],
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

export async function POST(request) {
  if (!process.env.ANTHROPIC_API_KEY) {
    return json(503, { error: "Concierge is not configured: set ANTHROPIC_API_KEY on the server." });
  }
  const ip = (request.headers.get("x-forwarded-for") || "local").split(",")[0].trim();
  if (rateLimited(ip)) return json(429, { error: "Too many requests - wait a minute and try again." });

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return json(413, { error: "Conversation too long - start a new chat." });
  let body;
  try { body = JSON.parse(raw); } catch { return json(400, { error: "Invalid JSON" }); }
  if (!validMessages(body.messages)) return json(400, { error: "Invalid messages" });

  const client = new Anthropic();   // reads ANTHROPIC_API_KEY
  try {
    const response = await client.beta.messages.create({
      model: MODEL,
      max_tokens: MAX_TOKENS,
      output_config: { effort: "medium" },
      // Server-side refusal fallback: if Opus 5.5 declines on a safety classifier, the API re-runs the turn
      // on a fallback model chosen by refusal category.
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      system: SYSTEM,
      tools: TOOLS,
      cache_control: { type: "ephemeral" },
      messages: body.messages,
    });
    // Return the full content (thinking/fallback blocks included) - the browser echoes it back unchanged.
    return json(200, { content: response.content, stop_reason: response.stop_reason, stop_details: response.stop_details ?? null, model: response.model });
  } catch (error) {
    if (error instanceof Anthropic.AuthenticationError) return json(500, { error: "Server API key is invalid." });
    if (error instanceof Anthropic.RateLimitError) return json(429, { error: "The AI service is busy - try again in a moment." });
    if (error instanceof Anthropic.BadRequestError) return json(400, { error: "Request rejected: " + error.message });
    if (error instanceof Anthropic.APIError) return json(502, { error: `AI service error (${error.status ?? "network"})` });
    return json(500, { error: "Unexpected server error" });
  }
}

export function GET() {
  return json(200, { ok: true, configured: Boolean(process.env.ANTHROPIC_API_KEY), model: MODEL });
}
