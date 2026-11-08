// Public runtime config for the browser, read from server env vars (never hardcoded, never in git).
// GOOGLE_MAPS_KEY is a browser key by design (Maps JS runs client-side): restrict it to this site's
// referrers in Google Cloud. SUPABASE_ANON_KEY is the public read-only key (RLS enforced).
// Server-only secrets (GEMINI_API_KEY, SUPABASE_SERVICE_KEY) are never returned here.
export function GET() {
  const body = {
    googleMapsKey: process.env.GOOGLE_MAPS_KEY || null,
    googleMapId: process.env.GOOGLE_MAP_ID || "DEMO_MAP_ID",
    supabaseUrl: process.env.SUPABASE_URL || null,
    supabaseAnonKey: process.env.SUPABASE_ANON_KEY || null,
    concierge: Boolean((process.env.GEMINI_API_KEY || "").trim()),
  };
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
