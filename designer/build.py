"""Builds the Agent Step Designer artboards (canvas screens) from shared pieces, so the top bar,
step list and flow graph stay identical across screens. Output: canvas/project/*.dc.html

Run: uv run --no-project --python 3.13 python designer/build.py
FreeForm.dc.html is hand-made (not generated) and is left untouched."""

from pathlib import Path

OUT = Path(__file__).parent / "canvas" / "project"   # mirrors the canvas artifact's project/ folder
ACC = "{{accent}}"

INK, MUTED, FAINT, LINE, SOFT = "#1F2430", "#4A4A42", "#8A8A80", "#E3E3DD", "#F8F8F5"
KIND = {  # step kind -> (pill bg, pill text)
    "ask": ("#EEF3F9", "#1F3E63"),
    "built-in": ("#EDEDE8", "#4A4A42"),
    "approve": ("#FBEED8", "#8A5A00"),
    "act": ("#E1F5EE", "#085041"),
    "record": ("#EFEAFB", "#5B3FA8"),
    "trigger": ("#EDEDE8", "#6B6B63"),
}

ICON = {
    "clock": '<circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M3 7l9 6 9-6"></path>',
    "layers": '<path d="M12 3l9 5-9 5-9-5 9-5z"></path><path d="M3 13l9 5 9-5"></path>',
    "shield": '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"></path><path d="M9 12l2 2 4-4"></path>',
    "person": '<circle cx="12" cy="8" r="4"></circle><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"></path>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"></rect><path d="M3 10h18"></path><path d="M8 3v4"></path><path d="M16 3v4"></path>',
    "record": '<rect x="4" y="3" width="16" height="18" rx="2"></rect><path d="M8 8h8"></path><path d="M8 12h8"></path><path d="M8 16h5"></path>',
    "plus": '<path d="M12 5v14"></path><path d="M5 12h14"></path>',
    "down": '<path d="M12 5v14"></path><path d="M19 12l-7 7-7-7"></path>',
    "check": '<path d="M5 12l5 5L20 7"></path>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"></rect><path d="M8 11V7a4 4 0 0 1 8 0v4"></path>',
    "alert": '<path d="M12 3l10 18H2L12 3z"></path><path d="M12 10v5"></path><path d="M12 18v.5"></path>',
}


def icon(name, size=15, color=FAINT, width=1.8):
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICON[name]}</svg>')


def pill(kind, text=None):
    bg, fg = KIND[kind]
    return (f'<span style="font-size: 10px; font-weight: 600; color: {fg}; background: {bg}; border-radius: 4px; '
            f'padding: 2px 6px; flex: none">{text or kind}</span>')


# ------------------------------------------------------------------ the workflow

READERS = [
    ("air", "Read airline emails", "Main.dc.html"),
    ("hotel", "Read hotel emails", None),
    ("portal", "Read portal &amp; car emails", None),
]
STEPS = [  # (id, label, kind, icon, link)
    ("tidy", "Tidy up", "built-in", "layers", "Tidy.dc.html"),
    ("verify", "Double-check bookings", "ask", "shield", "Verify.dc.html"),
    ("approve", "Approve trips", "approve", "person", "Approve.dc.html"),
    ("calendar", "Add to calendar", "act", "calendar", "Calendar.dc.html"),
]


def side_row(label, kind, ico, selected, href=None):
    border = f"1.5px solid {ACC}" if selected else f"1px solid {LINE}"
    bg = "#EEF3F9" if selected else "#FFFFFF"
    weight = "600" if selected else "400"
    color = INK if selected else MUTED
    stroke = ACC if selected else FAINT
    inner = (f'<span style="display: flex; align-items: center; gap: 8px; min-width: 0">{icon(ico, 15, stroke)}'
             f'<span style="font-size: 13px; font-weight: {weight}; color: {color}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis">{label}</span></span>'
             f'{pill(kind)}')
    style = (f"width: 100%; box-sizing: border-box; display: flex; align-items: center; justify-content: space-between; gap: 8px; "
             f"padding: 9px 10px; background: {bg}; border: {border}; border-radius: 8px; text-decoration: none")
    if href:
        return f'<a href="{href}" style="{style}">{inner}</a>'
    return f'<div style="{style}">{inner}</div>'


def section_label(text):
    return (f'<span style="font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; '
            f'color: {FAINT}; padding: 0 4px">{text}</span>')


def sidebar(selected):
    readers = "".join(side_row(label, "ask", "mail", sel_id == selected, href)
                      for sel_id, label, href in READERS)
    steps = "".join(side_row(label, kind, ico, sid == selected, href) for sid, label, kind, ico, href in STEPS)
    return f'''
    <div style="width: 248px; flex: none; box-sizing: border-box; padding: 18px 14px; background: {SOFT}; border-right: 1px solid {LINE}; display: flex; flex-direction: column; gap: 8px; overflow-y: auto">
      {section_label("Trigger")}
      {side_row("Every weekday · 7:00", "trigger", "clock", False)}
      <div style="display: flex; align-items: center; justify-content: space-between; padding: 10px 4px 0 4px">
        <span style="font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: {FAINT}">Steps</span>
        <button type="button" aria-label="Add step" style="width: 22px; height: 22px; display: flex; align-items: center; justify-content: center; background: #FFFFFF; border: 1px solid #D8D8D0; border-radius: 6px; color: {MUTED}; cursor: pointer">{icon("plus", 14, "currentColor", 2)}</button>
      </div>
      <div style="display: flex; flex-direction: column; gap: 6px; padding: 8px; border: 1px dashed #C9C9BF; border-radius: 10px">
        <span style="font-size: 10.5px; font-weight: 600; color: {FAINT}">Run together</span>
        {readers}
      </div>
      {steps}
      <div style="padding: 10px 4px 0 4px">{section_label("Data")}</div>
      {side_row("Booking", "record", "record", selected == "booking", "Booking.dc.html")}
    </div>'''


def topbar():
    return f'''
  <div style="height: 64px; flex: none; display: flex; align-items: center; justify-content: space-between; padding: 0 24px; background: #FFFFFF; border-bottom: 1px solid {LINE}">
    <div style="display: flex; align-items: center; gap: 10px">
      <span style="font-size: 13px; color: #6B6B63">Agent Service</span>
      <span style="font-size: 13px; color: #B7B7AC">/</span>
      <span style="font-size: 15px; font-weight: 600">travel-sync</span>
      <span style="font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: #8A5A00; background: #FBEED8; padding: 3px 8px; border-radius: 20px">Draft</span>
    </div>
    <div style="display: flex; align-items: center; gap: 10px">
      <button type="button" style="font: inherit; font-size: 13px; font-weight: 600; color: {INK}; background: #FFFFFF; border: 1px solid #D8D8D0; border-radius: 8px; padding: 8px 14px; cursor: pointer">Test on sample emails</button>
      <button type="button" style="font: inherit; font-size: 13px; font-weight: 600; color: #FFFFFF; background: {ACC}; border: none; border-radius: 8px; padding: 8px 16px; cursor: pointer">Publish</button>
    </div>
  </div>'''


def access_summary():
    return f'''
      <div style="width: 100%; box-sizing: border-box; display: flex; align-items: flex-start; gap: 10px; padding: 12px 14px; background: #FFFFFF; border: 1px solid {LINE}; border-radius: 10px">
        {icon("lock", 16, MUTED)}
        <p style="margin: 0; font-size: 12.5px; line-height: 1.5; color: {MUTED}; text-wrap: pretty"><strong style="color: {INK}">What this agent can do:</strong> read emails from 25 travel senders from the last 180 days, and create events on your calendar after you approve. It cannot send, delete or change anything else. Up to $0.50 per run.</p>
      </div>'''


def arrow():
    return icon("down", 15, "#B7B7AC", 2)


def node(label, caption, kind, ico, selected, width=210):
    if selected:
        box = f"background: #EEF3F9; border: 2px solid {ACC}; box-shadow: 0 4px 14px rgba(31,62,99,0.14)"
        stroke = ACC
    else:
        box = f"background: #FFFFFF; border: 1px solid {LINE}"
        stroke = FAINT
    return f'''<div style="width: {width}px; box-sizing: border-box; padding: 11px 12px; {box}; border-radius: 10px; display: flex; flex-direction: column; align-items: center; gap: 4px; text-align: center">
          <div style="display: flex; align-items: center; gap: 6px">{icon(ico, 16, stroke)}<span style="font-size: 12.5px; font-weight: 700; color: {INK}">{label}</span></div>
          <div style="display: flex; align-items: center; gap: 6px">{pill(kind)}<span style="font-size: 11px; color: {FAINT}">{caption}</span></div>
        </div>'''


def flow(selected):
    captions = {"air": "Haiku · airline senders", "hotel": "Haiku · hotel senders", "portal": "Haiku · portal senders"}
    readers = "".join(node(label.replace(" emails", ""), captions[rid], "ask", "mail", rid == selected, 188)
                      for rid, label, _ in READERS)
    step_caps = {"tidy": "no model", "verify": "Opus · cited emails only", "approve": "you, by email or web",
                 "calendar": "create only"}
    steps = f"\n        {arrow()}\n        ".join(node(label, step_caps[sid], kind, ico, sid == selected)
                                                for sid, label, kind, ico, _ in STEPS)
    return f'''
    <div style="flex: 1; min-width: 0; box-sizing: border-box; padding: 20px 28px; display: flex; flex-direction: column; align-items: center; gap: 7px; overflow: auto">
      {access_summary()}
      <div style="height: 6px"></div>
      <div style="padding: 7px 14px; background: #FFFFFF; border: 1px solid {LINE}; border-radius: 10px; display: flex; align-items: center; gap: 6px; opacity: 0.8">{icon("clock", 14)}<span style="font-size: 11.5px; font-weight: 600; color: {MUTED}">Every weekday · 7:00</span></div>
      {arrow()}
      <div style="display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 10px 12px 12px 12px; border: 1.5px dashed #C9C9BF; border-radius: 12px">
        <span style="font-size: 10.5px; font-weight: 600; color: {FAINT}">Run together · each sees only its own senders</span>
        <div style="display: flex; gap: 10px">{readers}</div>
      </div>
      <span style="font-size: 10.5px; color: {FAINT}">list of Booking</span>
      {arrow()}
        {steps}
      <span style="font-size: 10.5px; color: {FAINT}; margin-top: 2px">Only this last step changes anything outside the agent</span>
    </div>'''


# ------------------------------------------------------------------ right-panel pieces

def panel(inner):
    return f'''
    <div style="width: 400px; flex: none; box-sizing: border-box; padding: 20px 22px 18px 22px; background: #FFFFFF; border-left: 1px solid {LINE}; display: flex; flex-direction: column; gap: 14px; overflow-y: auto">
{inner}
      <div style="margin-top: auto; display: flex; gap: 10px; padding-top: 6px">
        <button type="button" style="flex: 1; font: inherit; font-size: 13px; font-weight: 600; color: {INK}; background: #FFFFFF; border: 1px solid #D8D8D0; border-radius: 8px; padding: 9px 0; cursor: pointer">Run test</button>
        <button type="button" style="flex: 1; font: inherit; font-size: 13px; font-weight: 600; color: #FFFFFF; background: {ACC}; border: none; border-radius: 8px; padding: 9px 0; cursor: pointer">Save step</button>
      </div>
    </div>'''


def header(name, kind_selected, note):
    kinds = [("ask", "Ask"), ("built-in", "Built-in"), ("approve", "Approve"), ("act", "Act")]
    btns = "".join(
        f'<button type="button" style="flex: 1; font: inherit; font-size: 12px; font-weight: 600; padding: 7px 0; border-radius: 6px; cursor: pointer; '
        + (f'border: 1.5px solid {ACC}; background: #EEF3F9; color: {INK}">' if k == kind_selected
           else f'border: 1px solid {LINE}; background: #FFFFFF; color: {FAINT}">')
        + f"{lbl}</button>" for k, lbl in kinds)
    return f'''
      <div>
        <label for="stepname" style="display: block; font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: {FAINT}; margin-bottom: 4px">Configure step</label>
        <input id="stepname" type="text" value="{name}" style="width: 100%; box-sizing: border-box; font: inherit; font-size: 17px; font-weight: 700; color: {INK}; padding: 4px 0; border: none; background: transparent">
      </div>
      <div style="display: flex; flex-direction: column; gap: 6px">
        <span style="font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: {FAINT}">Kind of step</span>
        <div style="display: flex; gap: 6px">{btns}</div>
        <span style="font-size: 11.5px; color: #6B6B63; line-height: 1.45">{note}</span>
      </div>'''


def block(title, inner, last=False):
    border = "" if last else f"; padding-bottom: 14px; border-bottom: 1px solid #EDEDE8"
    return f'''
      <div style="display: flex; flex-direction: column; gap: 8px{border}">
        <span style="font-size: 12px; font-weight: 600; color: {MUTED}">{title}</span>
        {inner}
      </div>'''


def connection(name, access, ico="mail"):
    return f'''<div style="display: flex; align-items: center; gap: 10px; padding: 9px 12px; background: {SOFT}; border: 1px solid {LINE}; border-radius: 8px">
          {icon(ico, 16, MUTED)}
          <span style="flex: 1; display: flex; flex-direction: column"><span style="font-size: 13px; font-weight: 600">{name}</span><span style="font-size: 11px; color: {FAINT}">{access}</span></span>
          <span style="width: 7px; height: 7px; border-radius: 50%; background: #2E8B57"></span>
          <span style="font-size: 11px; color: #2E6B47; font-weight: 600">Connected</span>
        </div>'''


def chips(items, more=None):
    tags = "".join(f'<span style="font-size: 11.5px; color: {MUTED}; background: {SOFT}; border: 1px solid {LINE}; border-radius: 20px; padding: 3px 9px">{i}</span>' for i in items)
    if more:
        tags += f'<button type="button" style="font: inherit; font-size: 11.5px; font-weight: 600; color: {ACC}; background: none; border: none; padding: 3px 4px; cursor: pointer">{more}</button>'
    return f'<div style="display: flex; flex-wrap: wrap; gap: 6px">{tags}</div>'


def textarea(tid, text, rows=4):
    return (f'<textarea id="{tid}" rows="{rows}" aria-label="Instructions" style="width: 100%; box-sizing: border-box; font: inherit; font-size: 12.5px; line-height: 1.5; '
            f'color: {INK}; padding: 9px 11px; border: 1px solid {LINE}; border-radius: 8px; background: #FCFCFA; resize: none">{text}</textarea>')


def select(sid, label, options):
    opts = "".join(f"<option>{o}</option>" for o in options)
    return (f'<div style="display: flex; flex-direction: column; gap: 5px"><label for="{sid}" style="font-size: 11.5px; color: #6B6B63">{label}</label>'
            f'<select id="{sid}" style="font: inherit; font-size: 12.5px; padding: 7px 8px; border: 1px solid {LINE}; border-radius: 6px; background: #FCFCFA; color: {INK}">{opts}</select></div>')


def checkbox(cid, text, detail=None, checked=True):
    det = f'<span style="display: block; font-size: 11px; color: {FAINT}; margin-top: 1px">{detail}</span>' if detail else ""
    return (f'<div style="display: flex; align-items: flex-start; gap: 9px"><input id="{cid}" type="checkbox"{" checked" if checked else ""} '
            f'style="margin: 2px 0 0 0; width: 15px; height: 15px; accent-color: #2F5D9F; flex: none">'
            f'<label for="{cid}" style="font-size: 12.5px; color: {INK}; line-height: 1.4">{text}{det}</label></div>')


def guarantees(items):
    rows = "".join(f'<div style="display: flex; align-items: flex-start; gap: 8px">{icon("check", 14, "#2E6B47", 2.2)}'
                   f'<span style="font-size: 12px; color: {MUTED}; line-height: 1.45">{t}</span></div>' for t in items)
    return f'<div style="display: flex; flex-direction: column; gap: 7px; padding: 10px 12px; background: #F3F8F4; border: 1px solid #D5E7DA; border-radius: 8px">{rows}</div>'


def record_ref(text):
    return (f'<a href="Booking.dc.html" style="display: flex; align-items: center; gap: 8px; padding: 9px 12px; border: 1px solid {LINE}; border-radius: 8px; '
            f'text-decoration: none; background: #FFFFFF">{icon("record", 15, "#5B3FA8")}<span style="flex: 1; font-size: 12.5px; color: {INK}">{text}</span>'
            f'<span style="font-size: 11.5px; font-weight: 600; color: {ACC}">Open</span></a>')


# ------------------------------------------------------------------ page shell

def page(title, sidebar_sel, center, right):
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="./support.js"></script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">
</head>
<body>
<x-dc>
<helmet>
<style>
body{{margin:0;font-family:'IBM Plex Sans',system-ui,sans-serif;background:#F1F1EE}}
a{{color:{ACC}}}
a:hover{{color:#1F3E63}}
::placeholder{{color:#8A8A80}}
</style>
</helmet>
<div style="width: 1440px; height: 900px; box-sizing: border-box; display: flex; flex-direction: column; background: #F1F1EE; color: {INK}; overflow: hidden">
{topbar()}
  <div style="flex: 1; min-height: 0; display: flex">
{sidebar(sidebar_sel)}
{center}
{right}
  </div>
</div>
</x-dc>
<script type="text/x-dc" data-dc-script data-props='{{"accent":{{"editor":"color","default":"#2F5D9F"}},"$preview":{{"width":1440,"height":900}}}}'>
class Component extends DCLogic {{
  renderVals() {{
    return {{ accent: this.props.accent ?? '#2F5D9F' }};
  }}
}}
</script>
</body>
</html>
'''


# ------------------------------------------------------------------ the screens

def read_airlines():
    right = panel(
        header("Read airline emails", "ask", "Ask: a model reads and extracts. It can never change anything outside the agent.")
        + block("Model", select("model", "Routine extraction: a fast model is enough", ["Claude Haiku 4.5 · fast, low cost", "Claude Sonnet 5", "Claude Opus 5"]))
        + block("Connection and limits", connection("Gmail", "Read only · cannot send or delete")
                + f'<span style="font-size: 11.5px; color: #6B6B63">Senders this step can read</span>'
                + chips(["united.com", "delta.com", "aa.com", "southwest.com", "alaskaair.com"], "+5 more")
                + '<div style="display: flex; align-items: center; gap: 8px"><label for="lookback" style="font-size: 11.5px; color: #6B6B63">Look back</label>'
                  f'<input id="lookback" type="text" value="180" style="width: 52px; font: inherit; font-size: 12.5px; padding: 5px 7px; border: 1px solid {LINE}; border-radius: 6px; background: #FCFCFA"><span style="font-size: 11.5px; color: #6B6B63">days</span></div>')
        + block("Instructions", textarea("instr", "Find every flight booking in these emails. Return each flight leg as its own booking, with departure and arrival in the airport&#39;s local time zone. Use only what the email says; never guess a confirmation number or time.", rows=3))
        + block("Returns", record_ref("A list of <strong>Booking</strong>"))
        + block("Checked by the service, not the model", guarantees([
            "Opens every email whose subject looks like a booking. If it skips one, it is retried once with those emails named.",
            "Every email found is either used or explained.",
            "Times without a time zone are rejected.",
        ]), last=True))
    return page("Read airline emails", "air", flow("air"), right)


def tidy():
    right = panel(
        header("Tidy up", "built-in", "Built-in: fixed rules, no model. The same bookings always give the same trips.")
        + block("Takes", record_ref("Bookings from all three readers"))
        + block("Clean up",
                checkbox("dedupe", "Remove duplicates", "Same Booking identity, even if two readers found it")
                + checkbox("past", "Drop trips that have ended")
                + checkbox("group", "Group into trips", "Same confirmation number, or dates within 1 day"))
        + block("Add notes for the approver",
                checkbox("g1", "Hotel booked but no flight")
                + checkbox("g2", "Flights more than a day apart with no hotel")
                + checkbox("g3", "Only one flight found")
                + checkbox("g4", "A booking doesn&#39;t list you as a traveler")
                + f'<div style="display: flex; flex-direction: column; gap: 5px; padding-left: 24px"><label for="me" style="font-size: 11.5px; color: #6B6B63">Your name as it appears on bookings</label>'
                  f'<input id="me" type="text" value="Alex Rivera" style="font: inherit; font-size: 12.5px; padding: 6px 8px; border: 1px solid {LINE}; border-radius: 6px; background: #FCFCFA"></div>')
        + block("Returns", f'<div style="font-size: 12.5px; color: {INK}; line-height: 1.5">A list of trips: each with its bookings, dates, and notes.</div>', last=True))
    return page("Tidy up", "tidy", flow("tidy"), right)


def verify():
    right = panel(
        header("Double-check bookings", "ask", "Ask: a second model re-reads the source emails. It did not see the readers&#39; reasoning.")
        + block("Model", select("vmodel", "Use a stronger model than the readers, so the check is independent", ["Claude Opus 5 · thorough", "Claude Sonnet 5", "Claude Haiku 4.5"]))
        + block("Connection and limits", connection("Gmail", "Read only")
                + f'<div style="display: flex; align-items: center; gap: 8px; font-size: 12px; color: {MUTED}">{icon("lock", 14, MUTED)}Can open only the emails the readers cited. It cannot search.</div>')
        + block("Instructions", textarea("vinstr", "For each booking, open its source email and compare every field: dates, times and time zones, flight numbers, confirmation and travelers. Do not trust the extracted values.", rows=3))
        + block("Returns for each booking",
                chips(["Confirmed", "Mismatch", "Not found"])
                + f'<span style="font-size: 11.5px; color: #6B6B63">Mismatches list each field that differs, e.g. &quot;flight number: extracted UA1523, email says UA1532&quot;.</span>')
        + block("Where results go", f'<span style="font-size: 12.5px; color: {INK}; line-height: 1.5">Shown on the approval screen. A trip with any mismatch is not pre-selected.</span>', last=True))
    return page("Double-check bookings", "verify", flow("verify"), right)


def approve():
    trips = [
        ("Tampa", "Nov 19 – 23", "Flight UA1523 · Hilton Tampa Downtown · Flight UA2218", "3 of 3 confirmed", None),
        ("Dallas / Fort Worth", "Oct 5 – 6", "Dallas/Fort Worth Airport Marriott", "1 of 1 confirmed", "Hotel booked but no flight"),
        ("Hong Kong", "Dec 10 – 11", "Flight UA877", "1 of 1 confirmed", "Traveler is Sam Rivera, not you"),
    ]
    rows = "".join(
        f'<div style="display: flex; flex-direction: column; gap: 3px; padding: 9px 11px; border: 1px solid {LINE}; border-radius: 8px; background: #FFFFFF">'
        f'<div style="display: flex; justify-content: space-between; gap: 8px"><span style="font-size: 12.5px; font-weight: 700">{t}</span><span style="font-size: 11.5px; color: #6B6B63">{d}</span></div>'
        f'<span style="font-size: 11.5px; color: {MUTED}">{b}</span>'
        f'<span style="font-size: 11px; color: #2E6B47; font-weight: 600">{c}</span>'
        + (f'<span style="display: flex; align-items: center; gap: 5px; font-size: 11px; color: #8A4B00">{icon("alert", 12, "#8A4B00")}{n}</span>' if n else "")
        + "</div>" for t, d, b, c, n in trips)
    choices = "".join(
        f'<div style="display: flex; align-items: center; gap: 9px; padding: 8px 10px; border: 1px solid {LINE}; border-radius: 8px; background: {bg}">'
        f'<span style="width: 20px; height: 20px; flex: none; display: flex; align-items: center; justify-content: center; border-radius: 50%; background: #EDEDE8; font-size: 11px; font-weight: 700; color: {MUTED}">{i}</span>'
        f'<span style="flex: 1; font-size: 12.5px; color: {INK}">{c}</span>{tag}</div>'
        for i, (c, bg, tag) in enumerate([
            ("Add nothing", "#FFFFFF", f'<span style="font-size: 10.5px; font-weight: 600; color: #6B6B63">if no answer</span>'),
            ("Add verified trips only", "#FFFFFF", ""),
            ("Pick trips", "#FFFFFF", ""),
        ], 1))
    right = panel(
        header("Approve trips", "approve", "Approve: a person decides before anything outside the agent changes.")
        + f'''
      <div style="display: flex; gap: 9px; padding: 10px 12px; background: #FDF6EA; border: 1px solid #EBD3A6; border-radius: 8px">
        {icon("lock", 16, "#8A5A00")}
        <span style="font-size: 12px; color: #5C3D00; line-height: 1.45"><strong>Required.</strong> This agent reads email written by other people and adds events to your calendar, so a person must approve in between.</span>
      </div>'''
        + block("Who approves", select("approver", "Approver", ["You (Alex Rivera)"])
                + checkbox("n1", "Email me a link") + checkbox("n2", "Show in the web app"))
        + block("What the approver sees", f'<div style="display: flex; flex-direction: column; gap: 6px">{rows}</div>')
        + block("Choices, in this order", choices
                + f'<div style="display: flex; align-items: center; gap: 8px"><label for="wait" style="font-size: 11.5px; color: #6B6B63">If nobody answers within</label>'
                  f'<input id="wait" type="text" value="24" style="width: 44px; font: inherit; font-size: 12.5px; padding: 5px 7px; border: 1px solid {LINE}; border-radius: 6px; background: #FCFCFA"><span style="font-size: 11.5px; color: #6B6B63">hours: add nothing</span></div>',
                last=True))
    return page("Approve trips", "approve", flow("approve"), right)


def calendar():
    mapping = "".join(
        f'<div style="display: flex; gap: 10px; padding: 8px 10px; border: 1px solid {LINE}; border-radius: 8px">'
        f'<span style="width: 58px; flex: none; font-size: 12px; font-weight: 600; color: {INK}">{k}</span>'
        f'<span style="display: flex; flex-direction: column; gap: 2px"><span style="font-size: 12px; color: {INK}">{t}</span><span style="font-size: 11px; color: {FAINT}">{d}</span></span></div>'
        for k, t, d in [
            ("Flight", "Flight UA1523 SFO → TPA", "Timed event, in each airport&#39;s time zone"),
            ("Hotel", "Hotel: Hilton Tampa Downtown", "All-day, check-in to check-out"),
            ("Car", "Car rental: Hertz", "All-day, pick-up to drop-off"),
        ])
    right = panel(
        header("Add to calendar", "act", "Act: changes something outside the agent. No model: it uses only checked fields from approved trips.")
        + block("Connection", connection("Google Calendar", "Create events only · cannot edit or delete", "calendar")
                + select("cal", "Calendar", ["Personal", "Travel"]))
        + block("Each booking becomes", mapping)
        + block("Never add duplicates",
                checkbox("d1", "Skip bookings this agent already added", "Matched by the booking&#39;s identity, so re-runs add nothing")
                + checkbox("d2", "Skip bookings already on your calendar", "e.g. added by Gmail: same days and the same flight number or hotel name"))
        + block("Test mode", checkbox("dry", "Dry run: list the events without creating them", "On until this version is published"), last=True))
    return page("Add to calendar", "calendar", flow("calendar"), right)


def booking():
    core = [
        ("type", "Choice: flight, hotel, car", "What was booked, not where the email came from"),
        ("confirmation", "Text", "Exactly as written in the email"),
        ("provider", "Text", "e.g. United. Shown to people, never used to match"),
        ("travelers", "List of names", "Used to flag other people&#39;s trips"),
        ("start", "Date and time with time zone", "Departure, check-in or pick-up"),
        ("end", "Date and time with time zone", "Arrival, check-out or drop-off"),
        ("destination", "Text", "Airport code or city"),
        ("source email", "Email reference", "Lets Double-check re-open it"),
    ]
    rows = "".join(
        f'<div style="display: grid; grid-template-columns: 150px 220px minmax(0, 1fr); gap: 12px; padding: 9px 14px; border-top: 1px solid #EDEDE8; align-items: baseline">'
        f'<span style="font-size: 12.5px; font-weight: 600; color: {INK}; font-family: ui-monospace, Menlo, monospace">{n}</span>'
        f'<span style="font-size: 12px; color: {MUTED}">{t}</span><span style="font-size: 12px; color: #6B6B63">{d}</span></div>'
        for n, t, d in core)
    per_type = "".join(
        f'<div style="display: flex; flex-direction: column; gap: 6px; padding: 12px 14px; background: #FFFFFF; border: 1px solid {LINE}; border-radius: 10px">'
        f'<span style="display: flex; align-items: center; gap: 6px"><span style="font-size: 11px; color: #6B6B63">when type is</span>{pill("record", k)}</span>'
        + "".join(f'<span style="font-size: 12.5px; color: {INK}; font-family: ui-monospace, Menlo, monospace">{f}</span>' for f in fs)
        + "</div>" for k, fs in [("flight", ["flight number", "from airport"]), ("hotel", ["hotel name", "address"]), ("car", ["pick-up location", "address"])])
    center = f'''
    <div style="flex: 1; min-width: 0; box-sizing: border-box; padding: 24px 28px; display: flex; flex-direction: column; gap: 16px; overflow: auto">
      <div style="display: flex; flex-direction: column; gap: 6px">
        <div style="display: flex; align-items: center; gap: 10px">{icon("record", 20, "#5B3FA8")}<h1 style="margin: 0; font-size: 22px; font-weight: 700">Booking</h1>{pill("record", "record type")}</div>
        <p style="margin: 0; font-size: 13px; line-height: 1.5; color: {MUTED}; max-width: 620px; text-wrap: pretty">One booking found in an email. Every step after the readers works with this one shape, whatever kind of booking it is, so adding a new kind later doesn&#39;t change them.</p>
      </div>
      <div style="background: #FFFFFF; border: 1px solid {LINE}; border-radius: 10px; overflow: hidden">
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 11px 14px">
          <span style="font-size: 13px; font-weight: 700">Shared fields</span><span style="font-size: 11.5px; color: #6B6B63">every booking has these</span>
        </div>
        {rows}
      </div>
      <div style="display: flex; flex-direction: column; gap: 8px">
        <span style="font-size: 13px; font-weight: 700">Extra fields by type</span>
        <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px">{per_type}</div>
      </div>
      <div style="display: flex; flex-direction: column; gap: 6px; padding: 12px 14px; background: #FFFFFF; border: 1px solid {LINE}; border-radius: 10px">
        <span style="font-size: 13px; font-weight: 700">Same booking when</span>
        <span style="font-size: 12.5px; color: {INK}">type + confirmation + flight number (or start date) match</span>
        <span style="font-size: 12px; color: #6B6B63; line-height: 1.5">Not the provider name: a reader wrote &quot;United&quot; one day and &quot;United Airlines&quot; the next, and the same flights were added twice.</span>
      </div>
    </div>'''
    used = "".join(
        f'<div style="display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; border: 1px solid {LINE}; border-radius: 8px">'
        f'<span style="display: flex; align-items: center; justify-content: space-between; gap: 8px"><span style="font-size: 12.5px; font-weight: 600">{s}</span>{pill(k)}</span>'
        f'<span style="font-size: 11.5px; color: #6B6B63">{w}</span></div>'
        for s, k, w in [
            ("3 readers", "ask", "Create bookings"),
            ("Tidy up", "built-in", "Identity, dates, travelers"),
            ("Double-check bookings", "ask", "Every field, against the source email"),
            ("Add to calendar", "act", "Times, flight number, hotel name"),
        ])
    right = f'''
    <div style="width: 400px; flex: none; box-sizing: border-box; padding: 20px 22px 18px 22px; background: #FFFFFF; border-left: 1px solid {LINE}; display: flex; flex-direction: column; gap: 14px; overflow-y: auto">
      <span style="font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: {FAINT}">Record type</span>
      {block("Used by", f'<div style="display: flex; flex-direction: column; gap: 6px">{used}</div>')}
      {block("Checked by the service after every step", guarantees([
          "Every time has a time zone.",
          "type is one of flight, hotel, car.",
          "Unknown fields are rejected.",
      ]), last=True)}
      <div style="margin-top: auto; display: flex; gap: 10px; padding-top: 6px">
        <button type="button" style="flex: 1; font: inherit; font-size: 13px; font-weight: 600; color: {INK}; background: #FFFFFF; border: 1px solid #D8D8D0; border-radius: 8px; padding: 9px 0; cursor: pointer">Add field</button>
        <button type="button" style="flex: 1; font: inherit; font-size: 13px; font-weight: 600; color: #FFFFFF; background: {ACC}; border: none; border-radius: 8px; padding: 9px 0; cursor: pointer">Save record type</button>
      </div>
    </div>'''
    return page("Booking record type", "booking", center, right)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, fn in [("Main", read_airlines), ("Tidy", tidy), ("Verify", verify), ("Approve", approve),
                     ("Calendar", calendar), ("Booking", booking)]:
        html = fn()
        (OUT / f"{name}.dc.html").write_text(html)
        print(f"{name}.dc.html  {len(html):,} bytes")
