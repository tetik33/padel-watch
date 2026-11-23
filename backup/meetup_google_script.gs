/**
 * Meetup e-mail alerts -> Telegram ("padelcheck" bot) + list of upcoming padel events
 *
 * What it does (every 5 minutes, no Gmail label or filter needed):
 *  - reads the event calendar of your Meetup groups (GROUPS below, every 30 minutes): this finds EVERY
 *    scheduled event, also the ones Meetup does not e-mail you about
 *  - reads Gmail messages from meetup.com
 *  - keeps only events whose TITLE mentions "padel" (the "More events from this group" part of a mail
 *    is read too, so other padel events in the same mail are collected)
 *  - sends a Telegram message for every NEW padel event (once per event)
 *  - keeps a list of upcoming padel events (date, time, venue, price, people going, link)
 *  - publishes that list at a private web address; the Telegram button "Meetup events" reads it
 *
 * Setup (once):
 *  1. script.google.com -> New project -> paste this whole file -> Save (Ctrl+S).
 *  2. Project Settings (gear) -> Script properties -> add:
 *       TELEGRAM_BOT_TOKEN = your bot token
 *       TELEGRAM_CHAT_ID   = your chat id
 *  3. Choose the function "setup" -> Run -> allow access. It creates the 5-minute trigger and
 *     writes a private key into the Script properties (MEETUP_KEY).
 *  3b. Put the address part of your Meetup groups into GROUPS below (meetup.com/<this part>/).
 *  4. Choose "checkMeetup" -> Run. The first run reads the last 60 days of Meetup mails and fills the list
 *     silently (only mails from the last 2 days send a Telegram message).
 *  5. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone -> Deploy.
 *     Copy the Web app URL and add  ?key=<MEETUP_KEY>  at its end (the key is in Script properties).
 *     That full address goes into GitHub as the secret MEETUP_EVENTS_URL (see the guide).
 */

// Your Meetup groups: the part of the address after meetup.com/ , e.g. meetup.com/vienna-sports-meetup/
const GROUPS = ["vienna-sports-meetup"];
const CALENDAR_EVERY_MIN = 30;    // the group calendars are read at most every 30 minutes (like a calendar app)

const SEARCH = "from:meetup.com";
const KEYWORDS = ["padel"];       // an event counts when its title contains one of these words
const ALERT_DAYS = 2;             // normal runs look at mails of the last N days
const IMPORT_DAYS = 60;           // the very first run reads this many days to fill the list
const TZ = "Europe/Vienna";
// The Telegram menu "Mute" writes the file mute.txt in your GitHub repo. While "meetup" is muted there, this script
// waits and sends the new events when the mute ends. Put "" here to switch the mute check off.
const MUTE_URL = "https://raw.githubusercontent.com/tetik33/padel-watch/main/state/mute.txt";

const MONTHS = { january: 1, february: 2, march: 3, april: 4, may: 5, june: 6, july: 7, august: 8,
                 september: 9, october: 10, november: 11, december: 12 };
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DATE_RE = /(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})/i;
const TIME_RE = /(\d{1,2}):(\d{2})\s*([AP]M)/i;
const LINK_RE = /https?:\/\/(?:www\.)?meetup\.com\/[^\/"'\s<>]+\/events\/(\d+)[^"'\s<>]*/i;

// ---------------------------------------------------------------- setup
function setup() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === "checkMeetup") ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger("checkMeetup").timeBased().everyMinutes(5).create();
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty("MEETUP_KEY")) {
    props.setProperty("MEETUP_KEY", Utilities.getUuid().replace(/-/g, ""));
  }
  Logger.log("Trigger created: checkMeetup runs every 5 minutes. The private key is stored as MEETUP_KEY in Script properties.");
}

// ---------------------------------------------------------------- main run (every 5 minutes)
function checkMeetup() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty("TELEGRAM_BOT_TOKEN");
  const chat = props.getProperty("TELEGRAM_CHAT_ID");
  if (!token || !chat) { Logger.log("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Script properties."); return; }

  props.setProperty("LAST_CHECK", new Date().toISOString());   // shown in the Telegram status
  const firstRun = !props.getProperty("INIT");
  const mutedSince = Number(props.getProperty("MUTED_SINCE") || 0);
  if (!firstRun && isMuted()) {                                 // muted in Telegram: leave the mails for later
    if (!mutedSince) props.setProperty("MUTED_SINCE", String(Date.now()));
    Logger.log("Meetup alerts are muted. Mails stay unread by the script until the mute ends.");
    return;
  }
  let alertFrom = Date.now() - ALERT_DAYS * 86400000;
  if (mutedSince && mutedSince - 3600000 < alertFrom) alertFrom = mutedSince - 3600000;   // after a long mute: look back to its start
  const days = firstRun ? IMPORT_DAYS : Math.max(ALERT_DAYS, Math.ceil((Date.now() - alertFrom) / 86400000) + 1);

  const messages = [];
  GmailApp.search(`${SEARCH} newer_than:${days}d`, 0, 100).forEach(th => th.getMessages().forEach(m => messages.push(m)));
  messages.sort((a, b) => a.getDate() - b.getDate());          // oldest first

  let failed = false;
  for (const m of messages) {
    let main = null, extra = [];
    try { main = parseMain(m); extra = parseMore(m); } catch (e) { Logger.log("Could not read a mail: " + e); continue; }

    const notPast = ev => !ev.date || ev.date >= today();       // events that are over are ignored
    for (const ev of extra) {                                   // other events listed in the mail: stored silently
      if (matches(ev.title) && notPast(ev)) saveEvent(props, ev, false);
    }
    if (main && matches(main.title) && notPast(main)) {
      const isNew = saveEvent(props, main, true) === "new";
      if (isNew && m.getDate().getTime() >= alertFrom) {
        if (!sendToTelegram(token, chat, buildText(main))) {    // not sent: forget it, try again at the next run
          props.deleteProperty("ev_" + main.id);
          failed = true;
          break;
        }
      }
    }
  }
  if (!failed) failed = checkCalendars(props, token, chat, false);
  purgePast(props);
  if (!failed) {
    props.setProperty("INIT", "1");
    props.deleteProperty("MUTED_SINCE");
  }
}

// True while the Telegram menu has muted "meetup" (reads mute.txt from GitHub; any problem = not muted)
function isMuted() {
  if (!MUTE_URL) return false;
  try {
    const r = UrlFetchApp.fetch(MUTE_URL + "?t=" + Date.now(), { muteHttpExceptions: true });
    if (r.getResponseCode() !== 200) return false;
    return r.getContentText().split("\n").some(line => {
      const p = line.trim().split(/\s+/);
      return p.length === 2 && p[0] === "meetup" && new Date(p[1]).getTime() > Date.now();
    });
  } catch (e) {
    return false;
  }
}

// ---------------------------------------------------------------- test with a forwarded mail
// Forward a Meetup mail (inline, not as attachment) to this Gmail account, choose "testForwarded" and press Run.
// It reads the newest mail of the last 3 days that mentions Meetup (forwarded ones too), shows what it found in the
// Execution log and sends the result to Telegram marked "TEST", even if the event is already over. Nothing is stored.
function testForwarded() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty("TELEGRAM_BOT_TOKEN");
  const chat = props.getProperty("TELEGRAM_CHAT_ID");
  if (!token || !chat) { Logger.log("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Script properties."); return; }
  const msgs = [];
  GmailApp.search('newer_than:3d (meetup OR "Sports Meetup Vienna")', 0, 10).forEach(th => th.getMessages().forEach(m => msgs.push(m)));
  msgs.sort((a, b) => b.getDate() - a.getDate());                // newest first
  if (!msgs.length) { Logger.log("No mail of the last 3 days mentions Meetup. Forward one to this account first."); return; }
  const m = msgs[0];
  Logger.log("Mail: " + m.getSubject());
  const main = parseMain(m), extra = parseMore(m);
  Logger.log("Main event: " + JSON.stringify(main));
  Logger.log("More events: " + JSON.stringify(extra));
  Logger.log("Counts as padel: " + matches(main.title));
  sendToTelegram(token, chat, "<b>🧪 TEST</b>\n" + buildText(main));
}

// ---------------------------------------------------------------- reading a mail
function htmlToLines(html) {
  const t = String(html || "")
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, " ")
    .replace(/<br\s*\/?>|<\/(p|div|tr|td|th|li|h[1-6]|table)>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ").replace(/&euro;/g, "€").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, "\"").replace(/&#39;|&#x27;/g, "'").replace(/&amp;/g, "&");
  return t.split("\n").map(s => s.replace(/\s+/g, " ").trim()).filter(Boolean);
}

function isoDate(match) {                                        // DATE_RE match -> 2026-02-12
  const mm = MONTHS[match[2].toLowerCase()];
  return match[4] + "-" + String(mm).padStart(2, "0") + "-" + String(match[3]).padStart(2, "0");
}

function time24(match) {                                         // TIME_RE match -> 19:00
  let h = parseInt(match[1], 10) % 12;
  if (match[3].toUpperCase() === "PM") h += 12;
  return String(h).padStart(2, "0") + ":" + match[2];
}

function cleanUrl(u) {
  return u.replace(/[?&](utm_[^&]*|eventOrigin=[^&]*|rv=[^&]*)/g, "").replace(/[?&]$/, "");
}

// The main event of the mail: title from the subject, the rest from the first block of the body.
function parseMain(m) {
  const subject = m.getSubject() || "";
  const q = subject.match(/["“„]([^"”“]+)["”“]/);
  const title = (q ? q[1] : subject).trim();
  const html = m.getBody() || "";
  const lines = htmlToLines(html);

  const di = lines.findIndex(l => DATE_RE.test(l));
  const link = (html.match(LINK_RE) || [])[0] || "";
  const id = link ? link.match(LINK_RE)[1] : "m" + m.getId();
  const ev = { id: id, title: title, date: "", time: "", venue: "", price: "", going: "", url: link ? cleanUrl(link) : "" };

  if (di >= 0) {
    const dm = lines[di].match(DATE_RE);
    ev.date = isoDate(dm);
    const rest = lines[di].slice(dm.index + dm[0].length);
    let ti = -1, tm = rest.match(TIME_RE);
    if (tm) ti = di; else {
      for (let k = di + 1; k <= di + 3 && k < lines.length; k++) {
        tm = lines[k].match(TIME_RE);
        if (tm) { ti = k; break; }
      }
    }
    if (tm) ev.time = time24(tm);
    if (ti >= 0 && ti + 1 < lines.length && !TIME_RE.test(lines[ti + 1])) ev.venue = lines[ti + 1];
    for (let k = Math.max(ti, di); k < Math.min(lines.length, Math.max(ti, di) + 8); k++) {
      const pm = lines[k].match(/€\s?\d+(?:[.,]\d{2})?/);
      if (pm) { ev.price = pm[0].replace(/\s/g, ""); break; }
    }
  }
  const gm = lines.join("\n").match(/(\d+)\s+Members?\s+going/i);
  if (gm) ev.going = gm[1];
  return ev;
}

// "More events from this group": link + title, then "Wednesday, May 14, 2025 8:00 PM · 15 attending"
function parseMore(m) {
  const full = m.getBody() || "";
  const start = full.search(/More events/i);
  if (start < 0) return [];                                      // no such block: nothing to collect
  const html = full.slice(start);
  const out = {};
  const re = /<a\b[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/gi;
  let a;
  while ((a = re.exec(html)) !== null) {
    const lm = a[1].match(LINK_RE);
    if (!lm) continue;
    const title = htmlToLines(a[2]).join(" ").trim();
    if (title.length < 4 || /^(rsvp|attend|going|learn more|view event)/i.test(title)) continue;
    const after = htmlToLines(html.slice(re.lastIndex, re.lastIndex + 600)).join(" ");
    const dm = after.match(DATE_RE);
    if (!dm || dm.index > 120) continue;
    const tail = after.slice(dm.index + dm[0].length, dm.index + dm[0].length + 60);   // only this event's own line
    const tm = tail.match(TIME_RE);
    const am = tail.match(/(\d+)\s+attending/i);
    out[lm[1]] = { id: lm[1], title: title, date: isoDate(dm), time: tm ? time24(tm) : "", venue: "", price: "",
                   going: am ? am[1] : "", url: cleanUrl(a[1].match(LINK_RE)[0]) };
  }
  return Object.keys(out).map(k => out[k]);
}

function matches(title) {
  const t = String(title || "").toLowerCase();
  return KEYWORDS.some(k => t.indexOf(k.toLowerCase()) !== -1);
}

// ---------------------------------------------------------------- the event list
// returns "new", "updated" or "same". Fields that are empty in the new data never erase known ones.
function saveEvent(props, ev, isMain) {
  const key = "ev_" + ev.id;
  const old = props.getProperty(key);
  if (!old) { props.setProperty(key, JSON.stringify(ev)); props.setProperty("LAST_CHANGE", new Date().toISOString()); return "new"; }
  const cur = JSON.parse(old);
  let changed = false;
  Object.keys(ev).forEach(f => {
    if (ev[f] && ev[f] !== cur[f] && (isMain || !cur[f])) { cur[f] = ev[f]; changed = true; }
  });
  if (changed) { props.setProperty(key, JSON.stringify(cur)); props.setProperty("LAST_CHANGE", new Date().toISOString()); }
  return changed ? "updated" : "same";
}

function today() { return Utilities.formatDate(new Date(), TZ, "yyyy-MM-dd"); }

function purgePast(props) {                                      // events of earlier days are removed
  const all = props.getProperties();
  const t = today();
  Object.keys(all).forEach(k => {
    if (k.indexOf("ev_") !== 0) return;
    try {
      const ev = JSON.parse(all[k]);
      if (ev.date && ev.date < t) props.deleteProperty(k);
    } catch (e) { props.deleteProperty(k); }
  });
}

function upcoming(props) {
  const all = props.getProperties();
  const t = today();
  const list = [];
  Object.keys(all).forEach(k => {
    if (k.indexOf("ev_") !== 0) return;
    try { const ev = JSON.parse(all[k]); if (ev.date && ev.date >= t) list.push(ev); } catch (e) {}
  });
  list.sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
  return list;
}

// Private web address: the Telegram listener reads the list here (only with the right key).
function doGet(e) {
  const props = PropertiesService.getScriptProperties();
  const key = props.getProperty("MEETUP_KEY");
  if (!key || !e || !e.parameter || e.parameter.key !== key) {
    return ContentService.createTextOutput("forbidden");
  }
  const clean = s => String(s || "").replace(/[|\n\r]/g, " ").trim();
  const rows = upcoming(props).map(ev =>
    [ev.date, ev.time, clean(ev.title), clean(ev.venue), clean(ev.price), clean(ev.going), clean(ev.url)].join("|"));
  // first line: version, time of the last check and of the last change (the Telegram status shows both)
  const head = "# v1 checked=" + (props.getProperty("LAST_CHECK") || "-") + " changed=" + (props.getProperty("LAST_CHANGE") || "-");
  return ContentService.createTextOutput(head + "\n" + rows.join("\n")).setMimeType(ContentService.MimeType.TEXT);
}

// ---------------------------------------------------------------- Telegram
function dayLabel(iso) {                                         // 2026-02-12 -> Thu 12.02.
  const p = iso.split("-").map(Number);
  const d = new Date(Date.UTC(p[0], p[1] - 1, p[2]));
  return DAYS[d.getUTCDay()] + " " + String(p[2]).padStart(2, "0") + "." + String(p[1]).padStart(2, "0") + ".";
}

function buildText(ev) {
  const title = ev.url ? "<a href=\"" + esc(ev.url) + "\">" + esc(ev.title) + "</a>" : esc(ev.title);
  const lines = ["<b><u>🤝 MEETUP · new padel event</u></b>", ""];
  if (ev.date) lines.push("⏰ <b>" + dayLabel(ev.date) + (ev.time ? " " + ev.time : "") + "</b>");
  lines.push("🏆 <i>" + title + "</i>");
  if (ev.venue) lines.push("📍 <b>" + esc(ev.venue) + "</b>");
  const extra = [];
  if (ev.price) extra.push("💶 " + esc(ev.price));
  if (ev.going) extra.push("👥 " + esc(ev.going) + " going");
  if (extra.length) lines.push(extra.join(" · "));
  return lines.join("\n");
}

function sendToTelegram(token, chat, text) {
  const res = UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/sendMessage", {
    method: "post",
    muteHttpExceptions: true,
    payload: { chat_id: chat, text: text.slice(0, 4000), parse_mode: "HTML", disable_web_page_preview: "true" }
  });
  if (res.getResponseCode() !== 200) {
    Logger.log("Telegram error " + res.getResponseCode() + ": " + res.getContentText().slice(0, 200));
    return false;
  }
  return true;
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------- Meetup group calendars (iCal)
// Meetup publishes each group's events as a calendar (the "add to calendar" feed that calendar apps read).
// It lists every scheduled event, also those Meetup does not announce by e-mail.

function fetchCalendar(group) {                                  // returns the iCal text, or null
  const url = "https://www.meetup.com/" + encodeURIComponent(group) + "/events/ical/";
  const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, followRedirects: true });
  const text = res.getContentText() || "";
  if (res.getResponseCode() !== 200 || text.indexOf("BEGIN:VCALENDAR") === -1) {
    Logger.log("Calendar of " + group + " could not be read (" + res.getResponseCode() + ").");
    return null;
  }
  return text;
}

function parseIcal(text) {                                       // iCal text -> list of events
  const unfolded = String(text).replace(/\r?\n[ \t]/g, "");
  const unesc = v => v.replace(/\\n/gi, " ").replace(/\\([,;\\])/g, "$1").trim();
  const events = [];
  unfolded.split(/BEGIN:VEVENT/).slice(1).forEach(block => {
    const body = block.split(/END:VEVENT/)[0];
    const field = name => {
      const m = body.match(new RegExp("^" + name + "(;[^:\\r\\n]*)?:(.*)$", "m"));
      return m ? { params: m[1] || "", value: unesc(m[2]) } : null;
    };
    const summary = field("SUMMARY"), start = field("DTSTART");
    if (!summary || !start) return;
    const url = field("URL"), uid = field("UID"), loc = field("LOCATION"), status = field("STATUS");
    if (status && /CANCELLED/i.test(status.value)) return;
    const idm = ((url && url.value) || "").match(/\/events\/(\d+)/) || ((uid && uid.value) || "").match(/(\d{5,})/);
    let date = "", time = "";
    const dm = start.value.match(/^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(?:\d{2})?)?(Z)?/);
    if (dm) {
      if (dm[6]) {                                               // UTC time -> Vienna time
        const d = new Date(Date.UTC(+dm[1], +dm[2] - 1, +dm[3], +(dm[4] || 0), +(dm[5] || 0)));
        date = Utilities.formatDate(d, TZ, "yyyy-MM-dd");
        time = dm[4] ? Utilities.formatDate(d, TZ, "HH:mm") : "";
      } else {
        date = dm[1] + "-" + dm[2] + "-" + dm[3];
        time = dm[4] ? dm[4] + ":" + dm[5] : "";
      }
    }
    const venue = loc ? loc.value.split(" (")[0].split(",")[0].trim() : "";
    events.push({ id: idm ? idm[1] : "c" + date + summary.value.slice(0, 20), title: summary.value, date: date, time: time,
                  venue: venue, price: "", going: "", url: url ? cleanUrl(url.value) : "" });
  });
  return events;
}

// Reads all group calendars (at most every CALENDAR_EVERY_MIN minutes, or always when forced).
// New padel events send a message; the very first time, ONE message lists all of them.
// Returns true when a Telegram message could not be sent (it is tried again next time).
function checkCalendars(props, token, chat, force) {
  const last = Number(props.getProperty("LAST_CALENDAR") || 0);
  if (!force && Date.now() - last < CALENDAR_EVERY_MIN * 60000) return false;
  const firstTime = !props.getProperty("CALENDAR_INIT");
  let read = 0;
  for (const group of GROUPS) {
    const text = fetchCalendar(group);
    if (text === null) continue;
    read++;
    for (const ev of parseIcal(text)) {
      if (!matches(ev.title) || !ev.date || ev.date < today()) continue;
      if (saveEvent(props, ev, true) === "new" && !firstTime) {
        if (!sendToTelegram(token, chat, buildText(ev))) {        // not sent: forget it, try again next time
          props.deleteProperty("ev_" + ev.id);
          return true;
        }
      }
    }
  }
  if (!read) return false;
  props.setProperty("LAST_CALENDAR", String(Date.now()));
  if (firstTime) {
    if (!sendToTelegram(token, chat, buildList(upcoming(props)))) return true;
    props.setProperty("CALENDAR_INIT", "1");
  }
  return false;
}

// One message with all upcoming padel events
function buildList(list) {
  const lines = ["<b><u>🤝 MEETUP · all upcoming padel events (" + list.length + ")</u></b>"];
  if (!list.length) lines.push("", "No padel events scheduled right now.");
  for (const ev of list) {
    const title = ev.url ? "<a href=\"" + esc(ev.url) + "\">" + esc(ev.title) + "</a>" : esc(ev.title);
    const block = "\n⏰ <b>" + dayLabel(ev.date) + (ev.time ? " " + ev.time : "") + "</b>\n🏆 <i>" + title + "</i>"
                  + (ev.venue ? "\n📍 " + esc(ev.venue) : "");
    if (lines.join("\n").length + block.length > 3900) { lines.push("", "…and more (button \"Meetup events\")"); break; }
    lines.push(block);
  }
  return lines.join("\n");
}

// Run this by hand to get ALL upcoming padel events now, as one Telegram message.
function sendAllPadelEventsNow() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty("TELEGRAM_BOT_TOKEN"), chat = props.getProperty("TELEGRAM_CHAT_ID");
  if (!token || !chat) { Logger.log("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Script properties."); return; }
  props.setProperty("CALENDAR_INIT", "1");                       // the list below replaces the automatic first list
  checkCalendars(props, token, chat, true);
  purgePast(props);
  const ok = sendToTelegram(token, chat, buildList(upcoming(props)));
  Logger.log(ok ? "Sent the list of " + upcoming(props).length + " padel event(s)." : "Sending failed: check the bot token.");
}
