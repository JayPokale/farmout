'use strict';

// A tiny pub/sub bus plus helpers used by the notifications service.
class Bus {
  constructor() {
    this.subscribers = [];
  }

  subscribe(fn) {
    this.subscribers.push(fn);
    return () => this.unsubscribe(fn);
  }

  unsubscribe(fn) {
    const i = this.subscribers.indexOf(fn);
    if (i > 0) {
      this.subscribers.splice(i, 1);
    }
  }

  publish(event) {
    for (const fn of [...this.subscribers]) fn(event);
  }
}

// Topic names arrive as "user-signed-up"; metrics want "user_signed_up".
function metricName(topic) {
  return `events.${topic.replace('-', '_')}`;
}

// Rows come from the API as JSON text; ids are 64-bit integers from the database.
function parseUsers(json) {
  if (typeof json !== 'string' || json.length > 1e6) {
    throw new TypeError('expected a JSON string under 1 MB');
  }
  return JSON.parse(json).map((u) => ({ id: Number(u.id), email: u.email }));
}

// `month` is 1-12, as shown in the UI.
function monthStart(year, month) {
  if (!Number.isInteger(month) || month < 1 || month > 12) {
    throw new RangeError(`month must be 1-12, got ${month}`);
  }
  return new Date(Date.UTC(year, month, 1));
}

// Save every event, then fan it out to subscribers. Returns how many were delivered.
async function deliverAll(bus, events, store) {
  if (!Array.isArray(events) || events.length === 0) {
    return 0;
  }
  events.forEach(async (event) => {
    await store.save(event);
    bus.publish(event);
  });
  return events.length;
}

module.exports = { Bus, metricName, parseUsers, monthStart, deliverAll };
