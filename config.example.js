// ParkShark Duke - configuration.
// Copy this file to config.js (which is gitignored) and fill in what you have.
// Everything is optional: with empty values the dashboard uses Esri basemaps
// and a time-of-day occupancy prior.
window.PARKSHARK_CONFIG = {
  // Supabase project (Settings -> API). Use the anon/publishable key only, never service_role.
  SUPABASE_URL: "",          // e.g. "https://abcd1234.supabase.co"
  SUPABASE_KEY: "",          // anon public key

  // Optional: adds a "Google Satellite" basemap option (display only, no data is taken from Google).
  GOOGLE_MAPS_KEY: "",

  // Destination for the time-to-park agent (Wilkinson Building, 534 Research Dr).
  DESTINATION: { name: "Wilkinson Building", lat: 36.0035, lon: -78.9405 },
  // Default origin for drive-time (right-click the map to move it).
  ORIGIN: { name: "Erwin Rd & Research Dr", lat: 36.0067, lon: -78.9420 },
};
