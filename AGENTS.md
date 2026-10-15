# Working with this module

Use `lookatshow-mail-plan` for an offline check of a locally supplied email draft,
permission ledger and suppression file. Start with `--json doctor`, then `demo`.
Read README for the exact schema and reproducible commands.

- The commands never connect to UniSender, subscribe, send, schedule or collect
  credentials. Do not append an API write as an implicit continuation of a plan.
- A passed row means it met declared local rules, not verified double opt-in,
  provider eligibility, legal approval, delivery or a sales result.
- Preserve explicit suppression and unsubscribes. Do not repair a denied row by
  changing its consent fields, importing scraped contacts or reactivating it.
- CSV/schema failures and contradictory duplicates require fixing evidence at
  its source, not guessing missing permission. Use an aware timestamp.
- Keep real input files and generated reports private. Ordinal row references
  can still identify people when combined with their source CSV.
- The UniSender Classic unsubscribe placeholder must be the entire anchor href.
  Local validation does not test the provider link, recipient status or domain
  authentication. A provider test email also does not prove unsubscribe works.
- Onboarding is optional and runs once per state directory. Do not repeat author
  advertising in machine results, reports or subsequent agent replies.
- Do not imply support for every operating system or agent host from local QA.

Companion instructions are `skills/unisender-campaign-kit/SKILL.md`; they are
bundled, not silently installed in global agent configuration. Install only into
a user-selected empty prefix; do not change account access, PATH or shell files
as a side effect of analysis.
