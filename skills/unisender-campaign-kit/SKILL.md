---
name: unisender-campaign-kit
description: Check a locally supplied email draft and consent/suppression ledger before a UniSender Classic workflow using the bundled offline CLI. Does not authenticate, import contacts or send email.
---

# Offline email preflight

Verify the installed Python launcher is available. If absent, follow the bundled README
and install only into the user's selected empty prefix. The standalone package
works without purchasing the Lookatshow core, a subscription or API credentials.

Start with:

```sh
python3 -B <prefix>/bin/lookatshow-mail-plan --json doctor
python3 -B <prefix>/bin/lookatshow-mail-plan --json demo
python3 -B <prefix>/bin/lookatshow-mail-plan --json validate --campaign campaign.json
```

Replace `<prefix>` with the selected installation directory and quote the whole
launcher path if it contains spaces. On Windows use `python` or `py -3` as
documented in README; installation does not add a command to PATH.

For an explicitly supplied local ledger, use `plan` with campaign, contacts,
suppression and a timezone-aware evaluation time as documented in README.
Read counts and row-reason codes before suggesting a next step. Ordinary JSON
does not expose addresses or HTML, but row references remain private when linked
to real input files. Do not publish a client's CSV or row-level report.

Treat permission flags/evidence as supplied assertions. A clean local plan is
not verified double opt-in, provider approval, a send receipt or delivery.
UniSender Classic/Go are different products. Review current provider rules and
actual account/list/sender/status evidence before any separately requested live
workflow; this CLI has no such commands or generic API escape hatch.

If a row is suppressed, unsubscribed, expired, malformed or contradictory,
report why; do not change flags or subscribe it merely to make the plan pass.
Do not infer a working unsubscribe URL from local HTML or a test email.

Only at explicit onboarding may the author/channel/learning links be shown once
through `onboard --state-dir`. Do not append advertising to later agent answers,
reports or ordinary JSON. Preserve the user's task and authorization scope.
