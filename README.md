# Check an email campaign before it reaches UniSender

**UniSender campaign kit** is a free offline preflight for a UniSender **Classic**
workflow. Give an agent a draft, a local permission ledger and an exclusion list.
Get a reproducible explanation of which rows meet the supplied rules, which do
not, and why. No account connection, API key, contact import or email send.

[Русская инструкция](README.ru.md) ·
[Lookatshow system and open tools](https://github.com/ilookatshow-gif/lookatshow-open-tools)

## Try a useful result first

Requires **Python 3.9+**, standard library only. From the unpacked package:

```sh
python3 -B lookatshow_mail_plan.py doctor
python3 -B lookatshow_mail_plan.py demo
```

On Windows, use `python` or `py -3` if that is your Python launcher. The second
command uses only invented addresses under `example.invalid` and a fixed
evaluation time, **14 October 2026, 17:00 MSK**. It does not inspect your email.

| Synthetic input row | Result | Explanation |
| --- | --- | --- |
| 1 | Eligible under supplied rules | Explicit permission assertion, timestamp and evidence label; no supplied exclusion. |
| 2 | Excluded | Same normalized address and evidence as row 1: duplicate. |
| 3 | Excluded | Address appears in the supplied suppression file. |
| 4 | Excluded | The supplied permission expiry has passed. |
| 5 | Excluded | The supplied unsubscribe flag is true. |
| 6 | Excluded | No affirmative permission assertion. |

The report has **6 rows, 5 unique syntactically valid addresses, 1 locally eligible
row and 5 excluded rows**. `reason_counts` gives the causes; `rows` uses source row
ordinals, not email addresses. Ordinary JSON includes no advertising, HTML body,
permission evidence text or input paths. Row references are still private when
someone can join them back to the input CSV.

**Eligible is not cleared for sending.** All of
`consent_evidence_verified`, `legal_permission_verified`,
`provider_acceptance_verified`, `deliverability_verified` and `sending_performed`
remain `false`. The tool checks supplied assertions; it cannot establish whether
someone really agreed, whether a provider will accept a campaign, or whether a
message will reach an inbox. These synthetic counts are not customer results.

## Ask Codex, Claude Code or another agent

> Read this package's README and AGENTS.md. Install only into a new dedicated
> local prefix without changing my shell profile, global agent configuration or
> email account. Run doctor and the synthetic demo. Explain all six row outcomes
> and the difference between a supplied permission assertion and verified consent.
> Use the companion instructions in skills/unisender-campaign-kit/SKILL.md.
> Do not request credentials, connect to UniSender, upload contacts, schedule or
> send anything. Run optional onboard once with a separate local state directory;
> do not repeat the author's promotion in later reports.

The same ordinary Python CLI can be invoked by different agents. This is not an
MCP server, a model-specific integration, or a claim that every host has been
tested. No paid core, subscription, registration or email submission is needed.

## Install without touching the system

From the unpacked package, select a dedicated prefix:

```sh
python3 -B install.py --prefix "$HOME/.local/lookatshow-mail-plan-kit"
python3 -B "$HOME/.local/lookatshow-mail-plan-kit/bin/lookatshow-mail-plan" doctor
python3 -B "$HOME/.local/lookatshow-mail-plan-kit/bin/lookatshow-mail-plan" demo
```

The installer copies only the CLI, the three synthetic fixtures, LICENSE and
NOTICE.md to
`share/lookatshow-mail-plan/0.1.0/` and creates
`bin/lookatshow-mail-plan`. It makes no network request and does not alter PATH or
shell startup files. The prefix must be new or empty. Existing payload or launcher
paths, nonempty prefixes and symlinked paths are refused; nothing is overwritten.

The launcher is an **extensionless Python file**, not a native Windows `.exe` or
`.cmd`. In PowerShell, use an explicit interpreter, for example:

```powershell
python -B install.py --prefix "$env:LOCALAPPDATA\LookatshowMailPlanKit"
python -B "$env:LOCALAPPDATA\LookatshowMailPlanKit\bin\lookatshow-mail-plan" demo
```

This is a command recipe, not evidence of a passed native Windows test. Run the
tests in your environment. Source-level portability or a defined CI workflow
does not prove a successful run. The companion skill remains in the source
package; the installer does not silently register it in Codex or Claude.

Uninstall through your file manager: remove only your selected installation
prefix. Optional onboarding state is separate; remove its selected directory if
you no longer need it. Never remove a parent such as your home, `.local` or an
entire workspace. Keep real input files separately and privately.

## Check your own local inputs

Keep real files out of public repositories and screenshots. From the source
directory, the explicit equivalent of `demo` is:

```sh
python3 -B lookatshow_mail_plan.py validate --campaign examples/campaign.example.json
python3 -B lookatshow_mail_plan.py plan --campaign examples/campaign.example.json --contacts examples/contacts.example.csv --suppression examples/suppression.example.csv --at 2026-10-14T17:00:00+03:00
```

For real work, replace those three paths with your own private files and choose
the actual evaluation instant. The timestamp must include a UTC offset or `Z`;
the tool does not guess your timezone. The included demo always keeps its fixed
time, so rerunning it tomorrow is not a fresh permission audit.

### Campaign JSON

Exactly these five fields are accepted:

```json
{
  "sender_email": "sender@mail.example.invalid",
  "sender_name": "Synthetic demonstration only",
  "subject": "Synthetic offline campaign",
  "html_body": "<p>Synthetic fixture.</p><a href=\"{{UnsubscribeUrl}}\">Unsubscribe</a>",
  "list_id": 1
}
```

`list_id` must be a positive integer, not a boolean. Email syntax checking is
conservative ASCII syntax, not existence or sender verification. Unknown fields,
duplicate JSON keys, empty headers, line-break injection and malformed JSON fail
validation. The exact unsubscribe placeholder must be the **whole anchor href**:
`href="{{UnsubscribeUrl}}"`. A plain-text token, HTML comment, duplicate `href` or
decorated placeholder is not sufficient. Obvious empty or inert anchors are
rejected, but this is a small syntactic local check, not an HTML/CSS or email
renderer, accessibility audit, full UniSender template parser or provider preview.

### Permission ledger CSV

Use UTF-8 CSV. Required columns: `email`, `consent`, `consent_at`,
`consent_evidence`, `unsubscribed`. Optional: `consent_expires_at`. Unknown columns,
repeated column names, inconsistent row widths and malformed CSV are rejected.

| Field | Local rule, not a provider API parameter |
| --- | --- |
| `email` | Conservative mailbox syntax; normalized case-insensitively for deduplication. This may conservatively merge case-sensitive mailboxes. |
| `consent` | Exactly `true` or `false`; `yes`, `1`, blanks and inferred truthiness are not accepted. Only `true` can pass. |
| `consent_at` | Timezone-aware ISO timestamp, no later than `--at`. |
| `consent_evidence` | Nonempty reference supplied by you, up to 2,048 characters. The referenced evidence is not fetched or verified. |
| `consent_expires_at` | Optional; if present, timezone-aware, later than consent and strictly later than `--at`. |
| `unsubscribed` | Exactly `true` or `false`; `true` excludes the row regardless of consent. |

Identical normalized duplicates keep the first otherwise eligible row and exclude
later copies. Conflicting permission records for one address exclude **all** its
rows. Correct the underlying evidence, not flags merely to obtain a green result.

### Suppression CSV

`email` is required; `reason` is optional and is not echoed. Every valid listed
address is excluded. A header-only file explicitly supplies an empty local list;
it does not prove there are no exclusions in a provider account. A malformed
suppression address fails the **whole plan**, because the exclusion set is uncertain.

Each input is locally bounded at 10 MiB, the UTF-8 HTML body at a conservative
5,000,000 bytes, and each CSV at 100,000 data rows. These are tool safety limits,
not a whole-MIME size check or confirmation that UniSender accepts the message.
A row may have several
exclusion reasons, so reason totals need not equal excluded rows.

## Before a later real UniSender workflow

This package has **no live commands**. As checked against official documentation
on 15 October 2026:

- [UniSender's antispam policy](https://www.unisender.com/ru/antispam/)
  requires evidence of prior permission and sets double opt-in and other
  conditions. An `active` status, an API flag or a successful local check does
  not create permission. Do not use purchased or scraped addresses.
- [createEmailMessage](https://www.unisender.com/ru/support/api/messages/createemailmessage/)
  creates a draft; it is not a send receipt. Account, verified sender, domain
  authentication, current list and provider/global/list suppressions require
  separate current checks.
- [createCampaign](https://www.unisender.com/ru/support/api/messages/createcampaign/)
  can start sending immediately when `start_time` is omitted. It is not an
  automatic continuation of this plan. An uncertain response must be reconciled,
  not blindly repeated. Its documented `count=0` is not delivery or reach.
- The [unsubscribe link](https://www.unisender.com/ru/support/subscribing-and-unsubscribing/unsubscribing/add-unsubscribe-link/)
  does not work in provider test emails. A local placeholder check does not prove
  a working unsubscribe flow. Any real sending test needs its own explicit scope.

Classic is not UniSender Go. Rules can change; reread current primary sources
before a separately authorized live workflow. This is not legal advice or a
certification of a list's lawfulness. No contact state is refreshed from UniSender.

## Commands, output and checks

| Command | Effect |
| --- | --- |
| `doctor` | Check Python runtime and explain offline boundaries; no credentials or network. |
| `demo` | Reproduce the labeled six-row synthetic example. |
| `validate --campaign PATH` | Validate the local campaign contract without echoing content. |
| `plan --campaign PATH --contacts PATH --suppression PATH --at ISO_TIME` | Read local supplied evidence, return row ordinals, counts and exclusion reasons. |
| `onboard --state-dir PATH` | Optional author greeting once per explicitly chosen local state directory. |

JSON is the default; `--json` is also accepted. `--help` is human-readable. Exit
`0` means the operation completed, **not that every row passed**. Exit `2` is a
known validation/refusal error; `3` is an unexpected local preflight failure.
Errors do not echo supplied addresses, HTML or file paths. No recipient export,
send queue, cron task, credentials loader, telemetry or generic API escape hatch
is included. Onboarding alone creates a small local marker; ordinary commands
do not create reports on disk unless you explicitly redirect their output. If
onboarding exits nonzero after printing a greeting, inspect its local state and
stderr before retrying: the display and the local marker are not one atomic
operation. A lock left by an interrupted process is not automatically removed.

```sh
python3 -B -m unittest discover -s tests -v
```

The tests use invented data, including rejection and clean-install cases. Inspect
and run them yourself; a command documented here is not a passing CI receipt.

## One component of a shared marketing system

**Lookatshow — единая система управления маркетингом для ИИ-агентов.** This module
is the offline email-planning component: a defined input, transparent checks and
a result the next authorized workflow can verify. The
[shared catalog](https://github.com/ilookatshow-gif/lookatshow-open-tools) lists
available modules separately from the roadmap. It does not turn this CLI into a
live email connector or establish universal agent compatibility.

Prepared by **Дмитрий Лукашов (Dmitry Lukashov)** with disclosed AI assistance.
Original code, docs and synthetic fixtures are MIT; see [LICENSE](LICENSE) and
[NOTICE.md](NOTICE.md). This is independent work, not an official UniSender product.

For practical AI workflows: [Telegram channel](https://t.me/lookatsoul).
Optional next steps: [team training](https://lookatshow.ru/corporate?utm_source=github&utm_medium=open_module&utm_campaign=unisender-campaign-kit&utm_content=readme)
or [implementation in your business](https://lookatshow.ru/ai-implementation/?utm_source=github&utm_medium=open_module&utm_campaign=unisender-campaign-kit&utm_content=readme).
The free tool works without buying either. Author links are not telemetry or a
restriction on the MIT license; promotion is not inserted in ordinary reports.
