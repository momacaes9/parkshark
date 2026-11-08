import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import '../demo.js';
const data = JSON.parse(await readFile(new URL('../data/main.json', import.meta.url)));
const items = [...data.lots, ...data.streets];
const demo = globalThis.ParkSharkDemo;
const options = demo.create(items);
const walk = (it) => it.dist / 80;
test('all scenario prices and visitor capacities have supporting source data', () => {
  assert.deepEqual(options.map(o => o.visitorCapacity), [55,50,250]);
  assert.deepEqual(options.map(o => o.total), [4,4,4]);
  assert.ok(options.every(o => o.free <= o.visitorCapacity));
});
test('recommendation changes only when the original lot becomes full', () => {
  assert.equal(demo.rank(options, 1, walk)[0].id, 'w32336367');
  assert.equal(demo.available(options[0], 2), 0);
  assert.equal(demo.rank(options, 2, walk)[0].id, 'w27458925');
  assert.equal(demo.rank(options, 3, walk)[0].id, 'w27458925');
});
test('replay restores original availability without mutating source records', () => {
  const snapshot = JSON.stringify(items);
  demo.rank(options, 2, walk);
  assert.deepEqual(options.map(o => demo.available(o,0)), [12,8,25]);
  assert.equal(JSON.stringify(items), snapshot);
});
test('changed facts fail closed instead of silently claiming visitor capacity', () => {
  const changed = structuredClone(items);
  changed.find(it => it.id === 'w27458925').capacity.scopes = [];
  assert.throws(() => demo.create(changed), /need review/);
  assert.throws(() => demo.create([]), /need review/);
});
