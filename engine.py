"""Evidence-based disruption decisions on a read-only imported fleet plan.

The engine checks what the source can establish. Missing operational facts stay
pending until a planner explicitly records a received confirmation.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone

LINES = {
    'Prag': {'id': 'Prag', 'name': 'Prague', 'route': 'Brussels – Amsterdam – Berlin – Prague', 'limit': 15, 'train_numbers': ['453', '452']},
    'Paris': {'id': 'Paris', 'name': 'Paris', 'route': 'Paris – Brussels – Hamburg – Berlin', 'limit': 13, 'train_numbers': ['475', '474']},
    'Milan': {'id': 'Milan', 'name': 'Milan', 'route': 'Brussels – Cologne – Switzerland – Milan', 'limit': 13, 'train_numbers': ['401', '400']},
}
PRODUCTS = {'1': 'Classic couchette', '2': 'Seating', '3': 'Comfort Standard', '4': 'Comfort Plus'}
ACTIVE_CODES = {'1', '2', '3', '4', '9', '0', '-1'}
COMMERCIAL_CODES = {'1', '2', '3', '4'}
CASE_TYPES = {
    'car_unavailable': {'id': 'car_unavailable', 'label': 'Car unavailable', 'description': 'Compare replacement and recovery actions for a failed or unavailable car.', 'requires_car': True},
    'maintenance_change': {'id': 'maintenance_change', 'label': 'Maintenance change', 'description': 'Assess a changed repair or maintenance commitment and replacement needs.', 'requires_car': True},
    'capacity_change': {'id': 'capacity_change', 'label': 'Capacity or booking change', 'description': 'Review additional capacity, product changes and passenger recovery.', 'requires_car': False},
    'service_delay': {'id': 'service_delay', 'label': 'Service delay', 'description': 'Coordinate preparation, departure and passenger consequences.', 'requires_car': False},
    'resource_unavailable': {'id': 'resource_unavailable', 'label': 'Resource unavailable', 'description': 'Review a missing shunter, track, operator or preparation resource.', 'requires_car': False},
    'formation_change': {'id': 'formation_change', 'label': 'Formation change', 'description': 'Assess a requested change to physical train order or carriage allocation.', 'requires_car': False},
    'workshop_release': {'id': 'workshop_release', 'label': 'Workshop release', 'description': 'Plan collection of a released car through an existing service or transport.', 'requires_car': True},
    'other': {'id': 'other', 'label': 'Other operational change', 'description': 'Record a change and build a reviewed manual proposal.', 'requires_car': False},
}


class APIError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_case_schema(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS cases (
      id TEXT PRIMARY KEY, line TEXT NOT NULL, date TEXT NOT NULL,
      car_id TEXT NOT NULL, reason TEXT NOT NULL, affected_passengers INTEGER,
      status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS updates (
      id TEXT PRIMARY KEY, case_id TEXT NOT NULL, department TEXT NOT NULL,
      message TEXT NOT NULL, option_id TEXT, confirmations TEXT NOT NULL,
      revoked_confirmations TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
      FOREIGN KEY(case_id) REFERENCES cases(id));
    CREATE TABLE IF NOT EXISTS decisions (
      case_id TEXT PRIMARY KEY, option_id TEXT NOT NULL, rationale TEXT NOT NULL,
      approved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
      FOREIGN KEY(case_id) REFERENCES cases(id));
    CREATE TABLE IF NOT EXISTS case_events (
      id TEXT PRIMARY KEY, case_id TEXT NOT NULL, kind TEXT NOT NULL,
      detail TEXT NOT NULL, created_at TEXT NOT NULL,
      FOREIGN KEY(case_id) REFERENCES cases(id));
    CREATE INDEX IF NOT EXISTS updates_case ON updates(case_id,created_at);
    CREATE TABLE IF NOT EXISTS custom_options (
      id TEXT PRIMARY KEY, case_id TEXT NOT NULL, proposal TEXT NOT NULL,
      created_at TEXT NOT NULL, FOREIGN KEY(case_id) REFERENCES cases(id));
    ''')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(cases)')}
    if 'case_type' not in columns:
        conn.execute("ALTER TABLE cases ADD COLUMN case_type TEXT NOT NULL DEFAULT 'car_unavailable'")
    if 'context' not in columns:
        conn.execute("ALTER TABLE cases ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
    conn.commit()


def event(conn, case_id, kind, detail):
    conn.execute('INSERT INTO case_events VALUES(?,?,?,?,?)',
                 (uuid.uuid4().hex, case_id, kind, json.dumps(detail), now()))


def dict_rows(conn, query, params=()):
    cursor = conn.execute(query, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def metadata(conn):
    try:
        rows = conn.execute('SELECT key,value FROM metadata').fetchall()
    except sqlite3.OperationalError:
        return {'imported': False, 'warnings': ['No fleet workbook has been imported.']}
    result = {}
    for key, value in rows:
        try:
            result[key] = json.loads(value)
        except (ValueError, TypeError):
            result[key] = value
    result.setdefault('imported', bool(rows))
    result.setdefault('warnings', [])
    return result


def validate_line(line):
    if not isinstance(line, str):
        raise APIError('Choose Prague, Paris or Milan.')
    aliases = {'Prague': 'Prag', 'prague': 'Prag', 'prag': 'Prag', 'paris': 'Paris', 'milan': 'Milan'}
    line = aliases.get(str(line), line)
    if line not in LINES:
        raise APIError('Choose Prague, Paris or Milan.')
    return line


def validate_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise APIError('Use a date in YYYY-MM-DD format.')
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise APIError('The selected date is invalid.') from exc
    return value


def normalize_car(value):
    if not isinstance(value, (str, int, float)):
        raise APIError('A carriage identifier is required.')
    text = ' '.join(str(value).strip().split())
    if not text or len(text) > 70:
        raise APIError('A valid carriage identifier is required.')
    # UIC full identifier -> the five-digit planning identifier.
    full = re.search(r'\b\d{2}\s*\d{2}\s*\d{2}\s*[- ]\s*(\d{2})\s*(\d{3})\s*-\s*\d\b', text)
    if full:
        return f'{full.group(1)} {full.group(2)}'
    if re.fullmatch(r'\d{5}', text):
        return text[:2] + ' ' + text[2:]
    if re.fullmatch(r'\d+\.0', text):
        text = text[:-2]
    return text


def code_text(code):
    if code is None:
        return ''
    if isinstance(code, (int, float)) and float(code).is_integer():
        return str(int(code))
    value = str(code).strip()
    return value[:-2] if re.fullmatch(r'-?\d+\.0', value) else value


def numeric_or_text(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (ValueError, TypeError):
        return value


def nominal_capacity(car_type, product):
    """Nominal capacity only; it is not a booking or usable-berth guarantee."""
    product = code_text(product)
    ctype = re.sub(r'[^a-z0-9]', '', str(car_type).lower())
    if product in ('4', 'Comfort Plus'):
        return 30 if ctype.startswith('wl30') else 33 if ctype.startswith('wl33') else None
    if product in ('3', 'Comfort Standard'):
        if ctype.startswith('bdcm'):
            return 18
        return 30 if any(ctype.startswith(x) for x in ('bvcmz', 'bocmh')) else None
    if product in ('1', 'Classic couchette'):
        if ctype.startswith('bdcm'):
            return 36
        if ctype.startswith('bvcmz5'):
            return 50
        if ctype.startswith(('bvcmz6', 'bocmh6')):
            return 60
        return None
    if product in ('2', 'Seating'):
        if ctype.startswith('am66'):
            return 66
        if ctype.startswith('bm72'):
            return 72
        if ctype.startswith('bimdz'):
            return 55
        if ctype.startswith(('bimz', 'bomdz', 'bvcmz', 'bocmh')):
            return 60
        if ctype.startswith('bdcm'):
            return 36
    return None


def make_check(check_id, label, status, evidence):
    return {'id': check_id, 'label': label, 'status': status, 'evidence': evidence}


def country_approval(car, line):
    owner = str(car.get('owner') or '').upper()
    ctype = str(car.get('type') or '').upper()
    car_id = str(car.get('id') or car.get('car_id') or '')
    if line == 'Milan' and owner == 'EE':
        return make_check('country_approval', 'Country approvals', 'fail', 'Rulebook: EuroExpress cars may not run to Italy.')
    if line == 'Milan' and owner == 'GFF' and re.search(r'309$', car_id.replace(' ', '')):
        return make_check('country_approval', 'Country approvals', 'fail', 'Rulebook: GFF car 309 may not run to Italy.')
    if line == 'Paris' and ctype.startswith('BDCM'):
        return make_check('country_approval', 'Country approvals', 'fail', 'Rulebook: BDCM cars may not run to France.')
    if owner == 'RDC':
        return make_check('country_approval', 'Country approvals', 'pass', 'Rulebook: RDC cars have no listed country restriction.')
    if owner in ('EE', 'GFF'):
        return make_check('country_approval', 'Country approvals', 'pass', 'No applicable country restriction in the supplied rulebook. Individual technical acceptance is checked separately.')
    return make_check('country_approval', 'Country approvals', 'pending', f'Approvals for owner {owner or "unknown"} are not established by the supplied rulebook.')


def product_compatibility(car, product):
    capacity = nominal_capacity(car.get('type'), product)
    return make_check('product', 'Car type supports the product', 'pass' if capacity is not None else 'fail',
                      f'{car.get("type")} supports {PRODUCTS.get(str(product), product)}; nominal capacity {capacity}. Booking-level usability remains unconfirmed.'
                      if capacity is not None else f'No documented {PRODUCTS.get(str(product), product)} capacity for {car.get("type")}.')


def public_fleet_record(row):
    item = dict(row)
    item['code'] = numeric_or_text(item.get('code'))
    item['product'] = PRODUCTS.get(code_text(item['code']))
    item['source'] = {'sheet': item.pop('source_sheet', item.get('line')), 'cell': item.pop('source_cell', None)}
    code = code_text(item['code'])
    meaning = str(item.get('colour_meaning') or '').lower()
    if 'not usable' in meaning or code.lower() == 'rev':
        item['status'] = 'unavailable'
    elif code in COMMERCIAL_CODES:
        item['status'] = 'commercial'
    elif code == '9':
        item['status'] = 'empty in train'
    elif code in {'0', '-1', '-2'}:
        item['status'] = {'0': 'shunted in', '-1': 'shunted out', '-2': 'separate transfer'}[code]
    elif code.upper() == 'X' or item.get('location'):
        item['status'] = 'reserve / parked'
    elif 'workshop' in meaning or 'frist' in meaning:
        item['status'] = 'maintenance attention'
    else:
        item['status'] = 'status requires review'
    return item


def _fleet_rows(conn, line=None, selected_date=None):
    where, params = [], []
    if line:
        where.append('f.line=?'); params.append(line)
    if selected_date:
        where.append('f.date=?'); params.append(selected_date)
    return dict_rows(conn, '''SELECT f.*, c.owner, c.type FROM fleet f
                            JOIN cars c ON c.id=f.car_id''' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY f.car_id,f.line', params)


def _source(row):
    return f'{row.get("source_sheet", row.get("line"))}!{row.get("source_cell", "")}'


def _formation_checks(rows, line):
    active = [row for row in rows if code_text(row.get('code')) in ACTIVE_CODES]
    issues = []
    for row in active:
        check = country_approval(row, line)
        if check['status'] != 'pass':
            issues.append({'severity': 'error' if check['status'] == 'fail' else 'warning', 'rule': 'country_approval', 'message': f'{row["car_id"]}: {check["evidence"]}', 'source': _source(row)})
        meaning = str(row.get('colour_meaning') or '').lower()
        if 'not usable' in meaning or code_text(row.get('code')).lower() == 'rev':
            issues.append({'severity': 'error', 'rule': 'unusable_status', 'message': f'{row["car_id"]} has an unavailable status while assigned to the train.', 'source': _source(row)})
    fixed_count = sum(code_text(r.get('code')) in COMMERCIAL_CODES | {'9'} for r in active)
    if len(active) > LINES[line]['limit']:
        hard = fixed_count > LINES[line]['limit']
        issues.append({'severity': 'error' if hard else 'warning', 'rule': 'train_length', 'message': f'{len(active)} cars appear across the day ({fixed_count} fixed assignments); the route limit is {LINES[line]["limit"]}. ' + ('Fixed assignments alone exceed the limit.' if hard else 'Shunted-in/out cars require segment-level formation review; this daily union does not establish an overlength segment.')})
    return active, issues


def get_bootstrap(conn):
    meta = metadata(conn)
    cars = dict_rows(conn, 'SELECT id,owner,type FROM cars ORDER BY id') if meta.get('imported') else []
    return {'meta': meta, 'lines': list(LINES.values()), 'cars': cars, 'case_types': list(CASE_TYPES.values())}


def get_plan(conn, line, selected_date):
    line, selected_date = validate_line(line), validate_date(selected_date)
    raw = _fleet_rows(conn, line, selected_date)
    days = dict_rows(conn, 'SELECT * FROM line_days WHERE line=? AND date=?', (line, selected_date))
    active, issues = _formation_checks(raw, line)
    capacity = {name: 0 for name in PRODUCTS.values()}
    capacity_unknown = []
    for row in active:
        code = code_text(row.get('code'))
        if code in PRODUCTS:
            value = nominal_capacity(row.get('type'), code)
            if value is None:
                capacity_unknown.append(row['car_id'])
            else:
                capacity[PRODUCTS[code]] += value
    all_rows = _fleet_rows(conn, selected_date=selected_date)
    same_day = {}
    for row in all_rows:
        if code_text(row.get('code')) in COMMERCIAL_CODES | {'9'}:
            same_day.setdefault(row['car_id'], []).append(row['line'])
    for car_id in sorted({row['car_id'] for row in active}):
        other = [x for x in same_day.get(car_id, []) if x != line]
        if other:
            issues.append({'severity': 'warning', 'rule': 'same_day_allocation', 'message': f'{car_id} is also assigned on {", ".join(other)} this date. Exact timings must establish whether an exchange is possible.'})
    if active:
        issues.append({'severity': 'info', 'rule': 'unverified_constraints', 'message': 'Door orientation, power condition, occupied-car shunting, booking-level capacity and exact transfer times are not structured in the imported grid. They require confirmation.'})
    if not days:
        issues.append({'severity': 'warning', 'rule': 'missing_source_date', 'message': 'No planning row was found for this line and date.'})
    if capacity_unknown:
        issues.append({'severity': 'warning', 'rule': 'unknown_capacity', 'message': 'Nominal capacity is not defined for: ' + ', '.join(capacity_unknown)})
    running_rows = dict_rows(conn, 'SELECT * FROM running WHERE line=? AND date=?', (line, selected_date))
    running = running_rows[0] if running_rows else None
    if running:
        for key in ('departure_delay', 'arrival_delay'):
            running[key] = numeric_or_text(running.get(key))
    route = LINES[line]['route']
    if line == 'Milan' and selected_date >= '2026-12-13':
        route = 'Brussels – Breda – Eindhoven – Cologne – Switzerland – Milan'
    return {'line': line, 'date': selected_date, 'route': route, 'cars': [public_fleet_record(row) for row in raw],
            'statistics': {'commercial_cars': sum(code_text(r.get('code')) in COMMERCIAL_CODES for r in raw),
                           'physical_car_count': len(active), 'route_car_limit': LINES[line]['limit'],
                           'nominal_capacity': capacity, 'capacity_unknown': capacity_unknown,
                           'passengers': days[0]['pax'] if days else None},
            'issues': issues, 'running': running,
            'source_notes': days[0]['notes'] if days else '',
            'formation_order_verified': False}


def get_car(conn, car_id, selected_date):
    car_id, selected_date = normalize_car(car_id), validate_date(selected_date)
    cars = dict_rows(conn, 'SELECT id,owner,type FROM cars WHERE id=?', (car_id,))
    if not cars:
        raise APIError('Carriage not found in the imported fleet.', 404)
    rows = [row for row in _fleet_rows(conn, selected_date=selected_date) if row['car_id'] == car_id]
    return {'car': cars[0], 'date': selected_date, 'assignments': [public_fleet_record(row) for row in rows],
            'note': 'These are date-level source records, not live location tracking.'}


def _length_check(rows, line):
    count = sum(code_text(r.get('code')) in ACTIVE_CODES for r in rows)
    limit = LINES[line]['limit']
    fixed_count = sum(code_text(r.get('code')) in COMMERCIAL_CODES | {'9'} for r in rows)
    if fixed_count > limit:
        return make_check('formation_length', 'Train-length limits', 'fail', f'{count} cars across the date exceed the route limit of {limit}.')
    if any(code_text(r.get('code')) in {'0', '-1'} for r in rows):
        return make_check('formation_length', 'Train-length limits', 'pending', f'Date-level count is {count}, limit {limit}; shunted-in/out cars require segment-level formation checks.')
    return make_check('formation_length', 'Train-length limits', 'pass', f'{count} cars in the scenario formation; route limit {limit}. Physical ordering is checked separately.')


def _remaining_check(rows, line):
    _, issues = _formation_checks(rows, line)
    errors = [x['message'] for x in issues if x['severity'] == 'error' and x['rule'] != 'train_length']
    unknown = [x['message'] for x in issues if x['severity'] == 'warning']
    return make_check('remaining_formation', 'Other cars remain permitted', 'fail' if errors else 'pending' if unknown else 'pass',
                      '; '.join(errors or unknown) if errors or unknown else 'No listed country or unusable-status violation among the remaining assigned cars.')


def _finish_option(option, confirmations):
    explicit = (confirmations or {}).get(option['id'], {})
    for check in option['checks']:
        # A human confirmation may resolve an unknown, never overwrite source-backed failure.
        if check['status'] == 'pending' and check['id'] in explicit:
            check['status'] = 'pass'
            check['evidence'] = 'Planner-recorded received confirmation: ' + str(explicit[check['id']])
    failed = [c for c in option['checks'] if c['status'] == 'fail']
    pending = [c for c in option['checks'] if c['status'] == 'pending']
    option['status'] = 'blocked' if failed else 'conditional' if pending else 'ready'
    option['blockers'] = [c['label'] + ': ' + c['evidence'] for c in failed + pending]
    if any(c['id'] == 'costs' and c['status'] == 'pass' for c in option['checks']):
        option['cost_label'] = 'Accepted in a recorded update; see evidence'
    return option


def _replacement_options(conn, case, confirmations=None):
    """Return candidate actions, with explicit pass/pending/fail evidence.

    confirmations has shape {option_id: {pending_check_id: evidence_text}}.
    Ordering is an evidence/availability heuristic, not a cost optimum.
    """
    line, selected_date = validate_line(case['line']), validate_date(case['date'])
    affected_id = normalize_car(case['car_id'])
    target_rows = _fleet_rows(conn, line, selected_date)
    source = next((r for r in target_rows if r['car_id'] == affected_id), None)
    if source is None or code_text(source.get('code')) not in ACTIVE_CODES:
        raise APIError('The affected carriage is not assigned to this service on the selected date.')
    product = code_text(source.get('code'))
    remaining = [r for r in target_rows if r['car_id'] != affected_id]
    all_rows = _fleet_rows(conn, selected_date=selected_date)
    by_car = {}
    for row in all_rows:
        by_car.setdefault(row['car_id'], []).append(row)
    catalogue = dict_rows(conn, 'SELECT id,owner,type FROM cars ORDER BY id')
    candidates = []
    for car in catalogue:
        if car['id'] == affected_id:
            continue
        # Do not present replacements with no defined product compatibility.
        if nominal_capacity(car.get('type'), product) is None:
            continue
        records = by_car.get(car['id'], [])
        target_record = next((r for r in records if r['line'] == line), None)
        active_assignments = [r for r in records if code_text(r.get('code')) in ACTIVE_CODES]
        if any(r['line'] == line for r in active_assignments):
            continue  # Existing cars in this formation are not spare capacity.
        reserve_records = [r for r in records if code_text(r.get('code')).upper() == 'X' or r.get('location')]
        revisions = [r for r in records if code_text(r.get('code')).lower() == 'rev']
        approvals = country_approval(car, line)
        bad_colour = target_record and 'not usable' in str(target_record.get('colour_meaning') or '').lower()
        # A different-line red status often represents country eligibility; do not
        # incorrectly turn it into a fleet-wide technical defect.
        checks = [product_compatibility(car, product), approvals]
        if bad_colour:
            checks.append(make_check('source_status', 'Source permits this car on the line', 'fail', f'{_source(target_record)} is coloured "not usable" for this line.'))
        elif revisions:
            checks.append(make_check('source_status', 'Source permits this car to operate', 'fail', 'Revision / out-of-service recorded at ' + ', '.join(_source(r) for r in revisions)))
        else:
            checks.append(make_check('source_status', 'No recorded unavailable block', 'pass', 'No target-line "not usable" colour or revision code is recorded on this date. This does not establish technical release.'))
        if active_assignments:
            checks.append(make_check('availability', 'Release from existing assignment', 'pending',
                                     'Assigned on ' + ', '.join(r['line'] + ' at ' + _source(r) for r in active_assignments) + '. Exact timings and replacement of that service require confirmation.'))
        elif reserve_records:
            checks.append(make_check('availability', 'Reserve / parked evidence', 'pass', ', '.join(_source(r) + ': ' + code_text(r.get('code')) for r in reserve_records)))
        else:
            checks.append(make_check('availability', 'Car availability', 'pending', 'A blank or absent entry is not proof of availability.'))
        locations = sorted({str(r['location']) for r in records if r.get('location')})
        checks.append(make_check('location', 'Exchange location and transfer window', 'pending',
                                 ('Recorded location(s): ' + ', '.join(locations) + '. ' if locations else 'No exact location is established. ') + 'Confirm the physical exchange point, route, arrival and available time.'))
        new_row = {**car, 'car_id': car['id'], 'line': line, 'date': selected_date, 'code': product, 'colour_meaning': '', 'source_sheet': 'Scenario', 'source_cell': ''}
        checks += [_length_check(remaining + [new_row], line), _remaining_check(remaining, line)]
        checks += [
            make_check('technical_release', 'Technical release and power condition', 'pending', 'ECM/maintenance release and working heating/high voltage are not structured in the workbook.'),
            make_check('orientation', 'Door orientation and physical order', 'pending', 'Check sleeper door placement, usable exits and final physical carriage order.'),
            make_check('booking_match', 'Booked product and compartments', 'pending', 'Match actual berth/compartment bookings, passenger car number, staff compartments and blocked capacity. Aggregate PAX is insufficient.'),
            make_check('preparation', 'Cleaning and logistics time', 'pending', 'Confirm that the chosen car can be prepared before departure; cleaning requires at least three hours.'),
            make_check('shunting', 'TCS acceptance and shunting slot', 'pending', 'Confirm the operator, track access, shunter and exchange slot.'),
            make_check('passenger_clearance', 'No passengers aboard during shunting', 'pending', 'Operational confirmation is needed before moving occupied-service cars.'),
            make_check('onward_commitments', 'Onward services and maintenance', 'pending', 'Check that using this spare does not break another service, mattress-car commitment or maintenance deadline.'),
            make_check('costs', 'Transport and shunting cost accepted', 'pending', 'No quote is provided for this proposed movement. No cost saving has been calculated.'),
        ]
        score = 0 if reserve_records else 30
        score += 100 if active_assignments else 0
        score += 1000 if any(c['status'] == 'fail' for c in checks) else 0
        if locations:
            score -= 5
        candidates.append((score, car['id'], {
            'id': 'replace:' + car['id'], 'title': 'Replace with ' + car['id'], 'kind': 'replacement',
            'description': f'{car["owner"]} {car["type"]}; preserves the {PRODUCTS.get(product, "required")} product if the pending checks are confirmed.',
            'car_id': car['id'], 'rank': 0,
            'passenger_impact': 'No product change intended; booking match not yet confirmed',
            'movement': ('Move from ' + ', '.join(locations) if locations else 'Location and transport route to be confirmed') + '; shunting required',
            'cost_label': 'Unknown; operator quote required', 'checks': checks,
        }))
    candidates.sort(key=lambda item: (item[0], item[1]))
    options = []
    for _, _, option in candidates[:6]:
        option['rank'] = len(options) + 1
        options.append(_finish_option(option, confirmations))
    affected_passengers = case.get('affected_passengers')
    reduced = {
        'id': 'reduced', 'title': 'Run a reduced formation and recover bookings', 'kind': 'reaccommodation',
        'description': 'Remove the unavailable car. Prepare a booking-level recovery plan before deciding who can travel and under which product.',
        'car_id': None, 'rank': len(options) + 1,
        'passenger_impact': f'Up to {affected_passengers} passengers; booking recovery not confirmed' if affected_passengers is not None else 'Affected passenger count unknown; booking-level recovery required',
        'movement': 'Remove the unavailable car; no replacement transport assumed',
        'cost_label': 'Unknown; recovery, downgrades and possible compensation',
        'checks': [_length_check(remaining, line), _remaining_check(remaining, line),
                   make_check('capacity_recovery', 'Booking-level recovery plan', 'pending', 'Remaining usable berths and private/shared compartments must be matched to bookings. Do not treat nominal capacities as free inventory.'),
                   make_check('customer_acceptance', 'Customer service confirmation', 'pending', 'Confirm proposed passenger moves, downgrades, cancellations and any required customer responses.'),
                   make_check('es_authority', 'ES operational decision', 'pending', 'Obtain the ES duty decision where passengers are affected.'),
                   make_check('orientation', 'Door orientation and final formation', 'pending', 'Removal changes neighbours and endpoints; recheck usable exits and physical order.'),
                   make_check('passenger_clearance', 'No passengers aboard during shunting', 'pending', 'Confirm the empty-car removal and the shunting arrangement.'),
                   make_check('preparation', 'Preparation and departure timing', 'pending', 'Confirm removal and preparation can fit the departure window.'),
                   make_check('onward_commitments', 'Onward services and maintenance', 'pending', 'Check subsequent departures, capacity commitments and workshop transport for the failed car.'),
                   make_check('costs', 'Recovery and removal costs accepted', 'pending', 'No recovery or shunting estimate has been supplied.')],
    }
    options.append(_finish_option(reduced, confirmations))
    return options


GENERIC_CHECKS = {
    'impact_verified': ('Operational impact confirmed', 'Confirm affected departures, cars, passenger commitments and any linked changes. The case description is a reported change, not verified impact analysis.'),
    'timing': ('Operating and preparation window', 'Confirm exact train direction, arrival/departure times, preparation buffer and the decision deadline. Date-level records are insufficient.'),
    'partner_confirmation': ('Partner acceptance and resources', 'Record the relevant operator, workshop, infrastructure or service partner’s acceptance and available resources.'),
    'costs': ('Costs and consequences accepted', 'Obtain the relevant movement, preparation and passenger-recovery estimates. Unknown amounts are not zero.'),
    'onward_commitments': ('Onward commitments checked', 'Check later services, booked products, mattress-car commitments, maintenance and staffing affected by the change.'),
    'formation': ('Full formation and route rules checked', 'Review country approvals, length on every route segment, door orientation, power continuity and passenger car-number/product mapping. The free-text proposal has not been automatically validated.'),
    'technical_release': ('Technical release and power condition', 'Confirm maintenance/ECM release and usable heating, high voltage and technical condition for the proposed operation.'),
    'passenger_clearance': ('Safe empty-car shunting', 'Confirm there will be no passengers aboard during shunting, and that staff, track access and the movement sequence are accepted.'),
    'preparation': ('Cleaning and logistics arrangement', 'Confirm staff, access, cleaning of at least three hours and the required logistics can fit the revised window.'),
    'booking_match': ('Booking-level product and compartment match', 'Check usable berths/seats, compartment groups, staff compartments, blocked capacity and passenger car numbers. Aggregate PAX and nominal capacities do not establish spare inventory.'),
    'customer_acceptance': ('Customer recovery confirmed', 'Confirm any customer notifications, moves, downgrades, cancellations, alternative transport and required customer responses.'),
    'es_authority': ('ES operational decision recorded', 'Obtain the ES duty decision where passengers or the operated service are affected.'),
    'resource_alternative': ('Alternative resource confirmed', 'Confirm the replacement shunter, staff, operator, track or preparation resource is actually available.'),
    'track_access': ('Track and exchange access', 'Confirm the proposed location, infrastructure permissions, route paths, shunting capacity and access restrictions.'),
    'maintenance_deadline': ('Maintenance schedule accepted', 'Obtain ECM/workshop acceptance of the changed maintenance, mileage or release commitment. A planner cannot extend an ECM deadline unilaterally.'),
    'workshop_collection': ('Collection and technical acceptance', 'Confirm the exact car, release, technical acceptance, collection date and the operator’s handover arrangement.'),
    'weekend_storage': ('Workshop storage and collection day', 'Workshops close at weekends. Confirm a permitted collection/drop-off day and that the car will not be left outside for weekend collection.'),
    'transfer_window': ('Transfer and arrival before preparation', 'Confirm the physical location, transport route, locomotive/driver/escort, running window and arrival before the preparation deadline.'),
    'scope_of_change': ('Requested change made concrete', 'Document the exact before/after cars, locations, product numbers, timings and responsible departments. This is a manual proposal requiring operational review.'),
}


def _template_checks(conn, case, keys, *, operates_car=False):
    days = dict_rows(conn, 'SELECT source_sheet,source_row FROM line_days WHERE line=? AND date=?', (case['line'], case['date']))
    checks = [make_check('source_context', 'Imported planning context', 'pass' if days else 'pending',
                         f'{days[0]["source_sheet"]}!row {days[0]["source_row"]}: imported date-level plan. This is not a live timetable.'
                         if days else 'No imported line/date row is available. Supply and verify the relevant plan context.')]
    if operates_car and case.get('car_id'):
        cars = dict_rows(conn, 'SELECT id,owner,type FROM cars WHERE id=?', (case['car_id'],))
        if cars:
            checks.append(country_approval(cars[0], case['line']))
    for key in dict.fromkeys(keys):
        label, evidence = GENERIC_CHECKS[key]
        checks.append(make_check(key, label, 'pending', evidence))
    return checks


def _context(case):
    context = case.get('context') or {}
    if isinstance(context, str):
        try:
            context = json.loads(context)
        except ValueError:
            context = {}
    return context


def _generic_options(conn, case, confirmations=None):
    case_type = case.get('case_type', 'other')
    context = _context(case)
    common = ['impact_verified', 'timing', 'partner_confirmation', 'onward_commitments', 'costs']
    operating = ['formation', 'technical_release', 'passenger_clearance', 'preparation', 'booking_match']
    recovery = ['booking_match', 'customer_acceptance', 'es_authority']
    definitions = {
        'service_delay': [
            ('replan_window', 'Replan preparation and departure', 'Agree a workable preparation window and revised departure with operators and service teams.', 'Revised timing; no car movement assumed', common + ['preparation', 'booking_match', 'customer_acceptance', 'es_authority']),
            ('alternate_formation', 'Assess an alternative formation', 'Consider a different prepared formation or staged preparation. Exact cars and operating times must be specified.', 'Car exchanges may be required; not yet specified', common + operating + ['resource_alternative', 'track_access']),
            ('passenger_recovery', 'Prepare passenger recovery', 'Assess rerouting, re-accommodation or cancellations when the service cannot meet its commitments.', 'Passenger and service plan to be confirmed', common + recovery),
        ],
        'resource_unavailable': [
            ('alternative_resource', 'Find another resource or operating window', 'Seek an accepted replacement resource or a later window at a permitted location.', 'Resource and exact movement to be confirmed', common + operating + ['resource_alternative', 'track_access']),
            ('revise_movement', 'Revise the movement or exchange location', 'Prepare another movement sequence or exchange location and obtain infrastructure/operator acceptance.', 'Revised movement sequence; not automatically routed', common + operating + ['track_access', 'transfer_window']),
            ('defer_nonessential', 'Defer a nonessential movement', 'Assess whether the movement can be deferred without breaking maintenance, passenger or onward-service commitments.', 'Defer only after the affected commitment is checked', common + ['formation', 'booking_match', 'maintenance_deadline', 'es_authority']),
        ],
        'formation_change': [
            ('requested_change', 'Review the requested formation change', 'Make the before/after carriage order and passenger-number mapping explicit, then check the complete formation.', 'Requested reordering or car exchange', common + operating + ['scope_of_change', 'track_access']),
            ('retain_and_defer', 'Retain the present formation and defer', 'Assess keeping the imported formation while deferring the requested change. Existing source issues still require review.', 'No requested change performed', common + ['formation', 'technical_release', 'booking_match', 'maintenance_deadline', 'es_authority']),
            ('stage_change', 'Stage the change at a later permitted point', 'Assess a staged change using a feasible location and an accepted empty-car movement sequence.', 'Staged exchange; exact point and timing unconfirmed', common + operating + ['scope_of_change', 'track_access', 'transfer_window']),
        ],
        'workshop_release': [
            ('scheduled_collection', 'Collect on an existing service', 'Assess using a scheduled service with the required exchange location, car position and running window.', 'Collection during an existing train run', common + operating + ['workshop_collection', 'track_access', 'weekend_storage', 'transfer_window']),
            ('dedicated_transport', 'Arrange a dedicated collection', 'Request a transport with technical acceptance, locomotive/driver/escort and a route-specific quote.', 'Separate transport; route and quote unconfirmed', common + operating + ['workshop_collection', 'track_access', 'weekend_storage', 'transfer_window']),
            ('retain_at_workshop', 'Keep the car at the workshop until a safe collection', 'Obtain an accepted storage arrangement and the next permitted weekday collection. Do not leave the car outside over a weekend.', 'No collection until a confirmed safe arrangement', common + ['workshop_collection', 'weekend_storage', 'maintenance_deadline', 'booking_match']),
        ],
        'capacity_change': [
            ('capacity_proposal', 'Prepare a compatible capacity change', 'Specify actual candidate cars and usable product/compartment capacity. Check the complete formation and movement before accepting.', 'Potential car exchange; exact cars unconfirmed', common + operating + ['scope_of_change', 'transfer_window', 'track_access']),
            ('recover_capacity', 'Recover bookings within available capacity', 'Match bookings to verified remaining berths/seats and identify passenger changes that need agreement.', 'Passenger re-accommodation; no spare capacity assumed', common + recovery + ['formation']),
            ('adjust_offer', 'Adjust the booking offer and blocked capacity', 'Assess changes to open-for-sale cars or blocked capacity with customer service and the relevant commercial owner.', 'Booking/product changes only after authorisation', common + recovery + ['formation', 'scope_of_change']),
        ],
        'other': [
            ('manual_review', 'Develop a reviewed operational proposal', 'Make the change concrete, record affected commitments and collect the relevant operational confirmations.', 'Exact action must be specified by the planner', common + operating + ['scope_of_change', 'es_authority']),
        ],
    }
    result = []
    for slug, title, description, movement, keys in definitions.get(case_type, definitions['other']):
        extra = ''
        if case_type == 'service_delay' and context.get('delay_minutes') is not None:
            extra = f' Reported delay: {context["delay_minutes"]} minutes; revised times are not calculated automatically.'
        if case_type == 'resource_unavailable' and context.get('resource'):
            extra = ' Reported resource: ' + context['resource'] + '.'
        if case_type == 'capacity_change' and (context.get('capacity_gap') is not None or context.get('product')):
            extra = f' Reported capacity gap: {context.get("capacity_gap", "not quantified")}; product: {context.get("product", "not specified")}. This is a reported requirement, not verified inventory.'
        option = {'id': case_type + ':' + slug, 'title': title, 'description': description + extra,
                  'kind': 'manual_planning', 'car_id': case.get('car_id') or None, 'rank': len(result) + 1,
                  'passenger_impact': f'{case["affected_passengers"]} reported affected passengers; final impact requires review' if case.get('affected_passengers') is not None else 'Passenger impact not quantified; booking-level review required',
                  'movement': movement, 'cost_label': 'Unknown; quote or estimate required',
                  'checks': _template_checks(conn, case, keys, operates_car=case_type == 'workshop_release' and slug != 'retain_at_workshop')}
        result.append(_finish_option(option, confirmations))
    return result


def generate_options(conn, case, confirmations=None):
    """Build real replacement candidates or explicitly manual, case-specific plans.

    Custom proposals keep a stable ID and receive the same explicit confirmation
    and approval controls. No free-text proposal is called an optimised solution.
    """
    case_type = case.get('case_type') or 'car_unavailable'
    if case_type in {'car_unavailable', 'maintenance_change'}:
        result = _replacement_options(conn, case, confirmations)
        if case_type == 'maintenance_change':
            for option in result:
                label, evidence = GENERIC_CHECKS['maintenance_deadline']
                option['checks'].append(make_check('maintenance_deadline', label, 'pending', evidence))
                _finish_option(option, confirmations)
    else:
        result = _generic_options(conn, case, confirmations)
    case_id = case.get('id')
    if case_id:
        rows = dict_rows(conn, 'SELECT id,proposal,created_at FROM custom_options WHERE case_id=? ORDER BY rowid', (case_id,))
        for row in rows:
            proposal = json.loads(row['proposal'])
            option = {'id': row['id'], 'title': proposal['title'], 'description': proposal['description'],
                      'kind': 'manual_proposal', 'car_id': None, 'rank': len(result) + 1,
                      'passenger_impact': proposal.get('passenger_impact') or 'Not quantified; booking review required',
                      'movement': proposal.get('movement') or 'Exact movement to be specified',
                      'cost_label': proposal.get('cost_label') or 'Unknown; estimate required',
                      'checks': _template_checks(conn, case, list(GENERIC_CHECKS))}
            option['checks'].insert(0, make_check('manual_proposal_review', 'Manual proposal independently reviewed', 'pending', 'This user-authored proposal has not been automatically checked against a structured car/order/timing model. Record an independent operational review before approval.'))
            result.append(_finish_option(option, confirmations))
    return result


def _case_row(conn, case_id):
    if not isinstance(case_id, str) or not re.fullmatch(r'[a-f0-9]{32}', case_id):
        raise APIError('Decision case not found.', 404)
    rows = dict_rows(conn, 'SELECT * FROM cases WHERE id=?', (case_id,))
    if not rows:
        raise APIError('Decision case not found.', 404)
    row = rows[0]
    row['context'] = _context(row)
    return row


def _updates(conn, case_id):
    rows = dict_rows(conn, 'SELECT * FROM updates WHERE case_id=? ORDER BY rowid', (case_id,))
    for row in rows:
        row['confirmations'] = json.loads(row['confirmations'])
        row['revoked_confirmations'] = json.loads(row['revoked_confirmations'])
    return rows


def _confirmation_map(updates):
    result = {}
    for update in updates:
        if not update.get('option_id'):
            continue
        option = result.setdefault(update['option_id'], {})
        evidence = f'{update["department"]} · {update["created_at"]}: {update["message"]}'
        for check_id in update['confirmations']:
            option[check_id] = evidence
        for check_id in update.get('revoked_confirmations', []):
            option.pop(check_id, None)
    return result


def make_drafts(case, selected):
    pending = [c['label'] for c in selected['checks'] if c['status'] != 'pass']
    category = CASE_TYPES.get(case.get('case_type'), CASE_TYPES['car_unavailable'])['label']
    car_note = ' Related car ' + case['car_id'] + '.' if case.get('car_id') else ''
    common = f'Decision case {case["id"][:8]} · {LINES[case["line"]]["name"]} · {case["date"]}. {category}.{car_note} Proposed action: {selected["title"]}. Reason: {case["reason"]}.'
    condition = ' Outstanding: ' + '; '.join(pending) + '.' if pending else ' All listed checks have recorded evidence; final execution remains with the operator.'
    confirmed = [c for c in selected['checks'] if c['evidence'].startswith('Planner-recorded received confirmation:')]
    evidence = '\nRecorded confirmations:\n' + '\n'.join('- ' + c['label'] + ': ' + c['evidence'] for c in confirmed) if confirmed else ''
    return [
        {'department': 'TCS operations', 'text': common + ' Confirm the relevant operating window, resources, movement sequence and formation for this proposal. This is a coordination draft, not a sent operating order.' + condition + evidence},
        {'department': 'Customer service', 'text': common + ' Confirm the booking-level effect, keeping compartment groups and staff/blocked capacity in view. Communicate passenger changes only after the recovery plan and operational decision are confirmed.' + condition},
        {'department': 'Workshop / maintenance', 'text': common + ' Confirm any affected maintenance/release commitment and collection or storage arrangement. Report changes back to this decision case.'},
    ]


def get_case(conn, case_id):
    case = _case_row(conn, case_id)
    case['updates'] = _updates(conn, case_id)
    case['options'] = generate_options(conn, case, _confirmation_map(case['updates']))
    decisions = dict_rows(conn, 'SELECT * FROM decisions WHERE case_id=?', (case_id,))
    case['decision'] = decisions[0] if decisions else None
    if case['decision']:
        case['decision']['approved'] = bool(case['decision']['approved'])
        selected = next((o for o in case['options'] if o['id'] == case['decision']['option_id']), None)
        case['drafts'] = make_drafts(case, selected) if selected else []
    else:
        case['drafts'] = []
    case['events'] = dict_rows(conn, 'SELECT id,kind,detail,created_at FROM case_events WHERE case_id=? ORDER BY rowid', (case_id,))
    for record in case['events']:
        record['detail'] = json.loads(record['detail'])
    case['ranking_note'] = ('Replacement candidates are ordered by recorded availability and known restrictions, not by cheapest cost. Unknown costs are not zero.'
                            if case.get('case_type') in {'car_unavailable', 'maintenance_change'} else 'These are case-specific manual planning templates, not an optimised or automatically validated network plan. Pending checks require recorded operational confirmation.')
    return case


def list_cases(conn):
    rows = dict_rows(conn, 'SELECT * FROM cases ORDER BY created_at DESC,rowid DESC LIMIT 200')
    for row in rows:
        row['context'] = _context(row)
    return rows


def _required_text(payload, key, label, limit):
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise APIError(label + ' is required.')
    value = value.strip()
    if len(value) > limit:
        raise APIError(label + f' must be at most {limit} characters.')
    return value


def create_case(conn, payload):
    line, selected_date = validate_line(payload.get('line')), validate_date(payload.get('date'))
    case_type = payload.get('case_type') or 'car_unavailable'
    if not isinstance(case_type, str) or case_type not in CASE_TYPES:
        raise APIError('Choose a supported operational case type.')
    car_value = payload.get('car_id')
    car_id = normalize_car(car_value) if car_value not in (None, '') else ''
    if CASE_TYPES[case_type]['requires_car'] and not car_id:
        raise APIError('Choose the car concerned by this operational change.')
    reason = _required_text(payload, 'reason', 'The operational change', 4000)
    context = payload.get('context')
    if context is None:
        context = {}
    if not isinstance(context, dict):
        raise APIError('Case context must be a JSON object.')
    cleaned = {}
    for key in ('resource', 'product'):
        if context.get(key) not in (None, ''):
            if not isinstance(context[key], str) or len(context[key]) > 200:
                raise APIError(f'{key} must be a short text value.')
            cleaned[key] = context[key].strip()
    for key, maximum in [('delay_minutes', 10080), ('capacity_gap', 10000)]:
        value = context.get(key)
        if value is None or value == '':
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= maximum or (key == 'capacity_gap' and int(value) != value):
            raise APIError(f'{key} must be a non-negative number no greater than {maximum}.' + (' Use a whole number.' if key == 'capacity_gap' else ''))
        cleaned[key] = value
    passengers = payload.get('affected_passengers')
    if passengers is not None and (isinstance(passengers, bool) or not isinstance(passengers, int) or passengers < 0 or passengers > 10000):
        raise APIError('Affected passengers must be a non-negative whole number, or left blank.')
    if car_id:
        if case_type == 'workshop_release':
            if not conn.execute('SELECT 1 FROM cars WHERE id=?', (car_id,)).fetchone():
                raise APIError('Choose a released car from the imported catalogue.')
        else:
            rows = _fleet_rows(conn, line, selected_date)
            if not any(row['car_id'] == car_id and code_text(row.get('code')) in ACTIVE_CODES for row in rows):
                raise APIError('Choose a car that is assigned to this line on the selected date, or leave the optional car blank.')
    case_id, timestamp = uuid.uuid4().hex, now()
    conn.execute('INSERT INTO cases(id,line,date,car_id,reason,affected_passengers,status,created_at,updated_at,case_type,context) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (case_id, line, selected_date, car_id, reason, passengers, 'open', timestamp, timestamp, case_type, json.dumps(cleaned)))
    # Keep column count explicit: SQLite schemas are portable to later additions.
    event(conn, case_id, 'created', {'line': line, 'date': selected_date, 'case_type': case_type, 'car_id': car_id, 'reason': reason, 'context': cleaned})
    conn.commit()
    return get_case(conn, case_id)


def create_custom_option(conn, case_id, payload):
    case = _case_row(conn, case_id)
    title = _required_text(payload, 'title', 'Proposal title', 200)
    description = _required_text(payload, 'description', 'Proposal description', 4000)
    proposal = {'title': title, 'description': description}
    for key in ('passenger_impact', 'movement', 'cost_label'):
        value = payload.get(key)
        if value not in (None, ''):
            if not isinstance(value, str) or len(value) > 2000:
                raise APIError(f'{key} must be text of at most 2000 characters.')
            proposal[key] = value.strip()
    if conn.execute('SELECT COUNT(*) FROM custom_options WHERE case_id=?', (case_id,)).fetchone()[0] >= 30:
        raise APIError('This case already has 30 manual proposals. Create a new case to keep the decision manageable.')
    option_id = 'custom:' + uuid.uuid4().hex
    timestamp = now()
    conn.execute('INSERT INTO custom_options VALUES(?,?,?,?)', (option_id, case_id, json.dumps(proposal), timestamp))
    conn.execute('UPDATE cases SET updated_at=? WHERE id=?', (timestamp, case_id))
    event(conn, case_id, 'proposal_added', {'option_id': option_id, 'title': title})
    conn.commit()
    return get_case(conn, case_id)


def record_update(conn, case_id, payload):
    case = get_case(conn, case_id)
    department = _required_text(payload, 'department', 'Department', 100)
    message = _required_text(payload, 'message', 'Received update', 8000)
    option_id = payload.get('option_id')
    if option_id is not None and not isinstance(option_id, str):
        raise APIError('Choose a valid candidate option.')
    confirmations = payload.get('confirmations', [])
    revoked = payload.get('revoke_confirmations', [])
    if not isinstance(confirmations, list) or not isinstance(revoked, list) or not all(isinstance(x, str) for x in confirmations + revoked):
        raise APIError('Confirmations must be a list of named checks.')
    if len(confirmations) > 30 or len(revoked) > 30:
        raise APIError('Too many confirmation fields.')
    if confirmations or revoked or option_id:
        # Use base checks: previously confirmed unknowns may be revoked, whereas
        # source-derived passes and failures are never editable through messaging.
        original = generate_options(conn, case)
        option = next((o for o in original if o['id'] == option_id), None)
        if option is None:
            raise APIError('Choose the option this received confirmation concerns.')
        allowed = {c['id'] for c in option['checks'] if c['status'] == 'pending'}
        if set(confirmations + revoked) - allowed:
            raise APIError('Only unresolved operational facts can be confirmed or revoked. Source-backed checks cannot be overridden.')
    timestamp, update_id = now(), uuid.uuid4().hex
    conn.execute('INSERT INTO updates VALUES(?,?,?,?,?,?,?,?)',
                 (update_id, case_id, department, message, option_id,
                  json.dumps(sorted(set(confirmations))), json.dumps(sorted(set(revoked))), timestamp))
    if case['decision'] and case['decision']['approved']:
        conn.execute('UPDATE decisions SET approved=0 WHERE case_id=?', (case_id,))
        conn.execute('UPDATE cases SET status=? WHERE id=?', ('selected', case_id))
        event(conn, case_id, 'approval_requires_review', {'reason': 'A new department update was recorded after approval.'})
    conn.execute('UPDATE cases SET updated_at=? WHERE id=?', (timestamp, case_id))
    event(conn, case_id, 'update_recorded', {'update_id': update_id, 'department': department, 'option_id': option_id,
                                           'confirmations': confirmations, 'revoked': revoked})
    conn.commit()
    return get_case(conn, case_id)


def choose_decision(conn, case_id, payload):
    case = get_case(conn, case_id)
    option_id = payload.get('option_id')
    if not isinstance(option_id, str):
        raise APIError('Choose an option first.')
    option = next((o for o in case['options'] if o['id'] == option_id), None)
    if not option:
        raise APIError('The selected option is not part of this case.')
    rationale = _required_text(payload, 'rationale', 'Decision rationale', 4000)
    approve = payload.get('approve', False)
    if not isinstance(approve, bool):
        raise APIError('Approval must be true or false.')
    if option['status'] == 'blocked':
        raise APIError('This action has a source-backed failure and cannot be selected. Resolve the source conflict or choose another option.', 409)
    if approve and option['status'] != 'ready':
        unresolved = [c['label'] for c in option['checks'] if c['status'] != 'pass']
        raise APIError('Approval is blocked until these checks are resolved: ' + '; '.join(unresolved), 409)
    timestamp = now()
    conn.execute('''INSERT INTO decisions(case_id,option_id,rationale,approved,created_at) VALUES(?,?,?,?,?)
                    ON CONFLICT(case_id) DO UPDATE SET option_id=excluded.option_id,rationale=excluded.rationale,
                    approved=excluded.approved,created_at=excluded.created_at''',
                 (case_id, option_id, rationale, int(approve), timestamp))
    conn.execute('UPDATE cases SET status=?,updated_at=? WHERE id=?', ('approved' if approve else 'selected', timestamp, case_id))
    event(conn, case_id, 'decision_approved' if approve else 'provisional_choice',
          {'option_id': option_id, 'rationale': rationale, 'checks': option['checks']})
    conn.commit()
    return get_case(conn, case_id)


def handoff_text(case):
    if not case.get('decision'):
        raise APIError('Save a provisional choice or approval before preparing the handoff.')
    label = 'Planner-approved decision' if case['decision']['approved'] else 'Provisional decision — pending confirmations'
    parts = ['SLEEPER OPERATIONS — COORDINATION DRAFT', label, 'Not sent to any department.',
             f'Case: {case["id"]}', f'Line/date: {case["line"]} / {case["date"]}',
             'Rationale: ' + case['decision']['rationale'], '']
    for draft in case.get('drafts', []):
        parts += [draft['department'].upper(), draft['text'], '']
    return '\n'.join(parts)
