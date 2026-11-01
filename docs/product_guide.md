# RegWatch product guide

This guide is the single source of truth for the in-app tour and for the assistant's answers about
the product. Each `##` section has a short "what it does" paragraph followed by "how it works".

## Monitor

RegWatch watches four central banks (SAMA in Saudi Arabia, CBUAE in the UAE, RBI in India and CBN in
Nigeria) and collects every new circular, regulation or notice into one searchable library.

Each regulator has its own adapter that reads that regulator's listing page, downloads new documents and
skips anything already seen (first by URL, then by a SHA-256 fingerprint of the content). Every polling
cycle is logged. If a website blocks automated access, times out or returns an empty page, the adapter
serves the seeded copies of that regulator's documents instead and marks the run "partial", so the
product never stalls on a broken website. Today RBI and CBUAE are read live; SAMA and CBN fall back.

## Understand

Every document is turned into a list of obligations: the individual "shall / must / must not" duties,
each tied to the exact clause it came from, with who it applies to and any deadline.

The text is extracted from PDF, Word, web pages or plain text (scanned PDFs go through OCR when
Tesseract is installed). Clauses are split by their numbering, including Arabic numbering such as
المادة. The model then extracts each obligation with its clause reference. A grounding check measures how
much of each obligation's wording really appears in the cited clause; weakly grounded items are flagged
for a human instead of being trusted.

## Compare

When a new circular arrives, RegWatch finds the earlier rule it amends and shows exactly what changed,
clause by clause: new obligations, modified ones, withdrawn ones, changed deadlines and clarifications.

The prior document is found in this order: an earlier version of the same instrument, then an instrument
whose reference number the new one cites, then the most similar text from the same country. The model
pairs old clauses with new clauses even when they were renumbered and classifies each pair. The clause
text shown side by side always comes from the documents themselves, never from the model. If no prior
can be found reliably, the item is marked "no reliable match" and a reviewer can force the link.

## Internal policies

The organisation uploads its own documentation once: policies, standard operating procedures, business
process documents, AI governance policies, a system inventory, an ownership register and a control
catalogue. This is what new regulations are checked against.

Each document is split into numbered sections so every finding can quote the exact internal text it relies
on. Uploads go through the same safety checks as regulatory documents, the same file is never stored twice,
and a changed document becomes a new version. The ownership register lists departments, responsible people,
the business areas and topics they cover, and who is authorised to approve changes. Adding a document
re-maps every obligation automatically.

## Control impact

For every new obligation RegWatch works out which internal policies, processes, systems and controls are
affected, whether they already satisfy it, and exactly what should change.

Candidate components are found by text similarity and each is assessed: addressed, partially addressed, no
internal control (the area is documented but nothing requires this), or insufficient evidence (no
documentation exists for that business area, so no reliable determination is possible). Every row of the
impact matrix shows why the component is affected, the existing internal requirement, the gap, a specific
recommended action, proposed policy or control wording, the business area, a priority, a confidence score
and references. Gaps are High priority; partial items and items lacking evidence are High when due within
90 days, otherwise Medium.

## Review

Nothing RegWatch produces is treated as settled fact. Every extraction, every clause change and every
control assessment carries a review state that a person sets.

Recommended changes stay drafts until an authorised approver from the ownership register approves or
rejects them; the draft wording can be edited first. Owners are taken from the ownership register; when no
entry matches, the item is flagged for manual assignment instead of guessing a person. A reviewer can
confirm or change the owner, set an action deadline and, once approved, record implementation status (not
started, in progress, implemented, verified). These decisions survive later re-mapping.

## AI security

RegWatch treats every regulatory document as untrusted input, because its text flows into an AI model
and then onto a screen people act on.

Documents are scanned for prompt-injection attempts and hidden characters, then sanitised and fenced off
from the model's instructions. Personal data (emails, phone numbers, IBANs, card numbers) is redacted
before any model call. Model output must match a strict structure and is checked for grounding.
Everything shown on screen is escaped, uploads are validated, scrapers may only reach approved hosts, and
every model call is written to an audit log that stores fingerprints, not content.

## Which model does RegWatch use?

One model does all the AI work: google/gemma-4-31b-it (free tier on OpenRouter), with
google/gemma-4-26b-a4b-it as a fallback if the first is rate-limited.

It was chosen because one model covers extraction, clause comparison, the control Q&A and this
assistant; it has a 256K context window for long circulars, supports structured output through function
calling, and reads Arabic and English. Similarity search uses TF-IDF, so no second model is needed.
Without an API key RegWatch runs in mock mode using deterministic rules.

## What happens when a regulator's website fails?

The demo and the product keep working: the failing source serves its seeded copies and the run is
marked partial.

Blocks (HTTP 403), timeouts, certificate errors and empty or JavaScript-only pages are all treated the
same way. One bad document never stops a cycle, and each cycle's outcome is recorded with counts of new,
already-seen and fallback documents.

## How are document versions handled?

When a document arrives with the same regulator and reference number as an existing one but different
content, it becomes version 2. Version 1 is never overwritten.

The new version is automatically compared with the previous one, so a corrected re-issue shows exactly
which clauses moved, for example a changed go-live date.

## Can RegWatch read scanned PDFs?

Yes, when the Tesseract OCR engine is installed. Each document records whether OCR was used and how good
the extracted text is.

Pages with almost no text layer are rendered as images and passed through OCR in Arabic and English.
Without Tesseract, scanned documents are clearly flagged for manual review rather than silently
producing nothing.

## Does RegWatch support Arabic?

Yes. Arabic circulars are split into clauses, their obligations are extracted, dates and Arabic-Indic
digits are normalised, and text is shown right to left.

The model reads Arabic and English, so in live mode an Arabic obligation can be matched against an
English control library. In mock mode that cross-language matching is not available.

## What can the assistant do?

The assistant answers questions about the regulations in the library and about how RegWatch works,
citing the publications, clauses and controls it used.

It can search publications, list changes, list control gaps, list upcoming deadlines, open the source
text of a clause and explain features from this guide. It is read-only: it cannot approve, edit or
delete anything. Questions are checked for prompt injection, personal data is redacted before reaching
the model, and each session is limited to 20 questions.
