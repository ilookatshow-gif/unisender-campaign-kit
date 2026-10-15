#!/usr/bin/env python3
"""Original, dependency-free offline mail preflight. Never connects or sends."""

import argparse
import csv
import datetime as dt
import io
import json
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import stat
import sys

VERSION = "0.1.0"
TOOL = "lookatshow-mail-plan"
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_HTML_BYTES = 5_000_000
MAX_HTML_DEPTH = 128
MAX_ROWS = 100000
CAMPAIGN_FIELDS = {"sender_email", "sender_name", "subject", "html_body", "list_id"}
CONTACT_REQUIRED = {"email", "consent", "consent_at", "consent_evidence", "unsubscribed"}
CONTACT_FIELDS = CONTACT_REQUIRED | {"consent_expires_at"}
SUPPRESSION_FIELDS = {"email", "reason"}
EMAIL_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+\Z")
DOMAIN_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
ONBOARD_STATE = "lookatshow-mail-plan-onboard-v1.json"
ONBOARD_GREETING = (
    "Lookatshow: original offline tool by Дмитрий Лукашов. "
    "Практика ИИ в маркетинге: https://t.me/lookatsoul . "
    "Обучение и внедрение: https://lookatshow.ru/ai-implementation/"
    "?utm_source=github&utm_medium=open_module&utm_campaign=unisender-campaign-kit"
    "&utm_content=implementation . No network access, no sending, no hidden telemetry."
)


class SafeError(Exception):
    def __init__(self, code, message, **details):
        self.code = code
        self.message = message
        self.details = details


class OutputFailure(Exception):
    """The output sink failed; do not retry or expose its exception text."""


def envelope(command, result=None, error=None):
    value = {"schema_version": 1, "tool": TOOL, "version": VERSION,
             "command": command, "mode": "offline", "ok": error is None}
    if error is None:
        value["result"] = result
    else:
        value["error"] = {"code": error.code, "message": error.message, **error.details}
    return value


def read_text(path, category):
    descriptor = None
    try:
        # O_NONBLOCK prevents opening a FIFO from waiting for a writer on POSIX.
        # fstat checks the opened descriptor, not only a raceable path snapshot.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SafeError("INPUT_NOT_REGULAR_FILE", "Input must be a regular local file.", input=category)
        if metadata.st_size > MAX_INPUT_BYTES:
            raise SafeError("INPUT_TOO_LARGE", "Input exceeds the local safety bound.", input=category)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(MAX_INPUT_BYTES + 1)
    except (OSError, ValueError):
        raise SafeError("INPUT_READ_FAILED", "Cannot read the supplied input.", input=category)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw) > MAX_INPUT_BYTES:
        raise SafeError("INPUT_TOO_LARGE", "Input exceeds the local safety bound.", input=category)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError:
        raise SafeError("INPUT_ENCODING", "Input must be UTF-8.", input=category)


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SafeError("DUPLICATE_JSON_KEY", "Repeated JSON keys are ambiguous.", input="campaign")
        result[key] = value
    return result


def load_campaign(path):
    try:
        value = json.loads(read_text(path, "campaign"), object_pairs_hook=no_duplicate_keys,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise SafeError("CAMPAIGN_JSON", "Campaign must be valid JSON.", input="campaign")
    return value


def normalized_email(value):
    """Conservative ASCII mailbox syntax, not existence/deliverability validation."""
    if not isinstance(value, str) or value != value.strip() or len(value) > 254:
        return None
    if value.count("@") != 1:
        return None
    local, domain = value.split("@")
    if not local or len(local) > 64 or not EMAIL_LOCAL.fullmatch(local):
        return None
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return None
    labels = domain.split(".")
    if len(labels) < 2 or not all(DOMAIN_LABEL.fullmatch(label) for label in labels):
        return None
    if len(domain) > 253 or labels[-1].isdigit():
        return None
    # Deliberately conservative: case-insensitive deduplication may exclude mailboxes.
    return value.casefold()


def aware_time(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError()
    return parsed.astimezone(dt.timezone.utc)


class UnsubscribeAnchorParser(HTMLParser):
    INERT = {"template", "textarea", "script", "style", "title", "xmp", "noscript", "iframe", "object"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.exact_anchor = False
        self.decorated_or_ambiguous = False
        self.frames = []

    @staticmethod
    def visibly_nonempty(value):
        return any(not char.isspace() and char not in "\u200b\u200c\u200d\u2060\ufeff" for char in value)

    @staticmethod
    def hidden(attrs):
        if any(key == "hidden" or (key == "aria-hidden" and (value or "").casefold().strip() == "true")
               for key, value in attrs):
            return True
        for key, value in attrs:
            if key != "style" or value is None:
                continue
            for declaration in value.split(";"):
                prop, separator, content = declaration.partition(":")
                if not separator:
                    continue
                content = re.sub(r"\s+", "", content.casefold()).replace("!important", "")
                prop = prop.strip().casefold()
                if ((prop == "display" and content == "none")
                        or (prop == "visibility" and content in {"hidden", "collapse"})
                        or (prop == "opacity" and re.fullmatch(r"0(?:\.0+)?", content))
                        or (prop == "font-size" and re.fullmatch(r"0(?:\.0+)?(?:px|pt|em|rem|%)?", content))):
                    return True
        return False

    def mark_content(self, value):
        if not self.visibly_nonempty(value) or (self.frames and self.frames[-1]["inactive"]):
            return
        for frame in self.frames:
            if frame["tag"] == "a" and frame["candidate"]:
                frame["content"] = True

    def handle_starttag(self, tag, attrs):
        if len(self.frames) >= MAX_HTML_DEPTH:
            raise ValueError("HTML exceeds the local nesting bound.")
        inactive = (bool(self.frames) and self.frames[-1]["inactive"]) or tag in self.INERT or self.hidden(attrs)
        candidate = False
        if tag == "a":
            hrefs = [value for key, value in attrs if key == "href"]
            if len(hrefs) > 1 or any(frame["tag"] == "a" for frame in self.frames):
                self.decorated_or_ambiguous = True
            for value in hrefs:
                if value == "{{UnsubscribeUrl}}":
                    candidate = not inactive
                elif value is not None and "{{UnsubscribeUrl}}" in value:
                    self.decorated_or_ambiguous = True
        if tag == "img" and not inactive:
            for key, value in attrs:
                if key == "alt" and value:
                    self.mark_content(value)
        if tag not in self.VOID:
            self.frames.append({"tag": tag, "inactive": inactive, "candidate": candidate, "content": False})

    def handle_endtag(self, tag):
        for index in range(len(self.frames) - 1, -1, -1):
            if self.frames[index]["tag"] == tag:
                frame = self.frames[index]
                if tag == "a" and frame["candidate"] and frame["content"]:
                    self.exact_anchor = True
                del self.frames[index:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        self.mark_content(data)


def unsubscribe_anchor_status(body):
    parser = UnsubscribeAnchorParser()
    try:
        parser.feed(body)
        parser.close()
    except (ValueError, RecursionError):
        return False, True
    return parser.exact_anchor, parser.decorated_or_ambiguous


def campaign_issues(value):
    if not isinstance(value, dict):
        return [{"reason": "campaign_not_object"}]
    issues = []
    unknown = set(value) - CAMPAIGN_FIELDS
    if unknown:
        issues.append({"reason": "unknown_campaign_fields", "count": len(unknown)})
    missing = CAMPAIGN_FIELDS - set(value)
    for key in sorted(missing):
        issues.append({"field": key, "reason": "missing_field"})
    if "sender_email" in value and normalized_email(value["sender_email"]) is None:
        issues.append({"field": "sender_email", "reason": "invalid_sender_email"})
    for key, bound in (("sender_name", 200), ("subject", 500)):
        if key in value:
            item = value[key]
            if (not isinstance(item, str) or not item.strip() or len(item) > bound
                    or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in item)):
                issues.append({"field": key, "reason": "invalid_header_text"})
    if (isinstance(value.get("sender_name"), str) and isinstance(value.get("sender_email"), str)
            and value["sender_name"].strip().casefold() == value["sender_email"].strip().casefold()):
        issues.append({"field": "sender_name", "reason": "sender_name_matches_email"})
    if "list_id" in value and (type(value["list_id"]) is not int or value["list_id"] <= 0):
        issues.append({"field": "list_id", "reason": "positive_integer_required"})
    if "html_body" in value:
        body = value["html_body"]
        if not isinstance(body, str) or not body.strip() or "\x00" in body:
            issues.append({"field": "html_body", "reason": "invalid_html_body"})
        else:
            try:
                body_bytes = len(body.encode("utf-8"))
            except UnicodeError:
                issues.append({"field": "html_body", "reason": "invalid_html_encoding"})
            else:
                # Local HTML cap leaves room below the documented whole-message
                # limit. MIME encoding/headers/provider acceptance remain untested.
                if body_bytes > MAX_HTML_BYTES:
                    issues.append({"field": "html_body", "reason": "html_byte_limit_exceeded"})
                else:
                    exact, decorated = unsubscribe_anchor_status(body)
                    if not exact:
                        issues.append({"field": "html_body", "reason": "unsubscribe_anchor_missing"})
                    if decorated:
                        issues.append({"field": "html_body", "reason": "unsubscribe_href_ambiguous_or_decorated"})
    return issues


def validate_campaign(value):
    issues = campaign_issues(value)
    if issues:
        raise SafeError("CAMPAIGN_INVALID", "Campaign failed local validation.", issues=issues)
    return {"checked_fields": len(CAMPAIGN_FIELDS), "exact_unsubscribe_anchor_present": True,
            "local_html_byte_limit": MAX_HTML_BYTES, "whole_message_size_verified": False,
            "provider_acceptance_verified": False, "legal_permission_verified": False,
            "sending_performed": False}


def load_csv(path, category, required, allowed):
    source = read_text(path, category)
    # CSV cannot carry NUL, which some spreadsheet/import tools silently truncate.
    if "\x00" in source:
        raise SafeError("CSV_INVALID", "CSV contains unsupported control bytes.", input=category)
    try:
        reader = csv.reader(io.StringIO(source, newline=""), strict=True)
        header = next(reader)
        if not header or len(set(header)) != len(header) or any(key != key.strip() for key in header):
            raise SafeError("CSV_HEADER", "CSV header is absent or ambiguous.", input=category)
        if not required.issubset(header) or not set(header).issubset(allowed):
            raise SafeError("CSV_SCHEMA", "CSV columns do not match the documented schema.", input=category,
                            unknown_column_count=len(set(header) - allowed),
                            missing_column_count=len(required - set(header)))
        rows = []
        for ordinal, row in enumerate(reader, 1):
            if ordinal > MAX_ROWS:
                raise SafeError("CSV_ROW_LIMIT", "CSV exceeds the local row bound.", input=category)
            if len(row) != len(header):
                raise SafeError("CSV_ROW_WIDTH", "CSV row width does not match its header.", input=category, ordinal=ordinal)
            rows.append(dict(zip(header, row)))
        return rows
    except StopIteration:
        raise SafeError("CSV_HEADER", "CSV header is absent or ambiguous.", input=category)
    except csv.Error:
        raise SafeError("CSV_INVALID", "CSV syntax is malformed.", input=category)


def supplied_bool(value):
    # No inference from yes/no, 1/0, localized words, blanks or truthiness.
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def evaluate_row(row, ordinal, at):
    reasons = []
    email = normalized_email(row["email"])
    if email is None:
        reasons.append("invalid_email")
    consent = supplied_bool(row["consent"])
    unsubscribed = supplied_bool(row["unsubscribed"])
    if consent is None:
        reasons.append("ambiguous_consent_flag")
    elif not consent:
        reasons.append("consent_not_supplied")
    if unsubscribed is None:
        reasons.append("ambiguous_unsubscribe_flag")
    elif unsubscribed:
        reasons.append("unsubscribed")
    consent_at = None
    try:
        consent_at = aware_time(row["consent_at"])
        if consent_at > at:
            reasons.append("consent_in_future")
    except (ValueError, OverflowError):
        reasons.append("invalid_consent_timestamp")
    evidence = row["consent_evidence"].strip()
    if not evidence or len(evidence) > 2048:
        reasons.append("missing_or_invalid_consent_evidence")
    expiry = None
    expiry_value = row.get("consent_expires_at", "")
    if expiry_value:
        try:
            expiry = aware_time(expiry_value)
            if consent_at is not None and expiry <= consent_at:
                reasons.append("invalid_consent_period")
            if expiry <= at:
                reasons.append("consent_expired")
        except (ValueError, OverflowError):
            reasons.append("invalid_expiry_timestamp")
    # Signature remains in memory; never serialize supplied evidence or addresses.
    signature = (consent, consent_at, evidence, expiry, unsubscribed,
                 tuple(sorted(reasons)))
    return {"ordinal": ordinal, "email": email, "signature": signature, "reasons": reasons}


def make_plan(campaign, contacts, suppression, at):
    validate_campaign(campaign)
    blocked = set()
    for ordinal, row in enumerate(suppression, 1):
        email = normalized_email(row["email"])
        if email is None:
            raise SafeError("SUPPRESSION_INVALID", "Invalid suppression row makes exclusions uncertain.",
                            input="suppression", ordinal=ordinal)
        blocked.add(email)
    records = [evaluate_row(row, ordinal, at) for ordinal, row in enumerate(contacts, 1)]
    groups = {}
    for record in records:
        if record["email"] is not None:
            groups.setdefault(record["email"], []).append(record)
    for email, group in groups.items():
        if email in blocked:
            for record in group:
                record["reasons"].append("suppressed")
        if len(group) > 1:
            if len({record["signature"] for record in group}) > 1:
                for record in group:
                    record["reasons"].append("duplicate_conflict")
            else:
                for record in group[1:]:
                    record["reasons"].append("duplicate_address")
    public_rows = []
    reason_counts = {}
    eligible = 0
    for record in records:
        reasons = sorted(set(record["reasons"]))
        if not reasons:
            eligible += 1
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        public_rows.append({"ordinal": record["ordinal"],
                            "status": "eligible_under_supplied_rules" if not reasons else "excluded",
                            "reasons": reasons})
    return {"scope": "supplied_local_data_only", "as_of_utc": at.isoformat(),
            "counts": {"contact_rows": len(records), "unique_valid_addresses": len(groups),
                       "suppression_rows": len(suppression), "unique_suppressions": len(blocked),
                       "eligible_under_supplied_rules": eligible, "excluded_rows": len(records) - eligible},
            "reason_counts": dict(sorted(reason_counts.items())), "rows": public_rows,
            "consent_evidence_verified": False, "legal_permission_verified": False,
            "provider_acceptance_verified": False, "deliverability_verified": False,
            "sending_performed": False}


def no_symlink_components(path):
    path = Path(os.path.abspath(str(path)))
    for component in (path,) + tuple(path.parents):
        try:
            mode = os.lstat(component).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise SafeError("STATE_UNSAFE_PATH", "State path must not contain symlinks.")
    return path


def emit_json(value, stream=None):
    """A single flushed, ASCII-safe JSON record; never rely on console encoding."""
    stream = sys.stdout if stream is None else stream
    try:
        stream.write(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n")
        stream.flush()
    except (OSError, ValueError):
        raise OutputFailure() from None


def onboard(state_dir, emit):
    """Emit before committing the marker; a separate lock serializes local callers.

    A lock left by a terminated process is deliberately not removed automatically.
    The caller must inspect that local state before deciding a safe recovery.
    """
    directory = no_symlink_components(state_dir)
    lock = directory / (ONBOARD_STATE + ".lock")
    pending = directory / (ONBOARD_STATE + ".pending")
    lock_created = False
    pending_created = False
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        no_symlink_components(directory)
        state = directory / ONBOARD_STATE
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(str(lock), flags, 0o600)
        except FileExistsError:
            raise SafeError("STATE_BUSY", "Onboarding state is locked; inspect before retrying.")
        lock_created = True
        os.close(fd)
        if os.path.lexists(state):
            mode = os.lstat(state).st_mode
            if not stat.S_ISREG(mode):
                raise SafeError("STATE_UNSAFE_PATH", "Existing state is not a regular file.")
            try:
                with open(state, "rb") as handle:
                    raw = handle.read(1025)
                marker = json.loads(raw) if len(raw) <= 1024 else None
            except (ValueError, UnicodeError, RecursionError):
                marker = None
            if (not isinstance(marker, dict) or set(marker) != {"schema_version", "greeting_shown"}
                    or type(marker["schema_version"]) is not int or marker["schema_version"] != 1
                    or marker["greeting_shown"] is not True):
                raise SafeError("STATE_INVALID", "Existing onboarding state is invalid; inspect before retrying.")
            emit({"greeting_shown": False, "state_scope": "supplied_state_directory"})
            return
        # Prepare a private local file before output; the canonical marker does
        # not exist until the full output record has been written and flushed.
        fd = os.open(str(pending), flags, 0o600)
        pending_created = True
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "greeting_shown": True}, handle)
        emit({"greeting_shown": True, "state_scope": "supplied_state_directory", "greeting": ONBOARD_GREETING})
        # A peer using this CLI is serialized by the lock; never overwrite an
        # unrelated marker unexpectedly created while output was being emitted.
        if os.path.lexists(state):
            raise SafeError("STATE_CHANGED", "Onboarding state changed during output; inspect before retrying.")
        os.replace(str(pending), str(state))
        pending_created = False
    except SafeError:
        raise
    except (OSError, ValueError):
        raise SafeError("STATE_WRITE_FAILED", "Cannot create local onboarding state.")
    finally:
        if pending_created:
            try:
                pending.unlink()
            except OSError:
                pass
        if lock_created:
            try:
                lock.unlink()
            except OSError:
                pass


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown arguments; those may contain secrets.
        raise SafeError("CLI_USAGE", "Invalid arguments. Use --help for the command contract.")


def parser():
    value = SafeParser(prog=TOOL, description="Offline campaign and consent-data preflight. Never sends or connects.")
    value.add_argument("--json", action="store_true", help="Stable JSON output (also the default).")
    value.add_argument("--version", action="version", version=VERSION)
    sub = value.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    sub.add_parser("doctor", help="Check the offline runtime; auth and network are not used.")
    validate = sub.add_parser("validate", help="Validate a local campaign JSON, without echoing content.")
    validate.add_argument("--campaign", required=True)
    plan = sub.add_parser("plan", help="Count eligible local rows and exclusions; never exports addresses or sends.")
    plan.add_argument("--campaign", required=True)
    plan.add_argument("--contacts", required=True)
    plan.add_argument("--suppression", required=True)
    plan.add_argument("--at", required=True, help="Explicit timezone-aware ISO timestamp for consent checks.")
    sub.add_parser("demo", help="Run only labeled synthetic example.invalid fixtures.")
    intro = sub.add_parser("onboard", help="One greeting per explicit local state directory; no network.")
    intro.add_argument("--state-dir", required=True)
    return value


def main(argv=None):
    args_raw = list(sys.argv[1:] if argv is None else argv)
    # Accept --json before or after subcommands; output is JSON in either case.
    args_raw = [item for item in args_raw if item != "--json"]
    command = "usage"
    emitted = False

    def output(result):
        nonlocal emitted
        emit_json(envelope(command, result=result))
        emitted = True

    try:
        args = parser().parse_args(args_raw)
        command = args.command
        if command == "doctor":
            result = {"runtime_supported": sys.version_info >= (3, 9), "python_minimum": "3.9",
                      "auth_required": False, "network_used": False, "sending_supported": False,
                      "commands": ["doctor", "validate", "plan", "demo", "onboard"]}
            if not result["runtime_supported"]:
                raise SafeError("PYTHON_VERSION", "Python 3.9 or later is required.")
        elif command == "validate":
            result = validate_campaign(load_campaign(args.campaign))
        elif command in ("plan", "demo"):
            if command == "demo":
                examples = Path(__file__).resolve().parent / "examples"
                campaign_path = examples / "campaign.example.json"
                contacts_path = examples / "contacts.example.csv"
                suppression_path = examples / "suppression.example.csv"
                clock = "2026-10-14T17:00:00+03:00"
            else:
                campaign_path, contacts_path, suppression_path = args.campaign, args.contacts, args.suppression
                clock = args.at
            try:
                at = aware_time(clock)
            except (ValueError, OverflowError):
                raise SafeError("CLOCK_INVALID", "Supply an explicit timezone-aware ISO timestamp.")
            result = make_plan(load_campaign(campaign_path),
                               load_csv(contacts_path, "contacts", CONTACT_REQUIRED, CONTACT_FIELDS),
                               load_csv(suppression_path, "suppression", {"email"}, SUPPRESSION_FIELDS), at)
            if command == "demo":
                result["data_kind"] = "synthetic_example_invalid_only"
        else:
            onboard(args.state_dir, output)
            return 0
        output(result)
        return 0
    except SafeError as error:
        # If a post-output state commit fails, do not emit a second stdout JSON
        # record that would corrupt parsers. Nonzero exit + stderr records the
        # local bookkeeping failure, without paths or input values.
        try:
            emit_json(envelope(command, error=error), sys.stderr if emitted else sys.stdout)
        except OutputFailure:
            pass
        return 2
    except OutputFailure:
        return 3
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # No tracebacks or supplied values, even if malformed data reaches a boundary.
        error = SafeError("LOCAL_PREFLIGHT_FAILED", "Local preflight failed without exposing input contents.")
        try:
            emit_json(envelope(command, error=error), sys.stderr if emitted else sys.stdout)
        except OutputFailure:
            pass
        return 3


if __name__ == "__main__":
    sys.exit(main())
