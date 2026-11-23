// Meetup group calendar (iCal) in the Google script: first run = one list, later only new events, no duplicates.
const fs = require('fs'), assert = require('assert');
const src = fs.readFileSync('backup/meetup_google_script.gs', 'utf8');

const ev = (id, title, start, loc, extra = '') =>
  `BEGIN:VEVENT\r\nDTSTAMP:20261123T120000Z\r\nDTSTART;TZID=Europe/Vienna:${start}\r\nSUMMARY:${title}\r\n` +
  `LOCATION:${loc}\r\nURL:https://www.meetup.com/vienna-sports-meetup/events/${id}/\r\nUID:event_${id}@meetup.com\r\n${extra}END:VEVENT\r\n`;
let ical = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-TIMEZONE:Europe/Vienna\r\n' +
  ev(1001, 'Tuesday Badminton / Praterstern / intermediate+', '20261124T180000', 'Sporthalle Praterstern (Wien\\, AT)') +
  ev(1002, 'Tuesday (bi-weekly) Padel\\, pre tournament rehearsal', '20261124T180000', 'Padelzone Floridsdorf (Wien\\, AT)') +
  ev(1003, 'Friday Padel Outdoor at Donau\r\n city', '20261127T180000', 'Sportcenter Donaucity (Arbeiterstrandbadstraße 128\\, Wien)') +
  ev(1004, 'Sunday Padel Outdoor at Sportcenter Donaucity.', '20261129T123000', 'Sportcenter Donaucity') +
  ev(1005, 'Old Padel game', '20261101T100000', 'x') +
  ev(1006, 'Cancelled Padel', '20261130T100000', 'x', 'STATUS:CANCELLED\r\n') +
  'BEGIN:VEVENT\r\nDTSTART:20261201T170000Z\r\nSUMMARY:Tuesday (bi-weekly) Padel\\, pre tournament rehearsal\r\nURL:https://www.meetup.com/vienna-sports-meetup/events/1007/\r\nUID:event_1007@meetup.com\r\nEND:VEVENT\r\n' +
  'END:VCALENDAR\r\n';

const props = { TELEGRAM_BOT_TOKEN: '123:ABC', TELEGRAM_CHAT_ID: '42' };
const sent = [], fetched = [];
let now = Date.UTC(2026, 10, 23, 12, 0);
const RealDate = Date;
class FakeDate extends RealDate { constructor(...a) { super(...(a.length ? a : [now])); } static now() { return now; } }
const sandbox = {
  Date: FakeDate,
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => props[k] ?? null, setProperty: (k, v) => { props[k] = String(v); },
                       deleteProperty: k => { delete props[k]; }, getProperties: () => ({ ...props }) }) },
  UrlFetchApp: { fetch: (url, o) => {
    fetched.push(url);
    if (url.includes('api.telegram.org')) { sent.push(o.payload.text); return { getResponseCode: () => 200, getContentText: () => '{"ok":true}' }; }
    if (url.includes('/events/ical/')) return { getResponseCode: () => 200, getContentText: () => ical };
    return { getResponseCode: () => 200, getContentText: () => '' };          // mute.txt: nothing muted
  } },
  GmailApp: { search: () => [] },
  ScriptApp: {}, Logger: { log: () => {} }, ContentService: {},
  Utilities: { formatDate: (d, tz, f) => {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
      .formatToParts(d).map(x => [x.type, x.value]));
    return f === 'yyyy-MM-dd' ? `${p.year}-${p.month}-${p.day}` : `${p.hour === '24' ? '00' : p.hour}:${p.minute}`; },
    getUuid: () => 'u' },
};
const api = new Function(...Object.keys(sandbox), src + '\nreturn { checkMeetup, sendAllPadelEventsNow, parseIcal, upcoming, buildText, saveEvent };')(...Object.values(sandbox));

// parsing
const list = api.parseIcal(ical);
const byId = Object.fromEntries(list.map(e => [e.id, e]));
assert.strictEqual(byId['1002'].title, 'Tuesday (bi-weekly) Padel, pre tournament rehearsal', 'escaped comma');
assert.strictEqual(byId['1003'].title, 'Friday Padel Outdoor at Donaucity', 'folded line joined');
assert.strictEqual(byId['1003'].venue, 'Sportcenter Donaucity', 'venue without address');
assert.strictEqual(byId['1004'].time, '12:30');
assert.strictEqual(byId['1007'].date + ' ' + byId['1007'].time, '2026-12-01 18:00', 'UTC time converted to Vienna');
assert(!byId['1006'], 'cancelled event left out');

// first run: e-mail import + calendar -> ONE list message with the 4 upcoming padel events
api.checkMeetup();
assert.strictEqual(sent.length, 1, 'first run sends exactly one message: ' + sent.length);
assert(sent[0].includes('all upcoming padel events (4)'), sent[0]);
['Tue 24.11. 18:00', 'Fri 27.11. 18:00', 'Sun 29.11. 12:30', 'Tue 01.12. 18:00'].forEach(t => assert(sent[0].includes(t), t));
assert(!sent[0].includes('Badminton') && !sent[0].includes('Old Padel') && !sent[0].includes('Cancelled'));
assert(sent[0].includes('Padel, pre tournament') && !sent[0].includes('<script'));

// 5 minutes later: calendar not read again (every 30 min), nothing sent
const calls = fetched.filter(u => u.includes('ical')).length;
now += 5 * 60000; api.checkMeetup();
assert.strictEqual(fetched.filter(u => u.includes('ical')).length, calls, 'calendar read at most every 30 min');
assert.strictEqual(sent.length, 1);

// 31 minutes later a new padel event appears -> exactly one "new padel event" message
now += 31 * 60000;
ical = ical.replace('END:VCALENDAR', ev(1008, 'Wednesday Padel Night', '20261202T190000', 'Padeldome Erdberg (Wien)') + 'END:VCALENDAR');
api.checkMeetup();
assert.strictEqual(sent.length, 2);
assert(sent[1].includes('new padel event') && sent[1].includes('Wed 02.12. 19:00') && sent[1].includes('Padeldome Erdberg'));

// the same event arriving by e-mail later is not announced twice
assert.strictEqual(api.saveEvent(sandbox.PropertiesService.getScriptProperties(), { id: '1008', title: 'Wednesday Padel Night', date: '2026-12-02', time: '19:00', venue: '', price: '€10', going: '5', url: '' }, true), 'updated');

// manual "send all now"
api.sendAllPadelEventsNow();
assert(sent[sent.length - 1].includes('all upcoming padel events (5)'));
console.log('test_meetup_calendar OK');
