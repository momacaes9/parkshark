// A deterministic presentation fixture. Never written to the occupancy feed.
// Real names, visitor prices and geometry come from main.json; availability is simulated.
(function (root) {
  "use strict";
  const fixtures = [
    { id: "w32336367", label: "Bryan Center Pay Lot", free: 12, visitorCapacity: 55, capacityEvidence: "OSM community capacity", scope: null },
    { id: "w27458925", label: "Garage IV · visitor area", free: 8, visitorCapacity: 50, capacityEvidence: "Duke: 50 visitor spaces within the garage", scope: "50 visitor spaces" },
    { id: "w47108081", label: "Research Drive · visitor area", free: 25, visitorCapacity: 250, capacityEvidence: "Duke: 250 visitor spaces on Level 2", scope: "250 visitor spaces" },
  ];
  function create(items) {
    return fixtures.map((f) => {
      const item = items.find((it) => it.id === f.id);
      if (!item || !Number.isFinite(item.visitorRate?.hourlyUsd) || !item.visitorRate.source ||
          (f.scope ? !(item.capacity.scopes || []).some((s) => s.includes(f.scope)) : item.capacity.value !== f.visitorCapacity)) {
        throw new Error("Demo parking facts need review before this scenario can run.");
      }
      return { ...f, item, rate: item.visitorRate.hourlyUsd,
        total: Math.min(item.visitorRate.hourlyUsd * 2, item.visitorRate.dailyMaxUsd ?? Infinity) };
    });
  }
  function available(option, step) { return step >= 2 && option.id === fixtures[0].id ? 0 : option.free; }
  function rank(options, step, walk) {
    return [...options].sort((a, b) => Number(available(a, step) === 0) - Number(available(b, step) === 0) ||
      walk(a.item) - walk(b.item) || a.total - b.total);
  }
  root.ParkSharkDemo = Object.freeze({ create, available, rank });
})(typeof window === "undefined" ? globalThis : window);
