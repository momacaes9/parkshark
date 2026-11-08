# Guided hackathon demo

Click **Try demo**, or open `/?demo=1`.

1. Visitor arriving at Wilkinson Tuesday at 9:30 a.m., staying two hours, no permit.
2. **Find my parking** highlights Bryan Center Pay Lot among three visitor options.
3. **Simulate lot filling up** changes Bryan Center from 12 to 0 simulated free spaces and recommends Garage IV’s visitor area.
4. **See the takeaway**, then **Replay demo**, **Restart scenario**, or **Exit demo**. Escape also exits.

All occupancy values are scripted and visibly labeled. They are not model output, live measurements, reservations, or promises of parking. Fixture state stays in the browser and never changes data files, sensor feeds, or the server. Normal dashboard state is restored on exit; timed normal refresh is suspended during the walkthrough.

Names, visitor prices, location and capacity provenance come from `data/main.json`. Garage IV uses only its published 50 visitor spaces; Research Drive uses its 250 visitor spaces, not whole-garage capacity. The two-hour base cost uses published hourly rates and any daily maximum, excluding transaction fees. Walks are approximate straight-line estimates. Current signs and visitor restrictions control actual parking.

Run `npm test` for fixture validity, recommendation changes, replay/source-data isolation, and changed-facts failure handling. The demo requires no Gemini or Supabase credentials. Maps use the existing Google Maps / Leaflet fallback.
