"""Local HTTP application for fleet evidence, operational cases and decisions."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import engine
from import_data import ensure_data_schema, import_workbooks

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / 'web'
MAX_BODY = 131072


def connect(db_path):
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn


def _local_env():
    """Read simple local KEY=value settings without executing shell content."""
    path = ROOT / '.env'
    if not path.is_file():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        value = raw.strip()
        if not value or value.startswith('#') or '=' not in value:
            continue
        key, content = value.split('=', 1)
        key = key.strip()
        if key not in {'FLEET_WORKBOOK', 'RUNNING_WORKBOOK', 'SLEEPER_DB', 'SLEEPER_PORT'}:
            continue
        content = content.strip()
        if len(content) >= 2 and content[0] == content[-1] and content[0] in "\"'":
            content = content[1:-1]
        os.environ.setdefault(key, content)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(db_path):
    """Bind one application's DB location without global mutable connections."""
    class Handler(BaseHTTPRequestHandler):
        server_version = 'SleeperOperations/1.0'
        sys_version = ''

        def log_message(self, fmt, *args):
            # Never log request bodies or department message content.
            sys.stderr.write('[operations] ' + (fmt % args) + '\n')

        def security_headers(self):
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

        def send_bytes(self, payload, content_type, status=200, download=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download}"')
            self.security_headers()
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)

        def send_json(self, value, status=200):
            self.send_bytes(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'),
                            'application/json; charset=utf-8', status)

        def read_json(self):
            if self.headers.get('Transfer-Encoding'):
                raise engine.APIError('Chunked request bodies are not supported.', 400)
            if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise engine.APIError('Send request data as application/json.', 415)
            try:
                length = int(self.headers.get('Content-Length', ''))
            except ValueError as exc:
                raise engine.APIError('Content-Length is required.', 411) from exc
            if length < 1 or length > MAX_BODY:
                raise engine.APIError('Request body is empty or too large.', 413)
            origin = self.headers.get('Origin')
            if origin:
                try:
                    parsed = urlsplit(origin)
                    origin_port = parsed.port
                except ValueError as exc:
                    raise engine.APIError('Invalid request origin.', 403) from exc
                allowed_hosts = {'127.0.0.1', 'localhost', self.server.server_address[0]}
                if parsed.scheme != 'http' or parsed.hostname not in allowed_hosts or origin_port != self.server.server_port:
                    raise engine.APIError('This local application accepts updates only from its own origin.', 403)
            try:
                payload = json.loads(self.rfile.read(length).decode('utf-8'), parse_constant=lambda text: (_ for _ in ()).throw(ValueError('Invalid number')))
            except (UnicodeDecodeError, ValueError) as exc:
                raise engine.APIError('The request is not valid JSON.') from exc
            if not isinstance(payload, dict):
                raise engine.APIError('Send one JSON object.')
            return payload

        def _route(self):
            parsed = urlsplit(self.path)
            path = unquote(parsed.path).rstrip('/') or '/'
            query = parse_qs(parsed.query, keep_blank_values=True)
            get = lambda key, default=None: query.get(key, [default])[0]
            if not path.startswith('/api/'):
                if self.command not in {'GET', 'HEAD'}:
                    raise engine.APIError('Method not allowed.', 405)
                relative = 'index.html' if path == '/' else path.lstrip('/')
                if any(part.startswith('.') for part in Path(relative).parts):
                    raise engine.APIError('Page not found.', 404)
                try:
                    target = (WEB_ROOT / relative).resolve()
                    target.relative_to(WEB_ROOT.resolve())
                except (ValueError, OSError) as exc:
                    raise engine.APIError('Page not found.', 404) from exc
                if not target.is_file() or target.suffix.lower() not in {'.html', '.css', '.js', '.svg', '.png', '.ico', '.woff2'}:
                    raise engine.APIError('Page not found.', 404)
                mime = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                if target.suffix in {'.html', '.css', '.js'}:
                    mime += '; charset=utf-8'
                self.send_bytes(target.read_bytes(), mime)
                return
            if self.command not in {'GET', 'HEAD', 'POST'}:
                raise engine.APIError('Method not allowed.', 405)
            with connect(db_path) as conn:
                if self.command in {'GET', 'HEAD'}:
                    if path == '/api/bootstrap':
                        self.send_json(engine.get_bootstrap(conn)); return
                    if path == '/api/plan':
                        self.send_json(engine.get_plan(conn, get('line', 'Milan'), get('date'))); return
                    if path == '/api/car':
                        self.send_json(engine.get_car(conn, get('id'), get('date'))); return
                    if path == '/api/cases':
                        self.send_json(engine.list_cases(conn)); return
                    parts = path.strip('/').split('/')
                    if len(parts) == 3 and parts[:2] == ['api', 'cases']:
                        self.send_json(engine.get_case(conn, parts[2])); return
                    if len(parts) == 4 and parts[:2] == ['api', 'cases'] and parts[3] == 'handoff':
                        case = engine.get_case(conn, parts[2])
                        self.send_bytes(engine.handoff_text(case).encode('utf-8'), 'text/plain; charset=utf-8', download='coordination-' + parts[2][:8] + '.txt'); return
                    if path == '/api/health':
                        self.send_json({'status': 'ok', 'imported': engine.metadata(conn).get('imported', False)}); return
                else:
                    payload = self.read_json()
                    # Serialize case updates: one HTTP request cannot overwrite a
                    # concurrently committed confirmation/approval snapshot.
                    conn.execute('BEGIN IMMEDIATE')
                    if path == '/api/cases':
                        self.send_json(engine.create_case(conn, payload), 201); return
                    parts = path.strip('/').split('/')
                    if len(parts) == 4 and parts[:2] == ['api', 'cases']:
                        if parts[3] == 'updates':
                            self.send_json(engine.record_update(conn, parts[2], payload), 201); return
                        if parts[3] == 'decision':
                            self.send_json(engine.choose_decision(conn, parts[2], payload)); return
                        if parts[3] == 'options':
                            self.send_json(engine.create_custom_option(conn, parts[2], payload), 201); return
            raise engine.APIError('API endpoint not found.', 404)

        def dispatch(self):
            try:
                self._route()
            except engine.APIError as exc:
                self.send_json({'error': str(exc)}, exc.status)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except sqlite3.OperationalError as exc:
                sys.stderr.write('[operations] Database request failed: ' + str(exc) + '\n')
                self.send_json({'error': 'The local database could not complete this request. Retry, or check the server log.'}, 503)
            except Exception as exc:
                sys.stderr.write('[operations] Request failed: ' + type(exc).__name__ + ': ' + str(exc) + '\n')
                self.send_json({'error': 'The request could not be completed. Check the local server log.'}, 500)

        do_GET = dispatch
        do_HEAD = dispatch
        do_POST = dispatch
        do_PUT = dispatch
        do_DELETE = dispatch
        do_PATCH = dispatch
        do_OPTIONS = dispatch

    return Handler


def main(argv=None):
    _local_env()
    parser = argparse.ArgumentParser(description='Run the local European Sleeper operations decision workspace.')
    parser.add_argument('--host', default='127.0.0.1', help='Bind address; loopback is the local-only default.')
    parser.add_argument('--port', type=int, default=int(os.environ.get('SLEEPER_PORT', '8766')))
    parser.add_argument('--db', default=os.environ.get('SLEEPER_DB', str(ROOT / 'var' / 'sleeper-ops.sqlite3')))
    parser.add_argument('--fleet', default=os.environ.get('FLEET_WORKBOOK'), help='Path to Fleetmanagement-2026sandbox.xlsx (read only).')
    parser.add_argument('--running', default=os.environ.get('RUNNING_WORKBOOK'), help='Path to Weekinfo details per train.xlsx (read only).')
    parser.add_argument('--reimport', action='store_true', help='Replace imported source records; preserve cases and invalidate prior approvals.')
    parser.add_argument('--check-data', action='store_true', help='Print import counts and exit without starting a server.')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535.')
    db_path = Path(args.db).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        ensure_data_schema(conn)
        engine.ensure_case_schema(conn)
        imported = engine.metadata(conn).get('imported', False)
        fleet = Path(args.fleet).expanduser().resolve() if args.fleet else None
        if fleet is None:
            for candidate in [ROOT / 'data' / 'Fleetmanagement-2026sandbox.xlsx', ROOT.parent / 'data' / 'Fleetmanagement-2026sandbox.xlsx']:
                if candidate.is_file():
                    fleet = candidate
                    break
        running = Path(args.running).expanduser().resolve() if args.running else None
        if running is None and fleet:
            candidate = fleet.parent / 'Weekinfo details per train.xlsx'
            if candidate.is_file():
                running = candidate
        if args.fleet and not fleet.is_file():
            parser.error('The supplied fleet workbook was not found.')
        if args.running and not running.is_file():
            parser.error('The supplied running workbook was not found.')
        if args.reimport and not fleet:
            parser.error('--reimport requires a fleet workbook path.')
        if fleet and (not imported or args.reimport):
            print('Reading source workbooks into the local database…', flush=True)
            try:
                result = import_workbooks(conn, fleet, running)
            except Exception as exc:
                parser.error('Workbook import failed: ' + str(exc))
            if imported:
                approved = conn.execute('SELECT case_id FROM decisions WHERE approved=1').fetchall()
                for row in approved:
                    engine.event(conn, row[0], 'approval_requires_review', {'reason': 'Source workbooks were reimported.'})
                conn.execute('UPDATE decisions SET approved=0 WHERE approved=1')
                conn.execute("UPDATE cases SET status='selected' WHERE status='approved'")
                # Source refresh can change underlying facts: stale confirmation
                # evidence remains in history but is explicitly revoked.
                for case_row in conn.execute('SELECT id FROM cases').fetchall():
                    grouped = engine._confirmation_map(engine._updates(conn, case_row[0]))
                    for option_id, checks in grouped.items():
                        if checks:
                            conn.execute('INSERT INTO updates VALUES(?,?,?,?,?,?,?,?)',
                                         (uuid.uuid4().hex, case_row[0], 'Source import',
                                          'Source records refreshed; operational confirmations require review.', option_id,
                                          '[]', json.dumps(list(checks)), engine.now()))
                conn.commit()
            print('Import complete: ' + json.dumps(result.get('counts', {})), flush=True)
        elif not imported:
            print('No source workbooks imported. Supply --fleet and --running to load operational data.', flush=True)
        if args.check_data:
            print(json.dumps(engine.get_bootstrap(conn)['meta'], ensure_ascii=False, indent=2))
            return 0
    server = LocalServer((args.host, args.port), make_handler(db_path))
    print(f'Sleeper Operations is ready at http://{args.host}:{server.server_port}', flush=True)
    print('Operational data and saved records stay in the local database. Press Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping Sleeper Operations.', flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
