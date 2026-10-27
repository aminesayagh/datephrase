// Reads one JSON task per line: {"text": ..., "ref": ISO}; writes one JSON line with chrono's results.
const chrono = require('chrono-node');
const readline = require('readline');
const rl = readline.createInterface({ input: process.stdin });
const comp = (c) => c && {
  date: c.date().toISOString(),
  known: Object.keys(c.knownValues || {}),
  implied: Object.keys(c.impliedValues || {}),
};
rl.on('line', (line) => {
  const t = JSON.parse(line);
  const results = chrono.parse(t.text, new Date(t.ref), { forwardDate: true });
  process.stdout.write(JSON.stringify(results.map(r => ({ text: r.text, index: r.index, start: comp(r.start), end: comp(r.end) }))) + '\n');
});
